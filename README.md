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
- **HTML 메타데이터**: 파싱 결과를 구조화된 HTML 테이블 태그로 출력 (후속 파싱 용이)

## 프로젝트 구조

```
pdf-parser/
├── run.py                        # CLI 진입점 (단일 PDF / 폴더 일괄 처리)
├── src/
│   ├── __init__.py
│   ├── utils.py                  # bbox/위치 정보 헬퍼
│   ├── converter.py              # DoclingConverter, ParsedDocument
│   ├── summarizer.py             # BedrockSummarizer (병렬 LLM 요약)
│   └── markdown_builder.py       # MarkdownBuilder (HTML 메타 테이블 + 마크다운 조립)
├── pdf_parser_docling.ipynb      # 인터랙티브 노트북 (단계별 실행/시각화)
├── sample.pdf                    # 샘플 PDF
├── pdfs/                         # 일괄 처리용 PDF 폴더
├── pyproject.toml                # uv 프로젝트 설정
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

### CLI (run.py)

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

### Jupyter Notebook

```bash
uv run jupyter notebook pdf_parser_docling.ipynb
```

노트북에서 단계별로 실행하며 중간 결과를 시각화할 수 있습니다.

### Python 코드에서 직접 사용

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
