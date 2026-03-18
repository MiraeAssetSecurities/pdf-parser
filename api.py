"""FastAPI server for PDF processing with S3 support.

Usage:
    # Single worker (development)
    uv run uvicorn api:app --host 0.0.0.0 --port 3000

    # Multiple workers (production, parallel request handling)
    uv run uvicorn api:app --host 0.0.0.0 --port 3000 --workers 2

Endpoints:
    POST /process - Process a single PDF from S3 (full pipeline)
    POST /ocr - OCR only with bbox visualization
    GET /health - Health check
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from pdf_parser.converter import DoclingConverter
from pdf_parser.ibm_converter import IbmLayoutConverter
from pdf_parser.s3_handler import S3Handler
from pdf_parser.summarizer import BedrockSummarizer
from pdf_parser.markdown_builder import MarkdownBuilder
from pdf_parser.utils import generate_bbox_images
from office_parser import OfficeParser, OfficeParserConfig

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pdf_parser.api")

app = FastAPI(
    title="Document Parser API",
    description="Extract text, tables, and images from PDFs and Office documents with AI summaries",
    version="0.2.0",
)

# Supported file extensions
PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".rtf"}
ALL_EXTENSIONS = PDF_EXTENSIONS | OFFICE_EXTENSIONS


class ProcessRequest(BaseModel):
    """Request model for document processing (PDF or Office)."""

    inputPath: str = Field(
        ...,
        description="S3 URI of input file (e.g., s3://bucket/input/sample.pdf or report.docx)",
        example="s3://my-bucket/input/sample.pdf",
    )
    outputPath: str = Field(
        ...,
        description="S3 URI of output base path (e.g., s3://bucket/output/)",
        example="s3://my-bucket/output/",
    )
    modelId: Optional[str] = Field(
        default="ap-northeast-2.anthropic.claude-haiku-4-5-20251001-v1:0",
        description="Bedrock model ID for AI summaries",
    )
    noSummary: Optional[bool] = Field(
        default=False,
        description="Skip LLM summaries",
    )
    tableMode: Optional[str] = Field(
        default="accurate",
        description="[PDF only] Table extraction mode: 'accurate' or 'fast'",
    )
    useAccelerator: Optional[bool] = Field(
        default=False,
        description="[PDF only] Enable CPU accelerator for faster processing (num_threads=4)",
    )
    layoutModel: Optional[str] = Field(
        default="docling",
        description="[PDF only] Layout detection model: 'docling' or 'ibm'",
    )
    ibmConfidenceThreshold: Optional[float] = Field(
        default=0.3,
        description="[IBM only] Minimum confidence for layout detection (0.0~1.0)",
        ge=0.0,
        le=1.0,
    )
    outputFormat: Optional[str] = Field(
        default="markdown",
        description="[Office only] Output format: 'markdown', 'html', or 'text'",
    )
    bedrockRegion: Optional[str] = Field(
        default="ap-northeast-2",
        description="[Office only] Bedrock region",
    )


class ProcessResponse(BaseModel):
    """Response model for PDF processing."""

    success: bool
    message: str
    finalMarkdownUri: Optional[str] = None
    uploadedFiles: Optional[list[str]] = None
    stats: Optional[dict] = None
    elapsedSeconds: Optional[float] = None


class OCRRequest(BaseModel):
    """Request model for OCR-only processing."""

    inputPath: str = Field(
        ...,
        description="S3 URI of input PDF (e.g., s3://bucket/input/sample.pdf)",
        example="s3://my-bucket/input/sample.pdf",
    )
    outputPath: str = Field(
        ...,
        description="S3 URI of output base path (e.g., s3://bucket/output/)",
        example="s3://my-bucket/output/",
    )
    tableMode: Optional[str] = Field(
        default="accurate",
        description="Table extraction mode: 'accurate' or 'fast'",
    )
    generateBboxImages: Optional[bool] = Field(
        default=True,
        description="Generate bounding box visualization images",
    )
    useAccelerator: Optional[bool] = Field(
        default=False,
        description="Enable CPU accelerator for faster processing (num_threads=4)",
    )
    layoutModel: Optional[str] = Field(
        default="docling",
        description="Layout detection model: 'docling' (full Docling pipeline) or 'ibm' (IBM LayoutPredictor direct)",
    )
    ibmConfidenceThreshold: Optional[float] = Field(
        default=0.3,
        description="[IBM only] Minimum confidence for bbox display (0.1~1.0). Lower = more boxes shown.",
        ge=0.0,
        le=1.0,
    )


class OCRResponse(BaseModel):
    """Response model for OCR processing."""

    success: bool
    message: str
    textMarkdownUri: Optional[str] = None
    bboxImagesUris: Optional[list[str]] = None
    uploadedFiles: Optional[list[str]] = None
    stats: Optional[dict] = None
    elapsedSeconds: Optional[float] = None


class OfficeProcessRequest(BaseModel):
    """Request model for Office document processing."""

    inputPath: str = Field(
        ...,
        description="S3 URI of input Office file (e.g., s3://bucket/input/report.docx)",
        example="s3://my-bucket/input/report.docx",
    )
    outputPath: str = Field(
        ...,
        description="S3 URI of output base path (e.g., s3://bucket/output/)",
        example="s3://my-bucket/output/",
    )
    modelId: Optional[str] = Field(
        default="ap-northeast-2.anthropic.claude-haiku-4-5-20251001-v1:0",
        description="Bedrock model ID for AI summaries",
    )
    noSummary: Optional[bool] = Field(
        default=False,
        description="Skip LLM summaries",
    )
    outputFormat: Optional[str] = Field(
        default="markdown",
        description="Output format: 'markdown', 'html', or 'text'",
    )
    bedrockRegion: Optional[str] = Field(
        default="ap-northeast-2",
        description="Bedrock region",
    )


class OfficeProcessResponse(BaseModel):
    """Response model for Office document processing."""

    success: bool
    message: str
    outputUri: Optional[str] = None
    uploadedFiles: Optional[list[str]] = None
    stats: Optional[dict] = None
    elapsedSeconds: Optional[float] = None


# ---------------------------------------------------------------------------
# IBM OCR 파이프라인 헬퍼 함수
# ---------------------------------------------------------------------------

def _crop_ibm_region(ibm_parsed, item: dict):
    """IBM 예측 결과(dict)의 bbox로 해당 페이지 이미지를 크롭."""
    page_no = item["page_no"]
    if page_no < 1 or page_no > len(ibm_parsed.page_images):
        return None
    img = ibm_parsed.page_images[page_no - 1]
    l, t, r, b = int(item["l"]), int(item["t"]), int(item["r"]), int(item["b"])
    l, t = max(0, l), max(0, t)
    r, b = min(img.width, r), min(img.height, b)
    if r <= l or b <= t:
        return None
    return img.crop((l, t, r, b))


def _save_ibm_assets(ibm_parsed, output_dir: Path):
    """IBM figure/table bbox 크롭 이미지를 output_dir에 저장."""
    from concurrent.futures import ThreadPoolExecutor
    fig_dir = output_dir / "pictures" / "ibm_figure"
    tbl_img_dir = output_dir / "table" / "img"
    fig_dir.mkdir(parents=True, exist_ok=True)
    tbl_img_dir.mkdir(parents=True, exist_ok=True)

    for i, fig in enumerate(ibm_parsed.get_figures(), start=1):
        img = _crop_ibm_region(ibm_parsed, fig)
        if img:
            img.save(fig_dir / f"{ibm_parsed.doc_name}_picture_{i}.png", "PNG")

    for i, tbl in enumerate(ibm_parsed.get_tables(), start=1):
        img = _crop_ibm_region(ibm_parsed, tbl)
        if img:
            img.save(tbl_img_dir / f"{ibm_parsed.doc_name}_table_{i}.png", "PNG")


def _summarize_pages_ibm(ibm_parsed, summarizer) -> dict:
    """IBM page_images로 페이지별 요약 생성."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pdf_parser.summarizer import SUMMARY_PROMPT

    results: dict = {}
    total = ibm_parsed.get_page_count()

    def _work(page_no, pil_image):
        return page_no, summarizer._call_vision(pil_image, SUMMARY_PROMPT)

    with ThreadPoolExecutor(max_workers=min(total, 10)) as ex:
        futs = {
            ex.submit(_work, i + 1, img): i + 1
            for i, img in enumerate(ibm_parsed.page_images)
        }
        for f in as_completed(futs):
            pn = futs[f]
            try:
                page_no, res = f.result()
                results[page_no] = res
            except Exception as e:
                results[pn] = {"summary": f"Summary generation failed: {e}", "entities": []}
    return results


