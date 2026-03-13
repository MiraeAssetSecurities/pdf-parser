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

from src.converter import DoclingConverter
from src.s3_handler import S3Handler
from src.summarizer import BedrockSummarizer
from src.markdown_builder import MarkdownBuilder
from src.utils import generate_bbox_images

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pdf_parser.api")

app = FastAPI(
    title="PDF Parser API",
    description="Extract text, tables, and images from PDFs with AI summaries",
    version="0.1.0",
)


class ProcessRequest(BaseModel):
    """Request model for PDF processing."""

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
    modelId: Optional[str] = Field(
        default="ap-northeast-2.anthropic.claude-haiku-4-5-20251001-v1:0",
        description="Bedrock model ID for AI summaries",
    )
    noSummary: Optional[bool] = Field(
        default=False,
        description="Skip LLM summaries (only Docling extraction)",
    )
    tableMode: Optional[str] = Field(
        default="accurate",
        description="Table extraction mode: 'accurate' or 'fast'",
    )
    useAccelerator: Optional[bool] = Field(
        default=False,
        description="Enable CPU accelerator for faster processing (num_threads=4)",
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


class OCRResponse(BaseModel):
    """Response model for OCR processing."""

    success: bool
    message: str
    textMarkdownUri: Optional[str] = None
    bboxImagesUris: Optional[list[str]] = None
    uploadedFiles: Optional[list[str]] = None
    stats: Optional[dict] = None
    elapsedSeconds: Optional[float] = None


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

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="pdf-parser-ocr-"))

    try:
        t0 = time.time()
        logger.info("🔍 OCR request: %s → %s", request.inputPath, request.outputPath)

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

        # 2) Docling conversion (OCR only)
        logger.info("📄 [%s] Docling OCR conversion started (accelerator=%s)", pdf_name, request.useAccelerator)
        converter = DoclingConverter(table_mode=request.tableMode, use_accelerator=request.useAccelerator)
        parsed = converter.convert(local_pdf)
        n_pages = len(parsed.doc.pages)
        n_figs = len(parsed.get_figures())
        n_tbls = len(parsed.get_tables())
        logger.info(
            "✅ [%s] OCR done — %d pages, %d figures, %d tables",
            pdf_name, n_pages, n_figs, n_tbls,
        )

        # 3) Save text markdown
        logger.info("💾 [%s] Saving text markdown", pdf_name)
        text_path = local_output / f"{parsed.doc_name}_text.md"
        text_path.write_text(parsed.doc.export_to_markdown(), encoding="utf-8")

        # 4) Generate bbox visualization images
        bbox_uris = []
        if request.generateBboxImages:
            logger.info("🖼️  [%s] Generating bbox visualization images", pdf_name)
            bbox_dir = local_output / "bbox"
            bbox_dir.mkdir(parents=True, exist_ok=True)

            bbox_images = generate_bbox_images(parsed, bbox_dir)

            # Save bbox images locally
            for page_no, jpg_bytes in bbox_images.items():
                bbox_path = bbox_dir / f"{parsed.doc_name}_page{page_no:03d}_bbox.jpg"
                bbox_path.write_bytes(jpg_bytes)

            logger.info("✅ [%s] Generated %d bbox images", pdf_name, len(bbox_images))

        # 5) Upload to S3
        logger.info("📤 [%s] Uploading OCR results to S3", pdf_name)
        s3_output_uri = request.outputPath.rstrip("/") + f"/{pdf_stem}/"
        uploaded_uris = s3.upload_directory(local_output, s3_output_uri)

        text_md_s3_uri = s3_output_uri + f"{parsed.doc_name}_text.md"

        # Filter bbox image URIs
        if request.generateBboxImages:
            bbox_uris = [uri for uri in uploaded_uris if "/bbox/" in uri]

        elapsed = time.time() - t0
        logger.info("🎉 [%s] OCR done → %s (%.1fs total)", pdf_name, text_md_s3_uri, elapsed)

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
                "acceleratorUsed": request.useAccelerator,
                "tableMode": request.tableMode,
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
    return {"status": "healthy", "service": "pdf-parser-api"}


@app.post("/process", response_model=ProcessResponse)
async def process_pdf(request: ProcessRequest):
    """Process a single PDF from S3 and upload results to S3.

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

        # 2) Docling conversion
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

        # 3) Save assets locally
        logger.info("💾 [%s] Saving assets locally", pdf_name)
        parsed.save_assets(local_output)

        # Save text markdown
        text_path = local_output / f"{parsed.doc_name}_text.md"
        text_path.write_text(parsed.doc.export_to_markdown(), encoding="utf-8")

        # 4) LLM summaries
        page_summaries, image_summaries, table_summaries = {}, {}, {}
        if not request.noSummary:
            summarizer = BedrockSummarizer(model_id=request.modelId)

            logger.info("🔍 [%s] Summarizing pages... (%d)", pdf_name, n_pages)
            page_summaries = summarizer.summarize_pages(parsed)

            logger.info("🖼️  [%s] Summarizing figures... (%d)", pdf_name, n_figs)
            image_summaries = summarizer.summarize_figures(parsed, page_summaries)

            logger.info("📊 [%s] Summarizing tables... (%d)", pdf_name, n_tbls)
            table_summaries = summarizer.summarize_tables(parsed, page_summaries)

        # 5) Build final markdown
        logger.info("📝 [%s] Building final markdown", pdf_name)
        builder = MarkdownBuilder(parsed, local_output)
        final_md = builder.build(page_summaries, image_summaries, table_summaries)

        final_md_path = local_output / f"{parsed.doc_name}_final.md"
        final_md_path.write_text(final_md, encoding="utf-8")

        # 6) Upload to S3
        logger.info("📤 [%s] Uploading results to S3", pdf_name)
        s3_output_uri = request.outputPath.rstrip("/") + f"/{pdf_stem}/"
        uploaded_uris = s3.upload_directory(local_output, s3_output_uri)

        final_md_s3_uri = s3_output_uri + f"{parsed.doc_name}_final.md"

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
                "acceleratorUsed": request.useAccelerator,
                "tableMode": request.tableMode,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
