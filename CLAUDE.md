# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PDF parser that extracts text, tables, and images from PDFs using Docling, generates AI summaries and entity extraction using AWS Bedrock multimodal LLMs. Supports four modes:
1. **Local processing**: Process local PDFs, output to local filesystem
2. **S3 processing**: Read PDFs from S3, write results to S3 (batch parallel processing)
3. **FastAPI Server**: RESTful API for S3-based PDF processing (production-ready)
4. **Interactive UI**: JupyterLab notebook interface for interactive development and exploration

## Essential Commands

### Setup
```bash
# Install dependencies
uv sync

# Configure AWS credentials (required for Bedrock and S3)
aws configure
# Or set environment variables:
# export AWS_ACCESS_KEY_ID=your_key
# export AWS_SECRET_ACCESS_KEY=your_secret
# export AWS_DEFAULT_REGION=us-east-1
```

### Local Processing (run.py)

```bash
# Single PDF with AI summaries
uv run python run.py sample.pdf -o output

# Batch process folder with 4 parallel workers
uv run python run.py ./pdfs/ -o output --workers 4

# Skip LLM summaries (Docling extraction only)
uv run python run.py sample.pdf -o output --no-summary

# Fast table mode (simpler tables)
uv run python run.py sample.pdf -o output --table-mode fast

# Change Bedrock model
uv run python run.py sample.pdf -o output --model-id us.anthropic.claude-3-5-sonnet-20241022-v2:0

# Verbose logging
uv run python run.py sample.pdf -o output -v
```

### S3 Processing (run_s3.py)

```bash
# Single PDF from S3 → results to S3
uv run python run_s3.py s3://bucket/input/sample.pdf s3://bucket/output/

# Batch process S3 folder with parallel workers
uv run python run_s3.py s3://bucket/pdfs/ s3://bucket/output/ --workers 4

# Custom temp directory
uv run python run_s3.py s3://bucket/input.pdf s3://bucket/output/ --temp-dir /tmp/pdf-parser

# No summaries, fast mode
uv run python run_s3.py s3://bucket/input.pdf s3://bucket/output/ --no-summary --table-mode fast
```

### FastAPI Server (Production API)

```bash
# Start FastAPI server on port 3000
uv run uvicorn api:app --host 0.0.0.0 --port 3000

# Access API docs at http://localhost:3000/docs
# Health check: http://localhost:3000/health

# Full pipeline with AI summaries
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "inputPath": "s3://my-bucket/input/sample.pdf",
    "outputPath": "s3://my-bucket/output/",
    "modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "noSummary": false,
    "tableMode": "accurate"
  }'

# OCR only with bbox visualization
curl -X POST http://localhost:3000/ocr \
  -H "Content-Type: application/json" \
  -d '{
    "inputPath": "s3://my-bucket/input/sample.pdf",
    "outputPath": "s3://my-bucket/output/",
    "tableMode": "accurate",
    "generateBboxImages": true
  }'
```

### JupyterLab (Interactive Development)

```bash
# Start JupyterLab server on port 8000
uv run jupyter lab --ip=0.0.0.0 --port=8000 --no-browser

# Access at http://localhost:8000 (use token from terminal output)
# Or with SSH port forwarding:
# ssh -L 8000:localhost:8000 user@server

# Available notebooks:
# - pdf_parser_docling.ipynb: Core pipeline exploration and testing
# - api_test.ipynb: FastAPI server testing and usage examples
#   Includes interactive S3 file browser widget for easy PDF selection
```

## Architecture

### Processing Modes

**1. Local Mode (run.py)**
- Input: Local PDF files or folder
- Output: Local filesystem (output/{pdf_name}/)
- Use case: Development, testing, small batches