def _summarize_figures_ibm(ibm_parsed, summarizer, page_summaries: dict) -> dict:
    """IBM figure bbox 크롭 이미지로 figure별 요약 생성 (1-based index)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pdf_parser.summarizer import SUMMARY_PROMPT

    figures = ibm_parsed.get_figures()
    results: dict = {}

    def _work(idx, item):
        img = _crop_ibm_region(ibm_parsed, item)
        if img is None:
            return idx, {"summary": "No image", "entities": []}
        page_no = item["page_no"]
        ctx_info = page_summaries.get(page_no, {})
        ctx = f"\n\n[Page {page_no} context] {ctx_info.get('summary', '')}" if ctx_info.get("summary") else ""
        return idx, summarizer._call_vision(img, SUMMARY_PROMPT + ctx)

    with ThreadPoolExecutor(max_workers=min(len(figures) or 1, 10)) as ex:
        futs = {ex.submit(_work, i + 1, item): i + 1 for i, item in enumerate(figures)}
        for f in as_completed(futs):
            idx = futs[f]
            try:
                i, res = f.result()
                results[i] = res
            except Exception as e:
                results[idx] = {"summary": f"Summary generation failed: {e}", "entities": []}
    return results


def _summarize_tables_ibm(ibm_parsed, summarizer, page_summaries: dict) -> dict:
    """IBM table bbox 크롭 이미지로 table별 요약 생성 (1-based index)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pdf_parser.summarizer import TABLE_SUMMARY_PROMPT

    tables = ibm_parsed.get_tables()
    results: dict = {}

    def _work(idx, item):
        img = _crop_ibm_region(ibm_parsed, item)
        if img is None:
            return idx, {"summary": "No image", "entities": [], "category": "other"}
        page_no = item["page_no"]
        ctx_info = page_summaries.get(page_no, {})
        ctx = f"\n\n[Page {page_no} context] {ctx_info.get('summary', '')}" if ctx_info.get("summary") else ""
        return idx, summarizer._call_vision(img, TABLE_SUMMARY_PROMPT + ctx)

    with ThreadPoolExecutor(max_workers=min(len(tables) or 1, 10)) as ex:
        futs = {ex.submit(_work, i + 1, item): i + 1 for i, item in enumerate(tables)}
        for f in as_completed(futs):
            idx = futs[f]
            try:
                i, res = f.result()
                results[i] = res
            except Exception as e:
                results[idx] = {"summary": f"Summary generation failed: {e}", "entities": [], "category": "other"}
    return results


