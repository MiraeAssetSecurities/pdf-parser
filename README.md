# PDF Parser

PDF 문서에서 텍스트, 테이블, 이미지를 추출하고 AWS Bedrock 멀티모달 LLM으로 요약/엔티티를 생성하는 도구입니다.
단일 PDF 또는 폴더 내 PDF 일괄 병렬 파싱을 지원합니다.

## 주요 기능

- **PDF 파싱**: [Docling](https://github.com/DS4SD/docling) 기반 고품질 PDF 변환
- **요소 추출**: 텍스트, 테이블, 이미지 자동 분리 + 바운딩 박스 위치 정보
- **이미지 분류**: DocumentFigureClassifier(EfficientNet-B0)로 16가지 카테고리 자동 분류
- **AI 요약**: AWS Bedrock Claude로 페이지/이미지/테이블별 요약 + 엔티티 추출
- **테이블 분석**: TableFormer 모델로 셀 구조 분석 + LLM 카테고리 분류
- **일괄 처리**: 폴더 내 PDF를 `ProcessPoolExecutor`로 병렬 파싱
- **S3 연동**: S3에서 PDF 읽고 변환 결과를 S3에 저장 (대규모 배치 처리)
- **웹 UI**: Streamlit 기반 브라우저 인터페이스로 변환 및 결과 조회
- **HTML 메타데이터**: 파싱 결과를 구조화된 HTML 테이블 태그로 출력 (후속 파싱 용이)

## 프로젝트 구조

```
pdf-parser/
├── run.py                        # CLI 진입점 (로컬 PDF / 폴더 일괄 처리)
├── run_s3.py                     # S3 CLI (S3 PDF → S3 결과)
├── app.py                        # Streamlit 웹 UI
├── src/
│   ├── __init__.py
│   ├── utils.py                  # bbox/위치 정보 헬퍼
│   ├── converter.py              # DoclingConverter, ParsedDocument
│   ├── summarizer.py             # BedrockSummarizer (병렬 LLM 요약)
│   ├── markdown_builder.py       # MarkdownBuilder (HTML 메타 테이블 + 마크다운 조립)
│   └── s3_handler.py             # S3Handler (S3 읽기/쓰기)
├── pdf_parser_docling.ipynb      # 인터랙티브 노트북 (단계별 실행/시각화)
├── sample.pdf                    # 샘플 PDF
├── pdfs/                         # 일괄 처리용 PDF 폴더
├── pyproject.toml                # uv 프로젝트 설정
├── CLAUDE.md                     # Claude Code 가이드
└── README.md
```

## 설치

```bash
uv sync
```

> **Apple Silicon**: DocumentFigureClassifier는 EfficientNet-B0 기반으로 CPU에서도 빠르게 동작합니다.

## 환경 설정

AWS Bedrock 접근을 위한 자격 증명이 필요합니다:

```bash
# AWS CLI 설정
aws configure

# 또는 환경 변수
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

## 사용법

### CLI - 로컬 처리 (run.py)

```bash
# 단일 PDF 파싱
uv run python run.py sample.pdf -o output

# 폴더 내 PDF 일괄 병렬 처리
uv run python run.py ./pdfs/ -o output --workers 4

# LLM 요약 없이 Docling 추출만
uv run python run.py sample.pdf -o output --no-summary

# 빠른 테이블 모드 (단순 테이블에 적합)
uv run python run.py sample.pdf -o output --table-mode fast

# Bedrock 모델 변경 (서울 리전)
uv run python run.py sample.pdf -o output --model-id ap-northeast-2.anthropic.claude-3-5-sonnet-20241022-v2:0

# 상세 로그 출력
uv run python run.py sample.pdf -o output -v
```

### CLI - S3 처리 (run_s3.py)

```bash
# S3 단일 PDF → S3 결과
uv run python run_s3.py s3://my-bucket/input/sample.pdf s3://my-bucket/output/

# S3 폴더 일괄 병렬 처리
uv run python run_s3.py s3://my-bucket/pdfs/ s3://my-bucket/output/ --workers 4

# 로컬 임시 디렉토리 지정
uv run python run_s3.py s3://bucket/input.pdf s3://bucket/output/ --temp-dir /tmp/pdf-parser

# 추가 옵션 (로컬 CLI와 동일)
uv run python run_s3.py s3://bucket/input.pdf s3://bucket/output/ --no-summary --table-mode fast -v
```

### FastAPI 서버 (프로덕션 API)

```bash
# 단일 워커 (개발용)
uv run uvicorn api:app --host 0.0.0.0 --port 3000

# 멀티 워커 (프로덕션, 동시 요청 처리)
uv run uvicorn api:app --host 0.0.0.0 --port 3000 --workers 2

# API 문서: http://localhost:3000/docs
# Health check: http://localhost:3000/health
```

**API 엔드포인트:**
- `POST /process`: 전체 파이프라인 (OCR + LLM 요약 + 마크다운)
- `POST /ocr`: OCR 전용 처리 (바운딩 박스 시각화 포함)
- `GET /health`: 서버 상태 확인

**성능 최적화:**
- `--workers 2`: c7i.large의 2코어를 활용하여 동시 요청 처리
- 워커 수 = CPU 코어 수 권장

### 웹 UI (app.py)

```bash
# Streamlit 웹 앱 실행
uv run streamlit run app.py

# 브라우저에서 http://localhost:8501 접속
```

**웹 UI 기능:**
- S3 PDF 목록 조회 및 변환 실행
- 로컬 PDF 업로드 및 결과 다운로드
- S3 결과 조회 및 미리보기

### JupyterLab (대화형 개발 환경)

```bash
# 포그라운드 실행
uv run jupyter lab --ip=0.0.0.0 --port=8000 --no-browser

# 백그라운드 실행 (방법 1: nohup)
nohup uv run jupyter lab --ip=0.0.0.0 --port=8000 --no-browser > jupyter.log 2>&1 &

# 백그라운드 실행 (방법 2: tmux, 권장)
tmux new -s jupyter
uv run jupyter lab --ip=0.0.0.0 --port=8000 --no-browser
# Ctrl+B, D로 세션 분리 / tmux attach -t jupyter로 재접속

# 백그라운드 실행 (방법 3: systemd 서비스, 영구 실행)
sudo cp jupyter.service.example /etc/systemd/system/jupyter.service
sudo systemctl daemon-reload
sudo systemctl start jupyter
sudo systemctl enable jupyter  # 부팅 시 자동 시작
sudo systemctl status jupyter  # 상태 확인
sudo journalctl -u jupyter -f  # 로그 확인

# SSH 포트 포워딩
# ssh -L 8000:localhost:8000 user@server
```

**주요 노트북:**
- **pdf_parser_docling.ipynb**: 핵심 파이프라인 탐색 및 테스트
- **api_test.ipynb**: FastAPI 서버 테스트 및 S3 파일 브라우저
- **ocr_visualizer.ipynb**: OCR 전용 시각화 도구

### Python 코드에서 직접 사용

자세한 API 사용법은 `CLAUDE.md` 파일을 참고하세요.

**로컬 처리:**
```python
from src.converter import DoclingConverter
from src.summarizer import BedrockSummarizer
from src.markdown_builder import MarkdownBuilder

converter = DoclingConverter(table_mode="accurate")
parsed = converter.convert("sample.pdf")
parsed.save_assets(output_dir)

summarizer = BedrockSummarizer()
page_summaries = summarizer.summarize_pages(parsed)
image_summaries = summarizer.summarize_figures(parsed, page_summaries)
table_summaries = summarizer.summarize_tables(parsed, page_summaries)

builder = MarkdownBuilder(parsed, output_dir)
final_md = builder.build(page_summaries, image_summaries, table_summaries)
```

**S3 연동:**
```python
from src.s3_handler import S3Handler

s3 = S3Handler(region_name="us-east-1")
s3.download_pdf("s3://bucket/input/file.pdf", Path("/tmp/file.pdf"))
s3.upload_directory(Path("output/sample"), "s3://bucket/output/sample/")
pdf_uris = s3.list_pdfs("s3://bucket/pdfs/")
```

**FastAPI 클라이언트:**
```python
import requests

response = requests.post(
    "http://localhost:3000/process",
    json={
        "inputPath": "s3://bucket/input/sample.pdf",
        "outputPath": "s3://bucket/output/",
        "tableMode": "accurate",
    }
)
result = response.json()
print(result['finalMarkdownUri'])
```

## 출력 구조

```
output/{pdf_name}/
├── {pdf_name}_text.md          # Docling 원본 마크다운 (순수 텍스트)
├── {pdf_name}_final.md         # 메타데이터 포함 최종 마크다운
├── table/
│   ├── img/                    # 테이블 영역 이미지 (PNG)
│   └── md/                     # 테이블 마크다운
└── pictures/
    ├── bar_chart/              # 카테고리별 분류된 이미지
    ├── flow_chart/
    ├── line_chart/
    └── ...
```

## 출력 마크다운 형식

최종 마크다운(`_final.md`)은 구조화된 HTML 메타데이터 테이블을 포함합니다:

**페이지 메타데이터** (`<table class="page-meta">`):
- `page_number`, `page_summary`, `entities`

**이미지 메타데이터** (`<table class="figure-meta">`):
- `image_id`, `category`, `page_number`, `image_summary`, `entities`, `bbox`, `img_source`

**테이블 메타데이터** (`<table class="table-meta">`):
- `table_id`, `category`, `page_number`, `table_summary`, `entities`, `bbox`, `img_source`

## 이미지 분류 카테고리 (16종)

`bar_chart` · `bar_code` · `chemistry_markush_structure` · `chemistry_molecular_structure` · `flow_chart` · `icon` · `line_chart` · `logo` · `map` · `other` · `pie_chart` · `qr_code` · `remote_sensing` · `screenshot` · `signature` · `stamp`

## 테이블 카테고리 (LLM 분류)

`financial_statement` · `comparison` · `statistics` · `performance_metrics` · `configuration` · `schedule` · `pricing` · `inventory` · `survey_results` · `reference` · `other`

## 요구사항

- Python 3.12+
- AWS 계정 (Bedrock Claude 모델 액세스)
- macOS / Linux (Windows는 WSL 권장)

## 라이선스

MIT
