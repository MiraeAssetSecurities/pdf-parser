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

# Bedrock 모델 변경
uv run python run.py sample.pdf -o output --model-id us.anthropic.claude-3-5-sonnet-20241022-v2:0

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

### 웹 UI (app.py)

```bash
# Streamlit 웹 앱 실행
uv run streamlit run app.py

# 브라우저에서 http://localhost:8501 접속
```

**웹 UI 기능:**
- **S3 → S3 변환**: S3 PDF 목록 조회 및 변환 실행
- **로컬 변환**: PDF 파일 업로드하여 변환, 결과 다운로드
- **결과 조회**: S3에 저장된 마크다운 조회 및 미리보기

### JupyterLab (대화형 개발 환경)

```bash
# JupyterLab 서버 시작 (포트 8000)
uv run jupyter lab --ip=0.0.0.0 --port=8000 --no-browser

# 출력되는 토큰을 사용하여 브라우저에서 접속:
# http://localhost:8000/?token=<token>

# SSH 포트 포워딩이 필요한 경우:
# ssh -L 8000:localhost:8000 user@server
```

**JupyterLab 기능:**
- **pdf_parser_docling.ipynb**: 핵심 파이프라인 탐색 및 테스트
  - 단계별 PDF 처리 (변환 → 추출 → 요약 → 마크다운 생성)
  - 바운딩 박스 시각화 (그림: 초록, 테이블: 빨강)
  - 인라인 결과 미리보기 및 파라미터 튜닝
- **api_test.ipynb**: FastAPI 서버 테스트
  - API 엔드포인트 테스트 및 성능 벤치마크
  - 배치 처리 시뮬레이션
  - **S3 파일 브라우저**: 인터랙티브 S3 탐색 위젯
    - 폴더 클릭 네비게이션
    - PDF 선택 및 자동 처리
    - 실시간 경로 편집
- **ocr_visualizer.ipynb**: OCR 전용 시각화 도구
  - S3 브라우저로 PDF 선택
  - OCR API 호출 및 결과 시각화
  - 바운딩 박스 이미지 인라인 표시
  - 추출 텍스트 마크다운 미리보기

### Python 코드에서 직접 사용

**로컬 처리:**
```python
from src.converter import DoclingConverter
from src.summarizer import BedrockSummarizer
from src.markdown_builder import MarkdownBuilder
from pathlib import Path

# 1) PDF 변환
converter = DoclingConverter(table_mode="accurate")
parsed = converter.convert("sample.pdf")

# 2) 에셋 저장
output_dir = Path("output/sample")
output_dir.mkdir(parents=True, exist_ok=True)
parsed.save_assets(output_dir)

# 3) LLM 요약 (선택)
summarizer = BedrockSummarizer(model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0")
page_summaries = summarizer.summarize_pages(parsed)
image_summaries = summarizer.summarize_figures(parsed, page_summaries)
table_summaries = summarizer.summarize_tables(parsed, page_summaries)

# 4) 최종 마크다운 생성
builder = MarkdownBuilder(parsed, output_dir)
final_md = builder.build(page_summaries, image_summaries, table_summaries)
Path("output/sample/sample_final.md").write_text(final_md, encoding="utf-8")
```

**S3 연동:**
```python
from src.s3_handler import S3Handler
from pathlib import Path

s3 = S3Handler(region_name="us-east-1")

# S3에서 PDF 다운로드
s3.download_pdf("s3://bucket/input/file.pdf", Path("/tmp/file.pdf"))

# 로컬 처리 후 결과를 S3에 업로드
s3.upload_directory(Path("output/sample"), "s3://bucket/output/sample/")

# S3 폴더의 PDF 목록 조회
pdf_uris = s3.list_pdfs("s3://bucket/pdfs/")

# S3에서 마크다운 읽기
content = s3.read_markdown("s3://bucket/output/sample/sample_final.md")
```

**S3 파일 브라우저 (JupyterLab 전용):**
```python
from src.s3_browser import create_s3_browser

# 인터랙티브 S3 브라우저 생성
browser = create_s3_browser(initial_path="s3://my-bucket/pdfs/")

# 선택된 PDF 가져오기
selected_pdf = browser.get_selected()  # 사용자가 PDF 클릭 후

# 콜백 함수와 함께 사용
def on_select(s3_uri):
    print(f"선택됨: {s3_uri}")
    # 자동으로 처리 시작

browser = create_s3_browser(
    initial_path="s3://my-bucket/",
    on_select=on_select
)
```

**FastAPI 클라이언트:**
```python
import requests

# 전체 파이프라인 (LLM 요약 포함)
response = requests.post(
    "http://localhost:3000/process",
    json={
        "inputPath": "s3://my-bucket/input/sample.pdf",
        "outputPath": "s3://my-bucket/output/",
        "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "noSummary": False,
        "tableMode": "accurate",
    }
)

result = response.json()
print(f"Final markdown: {result['finalMarkdownUri']}")

# OCR 전용 (바운딩 박스 시각화 포함)
response = requests.post(
    "http://localhost:3000/ocr",
    json={
        "inputPath": "s3://my-bucket/input/sample.pdf",
        "outputPath": "s3://my-bucket/output/",
        "tableMode": "accurate",
        "generateBboxImages": True,
    }
)

result = response.json()
print(f"Text markdown: {result['textMarkdownUri']}")
print(f"Bbox images: {len(result['bboxImagesUris'])} pages")
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