def _html_row(key: str, value: str) -> str:
    return f"  <tr>\n    <td>{key}</td>\n    <td>{value}</td>\n  </tr>\n"


def _build_ibm_markdown(
    ibm_parsed,
    output_dir: Path,
    page_summaries: dict,
    figure_summaries: dict,
    table_summaries: dict,
) -> str:
    """IBM 파이프라인 결과를 최종 마크다운으로 조립."""
    doc_name = ibm_parsed.doc_name
    figures = ibm_parsed.get_figures()
    tables = ibm_parsed.get_tables()

    # page_no → [(1-based_idx, item)] 매핑
    fig_by_page: dict = {}
    for i, fig in enumerate(figures, start=1):
        fig_by_page.setdefault(fig["page_no"], []).append((i, fig))

    tbl_by_page: dict = {}
    for i, tbl in enumerate(tables, start=1):
        tbl_by_page.setdefault(tbl["page_no"], []).append((i, tbl))

    parts = []
    for page_no, page_text in enumerate(ibm_parsed.page_texts, start=1):
        page_info = page_summaries.get(page_no, {"summary": "", "entities": []})
        page_meta = (
            '<table class="page-meta">\n'
            + _html_row("page_number", str(page_no))
            + _html_row("page_summary", page_info.get("summary", ""))
            + _html_row("entities", ", ".join(page_info.get("entities", [])))
            + "</table>\n"
        )

        fig_blocks = []
        for idx, fig in fig_by_page.get(page_no, []):
            info = figure_summaries.get(idx, {"summary": "", "entities": []})
            img_path = output_dir / "pictures" / "ibm_figure" / f"{doc_name}_picture_{idx}.png"
            img_rel = str(img_path.relative_to(output_dir)) if img_path.exists() else ""
            bbox_str = f'l={fig["l"]:.1f} t={fig["t"]:.1f} r={fig["r"]:.1f} b={fig["b"]:.1f}'
            fig_meta = (
                '<table class="figure-meta">\n'
                + _html_row("image_id", f"figure-{idx:03d}")
                + _html_row("category", fig.get("label", "Picture"))
                + _html_row("page_number", str(page_no))
                + _html_row("confidence", f"{fig.get('confidence', 0):.2f}")
                + _html_row("image_summary", info.get("summary", ""))
                + _html_row("entities", ", ".join(info.get("entities", [])))
                + _html_row("bbox", bbox_str)
                + _html_row("img_source", img_rel)
                + "</table>\n"
            )
            img_md = f"![figure-{idx:03d}]({img_rel})" if img_rel else ""
            fig_blocks.append(f"{fig_meta}\n{img_md}")

        tbl_blocks = []
        for idx, tbl in tbl_by_page.get(page_no, []):
            info = table_summaries.get(idx, {"summary": "", "entities": [], "category": "other"})
            img_path = output_dir / "table" / "img" / f"{doc_name}_table_{idx}.png"
            img_rel = str(img_path.relative_to(output_dir)) if img_path.exists() else ""
            bbox_str = f'l={tbl["l"]:.1f} t={tbl["t"]:.1f} r={tbl["r"]:.1f} b={tbl["b"]:.1f}'
            tbl_meta = (
                '<table class="table-meta">\n'
                + _html_row("table_id", f"table-{idx:03d}")
                + _html_row("category", info.get("category", "other"))
                + _html_row("page_number", str(page_no))
                + _html_row("confidence", f"{tbl.get('confidence', 0):.2f}")
                + _html_row("table_summary", info.get("summary", ""))
                + _html_row("entities", ", ".join(info.get("entities", [])))
                + _html_row("bbox", bbox_str)
                + _html_row("img_source", img_rel)
                + "</table>\n"
            )
            img_md = f"![table-{idx:03d}]({img_rel})" if img_rel else ""
            tbl_blocks.append(f"{tbl_meta}\n{img_md}")

        elements = [page_meta, page_text.strip()] + fig_blocks + tbl_blocks
        page_content = "\n\n".join(e for e in elements if e.strip())
        parts.append(f"<page-{page_no:03d}>\n{page_content}\n</page-{page_no:03d}>")

    return "\n\n".join(parts)