**2. S3 Mode (run_s3.py)**
- Input: S3 URIs (s3://bucket/path/file.pdf or s3://bucket/folder/)
- Output: S3 URIs (s3://bucket/output/)
- Workflow: Download PDF → Process locally in temp → Upload all results
- Use case: Production pipelines, large-scale batch processing
- Temp files automatically cleaned after processing

**3. FastAPI Mode (api.py)**
- RESTful API server with OpenAPI documentation (Swagger UI)
- Endpoints:
  - `POST /process`: Full pipeline (OCR + LLM summaries + markdown assembly)
  - `POST /ocr`: OCR-only processing with bbox visualization
  - `GET /health`: Health check endpoint
- Request format: JSON with `inputPath`, `outputPath`, optional parameters
- Response: JSON with processing results, stats, and S3 URIs
- Workflow: Same as S3 mode (download → process → upload)
- Use case: Microservice integration, webhooks, external API consumers
- Port: 3000 (configurable)
- Features:
  - Automatic API documentation at `/docs`
  - Input validation with Pydantic
  - Comprehensive error handling
  - Temp file cleanup in finally blocks
  - `/ocr` endpoint skips LLM calls for faster processing

**4. Interactive Mode (JupyterLab)**
- Notebook-based interface (`pdf_parser_docling.ipynb`) for exploration and development
- Features:
  - Interactive PDF processing with immediate visualization
  - Bounding box visualization for OCR validation (figures in green, tables in red)
  - Step-by-step pipeline execution with inline outputs
  - Easy parameter tuning and experimentation
  - Rich display of images, tables, and results
  - Access to all core pipeline components programmatically
- Use case: Development, debugging, research, one-off explorations

### Core 4-Stage Pipeline

All modes use the same processing pipeline:

**1. PDF Conversion (`src/converter.py`)**
- `DoclingConverter`: Configures Docling pipeline options
  - `image_scale`: Image resolution multiplier (default 2.0)
  - `table_mode`: "accurate" (TableFormerMode.ACCURATE) or "fast" (TableFormerMode.FAST)
  - Enables: page images, picture images, table images, picture classification, table structure analysis
- `ParsedDocument`: Wraps Docling Document
  - `get_figures()`: Returns (1-based_index, PictureItem, category), excludes logos
  - `get_tables()`: Returns (1-based_index, TableItem)
  - `save_assets()`: Saves table md/images and figure images to disk
- TableFormer model analyzes table cell structure with cell matching
- DocumentFigureClassifier (EfficientNet-B0) categorizes images into 16 types

**2. Asset Extraction (`src/converter.py`)**
- Tables: Markdown + PNG saved to `table/md/` and `table/img/`
- Figures: PNG saved to `pictures/{category}/` (16 categories)
- Logo figures filtered out (classified but not saved to final output)
- Naming: `{doc_name}_table_{idx}.md`, `{doc_name}_picture_{idx}.png` (1-based)

**3. AI Summarization (`src/summarizer.py`)**
- `BedrockSummarizer`: Parallel LLM calls via AWS Bedrock
  - `_call_vision()`: PIL image → base64 → Bedrock API → JSON response
  - `max_workers=10` for ThreadPoolExecutor (default)
  - Handles JSON extraction from markdown code fences
- Three sequential passes with page context propagation:
  - `summarize_pages()`: Page images → {summary, entities}
  - `summarize_figures()`: Figure images + page context → {summary, entities}
  - `summarize_tables()`: Table images + page context → {summary, entities, category}
- Each subsequent pass receives page summary as context for accuracy
- Korean language summaries (explicitly requested in prompts)
- Graceful error handling: failed summaries return error message, processing continues

**4. Markdown Assembly (`src/markdown_builder.py`)**
- `MarkdownBuilder.build()`: Generates final structured markdown
- Embeds HTML metadata tables before each element:
  - `<table class="page-meta">`: page_number, page_summary, entities
  - `<table class="figure-meta">`: image_id, category, page_number, image_summary, entities, bbox, img_source
  - `<table class="table-meta">`: table_id, category, page_number, table_summary, entities, bbox, img_source
- Uses regex to inject metadata while preserving Docling's markdown structure
- Removes logo figure metadata blocks from final output
- Page wrapper: `<page-NNN>...</page-NNN>` tags

### S3 Integration (`src/s3_handler.py`)

**S3Handler class**:
- `parse_s3_uri()`: Validates and parses s3://bucket/key format
- `download_pdf()`: S3 → local file
- `upload_file()`: Local file → S3
- `upload_directory()`: Recursively uploads directory preserving structure
- `list_pdfs()`: Lists all .pdf files in S3 prefix (handles pagination)
- `list_folders()`: Lists folders (common prefixes) in S3 path, returns folder names
- `browse_path()`: Returns folders and PDFs in current S3 directory (file explorer)
  - Returns: `{"folders": [...], "pdfs": [...]}`
  - Only lists items in current directory (no subdirectories)
- `download_directory()`: Downloads entire S3 directory to local, preserving structure
- `read_markdown()`: Reads text content from S3 markdown file
- Uses boto3 client with optional region configuration
- All methods have comprehensive error logging

**S3 URI Patterns**:
- Input PDF: `s3://bucket/input/file.pdf`
- Input folder: `s3://bucket/input/pdfs/`
- Output base: `s3://bucket/output/`
- Result structure: `s3://bucket/output/{pdf_stem}/{pdf_stem}_final.md`

### S3 Browser Widget (`src/s3_browser.py`)

**S3Browser class (JupyterLab only)**:
- Interactive file browser widget using `ipywidgets`
- Visual navigation through S3 buckets and folders
- Click-based interface for selecting PDFs
- Built on top of `S3Handler.browse_path()`

**Key methods**:
- `get_selected()`: Returns currently selected PDF S3 URI (or None)
- `display()`: Renders the widget in JupyterLab
- `_refresh_display()`: Reloads current directory contents

**UI Components**:
- Path input field with "Go" button (manual navigation)
- "↑ Parent" button (go up one directory level)
- "↻ Refresh" button (reload current view)
- Folder buttons (📁) - click to navigate into folder
- PDF buttons (📄) - click to select file
- Selected file display at bottom

**Usage patterns**:
- Create with `create_s3_browser(initial_path, on_select)`
- Optional callback fires when PDF is clicked
- Retrieve selection with `browser.get_selected()`
- Use in notebooks for interactive S3 exploration

### Parallel Processing Strategy

**Multi-PDF Processing (run.py, run_s3.py)**:
- `ProcessPoolExecutor` with configurable workers
- Each worker process gets independent logging setup
- S3 mode: Each worker gets isolated temp directory (worker_N/)
- Temp directories cleaned in finally blocks

**LLM Parallelization (src/summarizer.py)**:
- `ThreadPoolExecutor` within each PDF processing
- Parallel API calls to Bedrock (max 10 concurrent by default)
- Three sequential stages (pages → figures → tables)
- Each stage parallelizes its own LLM calls

### Utilities (`src/utils.py`)

**Location & Bbox**:
- `get_location()`: Extracts bbox coordinates in both BOTTOMLEFT (raw) and TOPLEFT origins
  - Returns: page_no, page_w, page_h, bbox_raw, bbox_tl
- `get_bbox_str()`: Formats bbox as "l=X t=Y r=Z b=W" string
- `get_figure_category()`: Retrieves predicted class name from Docling annotations

**Visualization**:
- `draw_bboxes_on_page()`: Draws bounding boxes on page image with labels
  - Converts pt coordinates to pixel coordinates using actual image scale
  - Color-coded: green (figures), red (tables)
  - Returns OpenCV BGR numpy array
- `generate_bbox_images()`: Generates bbox visualization for all pages
  - Collects all figures and tables by page
  - Returns {page_no: jpg_bytes} dictionary for display in JupyterLab or web UI

## Output Structure

### Local/S3 Output
```
output/{pdf_name}/
├── {pdf_name}_text.md          # Raw Docling markdown (text only, no metadata)
├── {pdf_name}_final.md         # Final markdown with HTML metadata tables
├── table/
│   ├── img/{pdf_name}_table_N.png    # Table region images
│   └── md/{pdf_name}_table_N.md      # Table markdown (Docling export)
└── pictures/
    ├── bar_chart/{pdf_name}_picture_N.png
    ├── flow_chart/{pdf_name}_picture_N.png
    └── ... (16 categories)
```

### Metadata Format

All metadata embedded as HTML tables for easy parsing:

```html
<table class="page-meta">
  <tr><td>page_number</td><td>1</td></tr>
  <tr><td>page_summary</td><td>페이지 요약 내용...</td></tr>
  <tr><td>entities</td><td>entity1, entity2, ...</td></tr>
</table>
```

Similar structure for `figure-meta` and `table-meta` with additional fields.

## Key Conventions

- **1-based indexing**: Figures and tables use 1-based indices (`figure-001`, `table-001`)
- **Korean summaries**: LLM prompts explicitly request Korean language output
- **Logo filtering**: Logos classified but excluded from final markdown
- **Bbox coordinates**: Provided in TOPLEFT origin for metadata (Docling uses BOTTOMLEFT internally)
- **HTML metadata**: Use `class` attributes for parsing (page-meta, figure-meta, table-meta)
- **Page context**: Later summarization stages receive earlier summaries as context
- **S3 temp cleanup**: Always clean temp files in finally blocks
- **Error resilience**: Failed LLM calls don't stop processing, return error messages

## Classification Categories

### Image Categories (16)
DocumentFigureClassifier (EfficientNet-B0) predicts:
`bar_chart`, `bar_code`, `chemistry_markush_structure`, `chemistry_molecular_structure`, `flow_chart`, `icon`, `line_chart`, `logo`, `map`, `other`, `pie_chart`, `qr_code`, `remote_sensing`, `screenshot`, `signature`, `stamp`

### Table Categories (11)
LLM-classified via prompt:
`financial_statement`, `comparison`, `statistics`, `performance_metrics`, `configuration`, `schedule`, `pricing`, `inventory`, `survey_results`, `reference`, `other`

## Bedrock Configuration

**Default model**: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (fast, cost-effective)

**Available models**:
- Haiku 4.5: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- Sonnet 3.5: `us.anthropic.claude-3-5-sonnet-20241022-v2:0`

**API format**:
- Version: `bedrock-2023-05-31`
- Image encoding: base64 PNG
- Max tokens: 1024
- Expects JSON response with summary/entities/category fields

**Prompt strategies**:
- `SUMMARY_PROMPT`: Generic image/page summarization
- `TABLE_SUMMARY_PROMPT`: Table-specific with category classification
- Page context appended for figure/table prompts

## JupyterLab Features (pdf_parser_docling.ipynb)

**Interactive Notebook Interface**:
- Step-by-step pipeline execution with rich outputs
- Inline visualization of processing results
- Direct access to all core components (converter, summarizer, builder)
- Configurable parameters in code cells

**Key Features**:
- **Bounding Box Visualization**:
  - Draw and display bboxes on page images
  - Color-coded: green (figures), red (tables)
  - Validate OCR detection accuracy visually
- **Asset Preview**:
  - Display extracted tables inline
  - Show figure images with categories
  - Preview markdown outputs in cells
- **Parameter Tuning**:
  - Adjust `image_scale`, `table_mode` interactively
  - Switch Bedrock models on the fly
  - Toggle LLM summary generation
- **Debugging**:
  - Inspect intermediate results at each pipeline stage
  - Print parsed document structure
  - Examine individual figure/table metadata

**Notebook Variables**:
- `OUTPUT_DIR`: Base output directory (default: `./output`)
- `IMAGE_SCALE`: Resolution multiplier for images (default: 2.0)
- `BBOX_DIR`, `TABLE_IMG_DIR`, `TABLE_MD_DIR`: Asset output paths
- Access to `DocumentConverter`, `ParsedDocument`, `BedrockSummarizer`, `MarkdownBuilder`

**Use Cases**:
- Rapid prototyping and testing new features
- Visual debugging of extraction accuracy
- Parameter optimization for specific PDF types
- One-off document analysis and exploration
- Educational demonstrations of the pipeline

## API Test Notebook (api_test.ipynb)

**FastAPI Testing Interface**:
- Comprehensive API testing and usage examples
- Interactive exploration of REST endpoints
- Performance benchmarking and comparison

**Key Features**:
- **S3 File Browser**: Interactive widget for browsing S3 buckets and selecting PDFs
  - Folder navigation with clickable buttons
  - PDF selection with visual feedback
  - Path editing and parent directory navigation
  - Callback support for automatic processing on selection
- **Health Check**: Verify API server status
- **Schema Inspection**: Explore OpenAPI documentation
- **Single PDF Processing**: Test `/process` endpoint (full pipeline)
- **Batch Processing**: Process multiple PDFs sequentially
- **Result Validation**: Download and inspect S3 results
- **Error Handling**: Test invalid requests and error responses
- **Performance Benchmarks**: Compare Fast vs Accurate modes
- **Utility Functions**: Reusable helper functions for common tasks

## OCR Visualizer Notebook (ocr_visualizer.ipynb)

**OCR-Only Processing and Visualization**:
- Dedicated notebook for OCR extraction and bbox visualization
- Uses `/ocr` API endpoint (no LLM summaries)
- Fast processing without AI inference costs

**Key Features**:
- **S3 File Browser**: Select PDFs from S3 buckets
- **OCR API Call**: Process PDFs with Docling OCR only
- **Bbox Visualization**: Display color-coded bounding boxes
  - Green boxes: Figure/image regions
  - Red boxes: Table regions
  - Labels show detected categories
- **Text Markdown**: Preview extracted text inline
- **Result Download**: Download OCR results to local
- **Statistics**: View extraction stats (pages, figures, tables)

**Use Cases**:
- Quick OCR without AI costs
- Visual validation of extraction accuracy
- Debugging bbox detection issues
- Text extraction for further processing
- Batch OCR validation before full pipeline

**Sections**:
1. API Server Health Check
2. API Schema Confirmation
3. PDF Processing Tests (basic, fast mode, different models)
4. S3 Result Download and Preview
5. Batch Processing Simulation
6. Error Handling Tests
7. Performance Benchmarks (with visualizations)
8. Utility Function Library
9. Usage Examples and Documentation
10. S3 File Browser (interactive widget)
11. S3 Browser with Callback (auto-process on selection)

**Use Cases**:
- API integration testing and validation
- Client application development
- Performance optimization and tuning
- API behavior documentation
- Debugging production issues

## Requirements

- Python 3.12+
- AWS account with:
  - Bedrock model access (Claude Haiku/Sonnet)
  - S3 read/write permissions
- Package manager: uv (not pip or poetry)
- Platforms: macOS / Linux (Windows requires WSL)
- Additional system dependencies:
  - OpenCV is installed via pip (opencv-python package)
  - No additional system packages required

## Programmatic Usage

### Local Processing
```python
from src.converter import DoclingConverter
from src.summarizer import BedrockSummarizer
from src.markdown_builder import MarkdownBuilder
from pathlib import Path

converter = DoclingConverter(table_mode="accurate")
parsed = converter.convert("sample.pdf")

output_dir = Path("output/sample")
output_dir.mkdir(parents=True, exist_ok=True)
parsed.save_assets(output_dir)

summarizer = BedrockSummarizer(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")
page_summaries = summarizer.summarize_pages(parsed)
image_summaries = summarizer.summarize_figures(parsed, page_summaries)
table_summaries = summarizer.summarize_tables(parsed, page_summaries)

builder = MarkdownBuilder(parsed, output_dir)
final_md = builder.build(page_summaries, image_summaries, table_summaries)
Path("output/sample/sample_final.md").write_text(final_md, encoding="utf-8")
```

### FastAPI Client

**Full pipeline with AI summaries:**
```python
import requests

# Process PDF via API (full pipeline)
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
print(f"Success: {result['success']}")
print(f"Final markdown: {result['finalMarkdownUri']}")
print(f"Stats: {result['stats']}")
print(f"Elapsed: {result['elapsedSeconds']}s")
```

**OCR-only with bbox visualization:**
```python
# OCR processing without LLM summaries
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
print(f"Bbox images: {result['bboxImagesUris']}")
print(f"Pages: {result['stats']['pages']}")
print(f"Figures: {result['stats']['figures']}")
print(f"Tables: {result['stats']['tables']}")
```

**Health check:**
```python
health = requests.get("http://localhost:3000/health").json()
print(f"Status: {health['status']}")
```

### S3 Integration
```python
from src.s3_handler import S3Handler
from pathlib import Path

s3 = S3Handler(region_name="us-east-1")

# Download PDF
s3.download_pdf("s3://bucket/input/file.pdf", Path("/tmp/file.pdf"))

# List PDFs in folder
pdf_uris = s3.list_pdfs("s3://bucket/pdfs/")

# Browse current directory (file explorer)
result = s3.browse_path("s3://bucket/input/")
# Returns: {"folders": ["folder1", "folder2"], "pdfs": ["file1.pdf", "file2.pdf"]}

# List result folders (for browsing)
folders = s3.list_folders("s3://bucket/output/")
# Returns: ["sample1", "sample2", ...]

# Upload results directory
s3.upload_directory(Path("output/sample"), "s3://bucket/output/sample/")

# Download entire result folder
local_dir = Path("downloads/sample")
file_count = s3.download_directory("s3://bucket/output/sample/", local_dir)

# Read markdown (single file)
content = s3.read_markdown("s3://bucket/output/sample/sample_final.md")
```

### S3 Browser Widget (JupyterLab only)
```python
from src.s3_browser import create_s3_browser

# Create interactive S3 file browser
browser = create_s3_browser(initial_path="s3://my-bucket/pdfs/")

# Get selected PDF (after user clicks on a PDF)
selected_pdf = browser.get_selected()
if selected_pdf:
    print(f"Selected: {selected_pdf}")

# With callback for automatic processing on selection
def on_pdf_selected(s3_uri: str):
    print(f"Processing: {s3_uri}")
    # Trigger processing automatically

browser = create_s3_browser(
    initial_path="s3://my-bucket/",
    on_select=on_pdf_selected
)
```

**Browser Features**:
- Click folders to navigate down
- Click "↑ Parent" button to go up one level
- Click PDF files to select them
- Edit path manually and click "Go"
- Click "↻ Refresh" to reload current directory
- Selected PDF path available via `browser.get_selected()`
- Optional callback fires immediately when PDF is selected

## Development Patterns

**Adding new summarization fields**:
1. Update prompt in `src/summarizer.py` (SUMMARY_PROMPT or TABLE_SUMMARY_PROMPT)
2. Update `_call_vision()` response parsing if needed
3. Update `MarkdownBuilder._html_row()` calls to include new field
4. Test with sample PDF

**Adding new image/table categories**:
- Image: Retrain DocumentFigureClassifier model (external to this repo)
- Table: Update `TABLE_SUMMARY_PROMPT` category list

**Modifying metadata format**:
- Edit `MarkdownBuilder` methods: `_replace_figures()`, `_wrap_tables()`, `_wrap_pages()`
- Keep HTML table structure for backward compatibility with parsers

**S3 error handling**:
- All S3Handler methods raise `ClientError` on AWS failures
- Wrap calls in try/except, log with logger.error()
- Clean up temp files in finally blocks

## Logging

- Logger name: `pdf_parser` (root), `pdf_parser.converter`, `pdf_parser.summarizer`, etc.
- Level: INFO (default), DEBUG (with -v flag)
- Format: `%(asctime)s %(levelname)-5s %(name)s — %(message)s`
- Child processes reinitialize logging via `_setup_logging()`
