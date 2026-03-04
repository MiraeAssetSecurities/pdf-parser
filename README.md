# PDF Parser

PDF 문서에서 텍스트, 테이블, 이미지를 추출하고 AI 기반 요약을 생성하는 도구입니다.

## 주요 기능

- **PDF 파싱**: Docling을 사용한 고품질 PDF 파싱
- **요소 추출**: 텍스트, 테이블, 이미지 자동 분리
- **이미지 분류**: 16가지 카테고리 자동 분류 (차트, 다이어그램 등)
- **AI 요약**: AWS Bedrock Claude를 활용한 페이지/이미지/테이블 요약
- **바운딩 박스**: 모든 요소의 위치 정보 추출

## 설치

```bash
# uv 사용 (권장)
uv sync

# 또는 pip 사용
pip install -e .
```

## 환경 설정

`.env` 파일 생성:

```bash
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=us-east-1
```

## 사용법

### 1. PDF 파싱 (Jupyter Notebook)

```bash
# Jupyter 실행
jupyter notebook pdf_parser_docling.ipynb
```

노트북에서 다음 작업 수행:
- PDF 파일 로드 (`sample.pdf`)
- 텍스트/테이블/이미지 추출
- 바운딩 박스 시각화
- `output/` 디렉토리에 결과 저장
  - `*_text.md`: 순수 텍스트
  - `*_final.md`: 텍스트 + 이미지/테이블 참조
  - `table/`: 테이블 이미지 및 마크다운
  - `pictures/`: 분류된 이미지
  - `bbox/`: 바운딩 박스 시각화

### 2. AI 요약 생성

```bash
# 기본 사용 (output/*_final.md 자동 탐색)
python summarizer.py

# 특정 파일 지정
python summarizer.py output/sample_final.md
```

**출력 파일**:
- `*_summary.md`: 페이지별 요약 모음
- `*_enriched.md`: 원본에 요약 태그 삽입

**요약 내용**:
- 페이지 요약 (3-5문장)
- 이미지 설명 (2-3문장)
- 테이블 분석 (2-3문장)

## 프로젝트 구조

```
pdf-parser/
├── pdf_parser_docling.ipynb  # PDF 파싱 노트북
├── summarizer.py              # AI 요약 생성기
├── sample.pdf                 # 샘플 PDF
├── pyproject.toml
└── README.md
```

## 기술 스택

- **PDF 파싱**: [Docling](https://github.com/DS4SD/docling)
- **AI 모델**: AWS Bedrock Claude Sonnet 4
- **비동기 처리**: Strands Agents
- **이미지 분류**: DocumentFigureClassifier (EfficientNet-B0)

## 이미지 분류 카테고리

`bar_chart`, `bar_code`, `chemistry_markush_structure`, `chemistry_molecular_structure`,  
`flow_chart`, `icon`, `line_chart`, `logo`, `map`, `other`,  
`pie_chart`, `qr_code`, `remote_sensing`, `screenshot`, `signature`, `stamp`

## 요구사항

- Python 3.12+
- AWS 계정 (Bedrock 액세스)
- macOS/Linux (Windows는 WSL 권장)

## 라이선스

MIT