@app.post("/ocr", response_model=OCRResponse)
async def ocr_pdf(request: OCRRequest):
    """OCR-only processing: Extract text and visualize bounding boxes.

    Args:
        request: OCRRequest with inputPath and outputPath

    Returns:
        OCRResponse with OCR results and bbox images
    """
    # Validate S3 URIs
    if not request.inputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="inputPath must be S3 URI starting with s3://",
        )
    if not request.outputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="outputPath must be S3 URI starting with s3://",
        )
    if not request.inputPath.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="inputPath must be a PDF file (ending with .pdf)",
        )
    if request.tableMode not in ["accurate", "fast"]:
        raise HTTPException(
            status_code=400,
            detail="tableMode must be 'accurate' or 'fast'",
        )
    if request.layoutModel not in ["docling", "ibm"]:
        raise HTTPException(
            status_code=400,
            detail="layoutModel must be 'docling' or 'ibm'",
        )

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="pdf-parser-ocr-"))

    try:
        t0 = time.time()
        logger.info(
            "OCR request [layoutModel=%s]: %s → %s",
            request.layoutModel, request.inputPath, request.outputPath,
        )

        # Initialize handlers
        s3 = S3Handler()

        # Extract PDF name
        pdf_name = Path(request.inputPath).name
        pdf_stem = Path(request.inputPath).stem

        # Local paths
        local_pdf = temp_dir / pdf_name
        local_output = temp_dir / pdf_stem
        local_output.mkdir(parents=True, exist_ok=True)

        # 1) Download PDF from S3
        logger.info("[%s] Downloading from S3", pdf_name)
        s3.download_pdf(request.inputPath, local_pdf)

        # 2) Conversion (Docling or IBM)
        bbox_uris = []

        if request.layoutModel == "ibm":
            # --- IBM LayoutPredictor 경로 ---
            logger.info("[%s] IBM layout conversion started", pdf_name)
            converter = IbmLayoutConverter()
            parsed = converter.convert(local_pdf)
            n_pages = parsed.get_page_count()
            n_figs = len(parsed.get_figures())
            n_tbls = len(parsed.get_tables())
            logger.info(
                "[%s] IBM layout done — %d pages, %d figures, %d tables",
                pdf_name, n_pages, n_figs, n_tbls,
            )

            # 텍스트 마크다운 저장 (PyMuPDF 기반)
            logger.info("[%s] Saving text markdown (fitz)", pdf_name)
            text_path = local_output / f"{parsed.doc_name}_text.md"
            text_path.write_text(parsed.export_text_markdown(), encoding="utf-8")

            # bbox 시각화 이미지 생성
            if request.generateBboxImages:
                threshold = request.ibmConfidenceThreshold if request.ibmConfidenceThreshold is not None else 0.3
                logger.info("[%s] Generating IBM bbox images (threshold=%.2f)", pdf_name, threshold)
                bbox_dir = local_output / "bbox"
                bbox_dir.mkdir(parents=True, exist_ok=True)

                bbox_images = parsed.generate_bbox_images(display_threshold=threshold)
                for page_no, jpg_bytes in bbox_images.items():
                    bbox_path = bbox_dir / f"{parsed.doc_name}_page{page_no:03d}_bbox.jpg"
                    bbox_path.write_bytes(jpg_bytes)

                logger.info("[%s] Generated %d IBM bbox images", pdf_name, len(bbox_images))

            doc_name_str = parsed.doc_name
            stats_extra = {
                "layoutModel": "ibm",
                "ibmConfidenceThreshold": request.ibmConfidenceThreshold,
            }

        else:
            # --- Docling 경로 (기존) ---
            logger.info(
                "[%s] Docling OCR conversion started (accelerator=%s)",
                pdf_name, request.useAccelerator,
            )
            converter = DoclingConverter(
                table_mode=request.tableMode, use_accelerator=request.useAccelerator
            )
            parsed = converter.convert(local_pdf)
            n_pages = len(parsed.doc.pages)
            n_figs = len(parsed.get_figures())
            n_tbls = len(parsed.get_tables())
            logger.info(
                "[%s] Docling OCR done — %d pages, %d figures, %d tables",
                pdf_name, n_pages, n_figs, n_tbls,
            )

            # 텍스트 마크다운 저장 (Docling 기반)
            logger.info("[%s] Saving text markdown (Docling)", pdf_name)
            text_path = local_output / f"{parsed.doc_name}_text.md"
            text_path.write_text(parsed.doc.export_to_markdown(), encoding="utf-8")

            # bbox 시각화 이미지 생성
            if request.generateBboxImages:
                logger.info("[%s] Generating Docling bbox images", pdf_name)
                bbox_dir = local_output / "bbox"
                bbox_dir.mkdir(parents=True, exist_ok=True)

                bbox_images = generate_bbox_images(parsed, bbox_dir)
                for page_no, jpg_bytes in bbox_images.items():
                    bbox_path = bbox_dir / f"{parsed.doc_name}_page{page_no:03d}_bbox.jpg"
                    bbox_path.write_bytes(jpg_bytes)

                logger.info("[%s] Generated %d Docling bbox images", pdf_name, len(bbox_images))

            doc_name_str = parsed.doc_name
            stats_extra = {
                "layoutModel": "docling",
                "acceleratorUsed": request.useAccelerator,
                "tableMode": request.tableMode,
            }

        # 3) Upload to S3
        logger.info("[%s] Uploading OCR results to S3", pdf_name)
        s3_output_uri = request.outputPath.rstrip("/") + f"/{pdf_stem}/"
        uploaded_uris = s3.upload_directory(local_output, s3_output_uri)

        text_md_s3_uri = s3_output_uri + f"{doc_name_str}_text.md"

        if request.generateBboxImages:
            bbox_uris = [uri for uri in uploaded_uris if "/bbox/" in uri]

        elapsed = time.time() - t0
        logger.info("[%s] OCR done → %s (%.1fs total)", pdf_name, text_md_s3_uri, elapsed)

        return OCRResponse(
            success=True,
            message=f"Successfully OCR processed {pdf_name}",
            textMarkdownUri=text_md_s3_uri,
            bboxImagesUris=bbox_uris if bbox_uris else None,
            uploadedFiles=uploaded_uris,
            stats={
                "pages": n_pages,
                "figures": n_figs,
                "tables": n_tbls,
                "bboxImages": len(bbox_uris) if bbox_uris else 0,
                **stats_extra,
            },
            elapsedSeconds=round(elapsed, 2),
        )

    except Exception as e:
        logger.error("❌ OCR processing failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(e)}",
        )

    finally:
        # Cleanup temp files
        logger.info("🧹 Cleaning up temp files")
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "document-parser-api",
        "version": "0.2.0",
        "supported_formats": {
            "pdf": list(PDF_EXTENSIONS),
            "office": list(OFFICE_EXTENSIONS),
        },
    }


@app.post("/process", response_model=ProcessResponse)
async def process_pdf(request: ProcessRequest):
    """Process a single PDF from S3 and upload results to S3.

    This endpoint is specifically for PDF files.
    For unified processing (PDF + Office), use /process-document instead.

    Args:
        request: ProcessRequest with inputPath and outputPath

    Returns:
        ProcessResponse with processing results
    """
    # Validate S3 URIs
    if not request.inputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="inputPath must be S3 URI starting with s3://",
        )
    if not request.outputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="outputPath must be S3 URI starting with s3://",
        )
    if not request.inputPath.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="inputPath must be a PDF file (ending with .pdf)",
        )
    if request.tableMode not in ["accurate", "fast"]:
        raise HTTPException(
            status_code=400,
            detail="tableMode must be 'accurate' or 'fast'",
        )
    if (request.layoutModel or "docling") not in ["docling", "ibm"]:
        raise HTTPException(
            status_code=400,
            detail="layoutModel must be 'docling' or 'ibm'",
        )

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="pdf-parser-"))

    try:
        t0 = time.time()
        logger.info("🚀 Processing request: %s → %s", request.inputPath, request.outputPath)

        # Initialize handlers
        s3 = S3Handler()

        # Extract PDF name
        pdf_name = Path(request.inputPath).name
        pdf_stem = Path(request.inputPath).stem

        # Local paths
        local_pdf = temp_dir / pdf_name
        local_output = temp_dir / pdf_stem
        local_output.mkdir(parents=True, exist_ok=True)

        # 1) Download PDF from S3
        logger.info("📥 [%s] Downloading from S3", pdf_name)
        s3.download_pdf(request.inputPath, local_pdf)

        layout_model = request.layoutModel or "docling"

        if layout_model == "ibm":
            # ── IBM LayoutPredictor 경로 ──────────────────────────────────
            threshold = request.ibmConfidenceThreshold if request.ibmConfidenceThreshold is not None else 0.3
            logger.info("📄 [%s] IBM layout conversion started (threshold=%.2f)", pdf_name, threshold)
            ibm_converter = IbmLayoutConverter(base_threshold=threshold)
            ibm_parsed = ibm_converter.convert(local_pdf)
            n_pages = ibm_parsed.get_page_count()
            n_figs = len(ibm_parsed.get_figures())
            n_tbls = len(ibm_parsed.get_tables())
            logger.info(
                "✅ [%s] IBM conversion done — %d pages, %d figures, %d tables",
                pdf_name, n_pages, n_figs, n_tbls,
            )

            # 에셋 저장 (bbox 크롭 이미지)
            logger.info("💾 [%s] Saving IBM assets", pdf_name)
            _save_ibm_assets(ibm_parsed, local_output)

            # 텍스트 마크다운 저장
            text_path = local_output / f"{ibm_parsed.doc_name}_text.md"
            text_path.write_text(ibm_parsed.export_text_markdown(), encoding="utf-8")

            # LLM 요약
            page_summaries, image_summaries, table_summaries = {}, {}, {}
            if not request.noSummary:
                summarizer = BedrockSummarizer(model_id=request.modelId)

                logger.info("🔍 [%s] Summarizing pages (IBM)... (%d)", pdf_name, n_pages)
                page_summaries = _summarize_pages_ibm(ibm_parsed, summarizer)

                logger.info("🖼️  [%s] Summarizing figures (IBM)... (%d)", pdf_name, n_figs)
                image_summaries = _summarize_figures_ibm(ibm_parsed, summarizer, page_summaries)

                logger.info("📊 [%s] Summarizing tables (IBM)... (%d)", pdf_name, n_tbls)
                table_summaries = _summarize_tables_ibm(ibm_parsed, summarizer, page_summaries)

            # 최종 마크다운 빌드
            logger.info("📝 [%s] Building IBM final markdown", pdf_name)
            final_md = _build_ibm_markdown(ibm_parsed, local_output, page_summaries, image_summaries, table_summaries)
            doc_name_str = ibm_parsed.doc_name
            stats_extra = {"layoutModel": "ibm", "ibmConfidenceThreshold": threshold}

        else:
            # ── Docling 기존 경로 ─────────────────────────────────────────
            logger.info("📄 [%s] Docling conversion started (accelerator=%s)", pdf_name, request.useAccelerator)
            converter = DoclingConverter(table_mode=request.tableMode, use_accelerator=request.useAccelerator)
            parsed = converter.convert(local_pdf)
            n_pages = len(parsed.doc.pages)
            n_figs = len(parsed.get_figures())
            n_tbls = len(parsed.get_tables())
            logger.info(
                "✅ [%s] Conversion done — %d pages, %d figures, %d tables",
                pdf_name, n_pages, n_figs, n_tbls,
            )

            logger.info("💾 [%s] Saving assets locally", pdf_name)
            parsed.save_assets(local_output)

            text_path = local_output / f"{parsed.doc_name}_text.md"
            text_path.write_text(parsed.doc.export_to_markdown(), encoding="utf-8")

            page_summaries, image_summaries, table_summaries = {}, {}, {}
            if not request.noSummary:
                summarizer = BedrockSummarizer(model_id=request.modelId)

                logger.info("🔍 [%s] Summarizing pages... (%d)", pdf_name, n_pages)
                page_summaries = summarizer.summarize_pages(parsed)

                logger.info("🖼️  [%s] Summarizing figures... (%d)", pdf_name, n_figs)
                image_summaries = summarizer.summarize_figures(parsed, page_summaries)

                logger.info("📊 [%s] Summarizing tables... (%d)", pdf_name, n_tbls)
                table_summaries = summarizer.summarize_tables(parsed, page_summaries)

            logger.info("📝 [%s] Building final markdown", pdf_name)
            builder = MarkdownBuilder(parsed, local_output)
            final_md = builder.build(page_summaries, image_summaries, table_summaries)
            doc_name_str = parsed.doc_name
            stats_extra = {
                "layoutModel": "docling",
                "acceleratorUsed": request.useAccelerator,
                "tableMode": request.tableMode,
            }

        final_md_path = local_output / f"{doc_name_str}_final.md"
        final_md_path.write_text(final_md, encoding="utf-8")

        # Upload to S3
        logger.info("📤 [%s] Uploading results to S3", pdf_name)
        s3_output_uri = request.outputPath.rstrip("/") + f"/{pdf_stem}/"
        uploaded_uris = s3.upload_directory(local_output, s3_output_uri)

        final_md_s3_uri = s3_output_uri + f"{doc_name_str}_final.md"

        elapsed = time.time() - t0
        logger.info("🎉 [%s] Done → %s (%.1fs total)", pdf_name, final_md_s3_uri, elapsed)

        return ProcessResponse(
            success=True,
            message=f"Successfully processed {pdf_name}",
            finalMarkdownUri=final_md_s3_uri,
            uploadedFiles=uploaded_uris,
            stats={
                "pages": n_pages,
                "figures": n_figs,
                "tables": n_tbls,
                "summariesGenerated": not request.noSummary,
                **stats_extra,
            },
            elapsedSeconds=round(elapsed, 2),
        )

    except Exception as e:
        logger.error("❌ Processing failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Processing failed: {str(e)}",
        )

    finally:
        # Cleanup temp files
        logger.info("🧹 Cleaning up temp files")
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/process-document")
async def process_document(request: ProcessRequest):
    """Unified endpoint: Process PDF or Office document based on file extension.

    Automatically routes to appropriate processor based on file extension.

    Supports:
    - PDF: .pdf
    - Office: .docx, .pptx, .xlsx, .odt, .odp, .ods, .rtf

    Args:
        request: ProcessRequest with inputPath and outputPath

    Returns:
        ProcessResponse (for PDF) or OfficeProcessResponse (for Office)
    """
    # Validate S3 URIs
    if not request.inputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="inputPath must be S3 URI starting with s3://",
        )
    if not request.outputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="outputPath must be S3 URI starting with s3://",
        )

    # Determine file type
    file_ext = Path(request.inputPath).suffix.lower()

    if file_ext in PDF_EXTENSIONS:
        # Route to PDF processor
        return await process_pdf(request)
    elif file_ext in OFFICE_EXTENSIONS:
        # Convert to OfficeProcessRequest and route to Office processor
        office_request = OfficeProcessRequest(
            inputPath=request.inputPath,
            outputPath=request.outputPath,
            modelId=request.modelId,
            noSummary=request.noSummary,
            outputFormat=request.outputFormat or "markdown",
            bedrockRegion=request.bedrockRegion or "ap-northeast-2",
        )
        return await process_office(office_request)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension: {file_ext}. Supported: {', '.join(sorted(ALL_EXTENSIONS))}",
        )


@app.post("/process-office", response_model=OfficeProcessResponse)
async def process_office(request: OfficeProcessRequest):
    """Process a single Office document from S3 and upload results to S3.

    Supports: docx, pptx, xlsx, odt, odp, ods, rtf

    Args:
        request: OfficeProcessRequest with inputPath and outputPath

    Returns:
        OfficeProcessResponse with processing results
    """
    # Validate S3 URIs
    if not request.inputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="inputPath must be S3 URI starting with s3://",
        )
    if not request.outputPath.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail="outputPath must be S3 URI starting with s3://",
        )

    # Validate file extension
    file_ext = Path(request.inputPath).suffix.lower()
    if file_ext not in OFFICE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"inputPath must be an Office file. Supported: {', '.join(sorted(OFFICE_EXTENSIONS))}",
        )

    # Validate output format
    if request.outputFormat not in ["markdown", "html", "text"]:
        raise HTTPException(
            status_code=400,
            detail="outputFormat must be 'markdown', 'html', or 'text'",
        )

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="office-parser-"))

    try:
        t0 = time.time()
        logger.info("🚀 Office processing request: %s → %s", request.inputPath, request.outputPath)

        # Initialize handlers
        s3 = S3Handler()

        # Extract file name
        file_name = Path(request.inputPath).name
        file_stem = Path(request.inputPath).stem

        # Local paths
        local_file = temp_dir / file_name
        local_output = temp_dir / file_stem
        local_output.mkdir(parents=True, exist_ok=True)

        # 1) Download file from S3
        logger.info("📥 [%s] Downloading from S3", file_name)
        s3.download_pdf(request.inputPath, local_file)  # Reuse download_pdf (works for any file)

        # 2) Parse Office document
        logger.info("📄 [%s] Office parsing started", file_name)
        config = OfficeParserConfig(
            summarize=not request.noSummary,
            bedrock_model_id=request.modelId,
            bedrock_region=request.bedrockRegion,
        )
        ast = OfficeParser.parse_office(str(local_file), config)

        # 3) Save attachments to pictures/ folder
        pictures_dir = local_output / "pictures"
        image_dir = None
        if config.extract_attachments and ast.attachments:
            pictures_dir.mkdir(parents=True, exist_ok=True)
            for att in ast.attachments:
                (pictures_dir / att.filename).write_bytes(att.data)
            image_dir = "pictures"
            logger.info("💾 [%s] Saved %d attachments", file_name, len(ast.attachments))

        # 4) Generate output
        ext_map = {"html": ".html", "markdown": ".md", "text": ".txt"}
        out_ext = ext_map.get(request.outputFormat, ".md")
        out_path = local_output / f"{file_stem}{out_ext}"

        if request.outputFormat == "html":
            output = ast.to_html(image_dir=image_dir)
        elif request.outputFormat == "markdown":
            output = ast.to_markdown(image_dir=image_dir)
        else:  # text
            output = ast.to_text()

        out_path.write_text(output, encoding="utf-8")
        logger.info("✅ [%s] Parsing done", file_name)

        # 5) Upload to S3
        logger.info("📤 [%s] Uploading results to S3", file_name)
        s3_output_uri = request.outputPath.rstrip("/") + f"/{file_stem}/"
        uploaded_uris = s3.upload_directory(local_output, s3_output_uri)

        output_s3_uri = s3_output_uri + f"{file_stem}{out_ext}"

        elapsed = time.time() - t0
        logger.info("🎉 [%s] Done → %s (%.1fs total)", file_name, output_s3_uri, elapsed)

        return OfficeProcessResponse(
            success=True,
            message=f"Successfully processed {file_name}",
            outputUri=output_s3_uri,
            uploadedFiles=uploaded_uris,
            stats={
                "fileType": file_ext,
                "outputFormat": request.outputFormat,
                "attachments": len(ast.attachments),
                "summariesGenerated": not request.noSummary,
            },
            elapsedSeconds=round(elapsed, 2),
        )

    except Exception as e:
        logger.error("❌ Office processing failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Office processing failed: {str(e)}",
        )

    finally:
        # Cleanup temp files
        logger.info("🧹 Cleaning up temp files")
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
