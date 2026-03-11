"""PDF 파서 CLI with S3 support — S3에서 PDF 읽고 결과를 S3에 저장.

사용법:
    # S3 단일 PDF 처리
    python run_s3.py s3://my-bucket/input/sample.pdf s3://my-bucket/output/

    # S3 폴더의 PDF 일괄 처리
    python run_s3.py s3://my-bucket/pdfs/ s3://my-bucket/output/ --workers 4

    # 로컬 임시 디렉토리 지정
    python run_s3.py s3://bucket/input.pdf s3://bucket/output/ --temp-dir /tmp/pdf-parser
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from src.s3_handler import S3Handler

logger = logging.getLogger("pdf_parser.s3")


def _setup_logging(verbose: bool = False):
    fmt = "%(asctime)s %(levelname)-5s %(name)s — %(message)s"
    logging.basicConfig(level=logging.WARNING, format=fmt, datefmt="%H:%M:%S")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def _is_s3_uri(path: str) -> bool:
    """Check if path is S3 URI."""
    return path.startswith("s3://")


def process_single_s3(
    s3_pdf_uri: str,
    s3_output_base: str,
    model_id: str,
    no_summary: bool,
    table_mode: str,
    temp_dir: Path,
    verbose: bool = False,
) -> tuple[str, list[str]]:
    """S3에서 PDF 다운로드 → 변환 → 결과를 S3에 업로드.

    Args:
        s3_pdf_uri: S3 PDF 경로 (s3://bucket/input/file.pdf)
        s3_output_base: S3 출력 베이스 경로 (s3://bucket/output/)
        model_id: Bedrock 모델 ID
        no_summary: LLM 요약 스킵 여부
        table_mode: TableFormer 모드 (accurate/fast)
        temp_dir: 로컬 임시 디렉토리
        verbose: 상세 로그 출력

    Returns:
        (final_md_s3_uri, list_of_uploaded_s3_uris)
    """
    _setup_logging(verbose)
    from src.converter import DoclingConverter
    from src.summarizer import BedrockSummarizer
    from src.markdown_builder import MarkdownBuilder

    s3 = S3Handler()

    # S3 URI에서 파일명 추출
    pdf_name = Path(s3_pdf_uri).name
    pdf_stem = Path(s3_pdf_uri).stem

    # 로컬 임시 경로 설정
    local_pdf = temp_dir / pdf_name
    local_output = temp_dir / pdf_stem
    local_output.mkdir(parents=True, exist_ok=True)

    t0 = time.time()

    # 1) S3에서 PDF 다운로드
    logger.info("📥 [%s] Downloading from S3", pdf_name)
    s3.download_pdf(s3_pdf_uri, local_pdf)

    # 2) Docling 변환 (로컬)
    logger.info("📄 [%s] Docling conversion started", pdf_name)
    converter = DoclingConverter(table_mode=table_mode)
    parsed = converter.convert(local_pdf)
    n_pages = len(parsed.doc.pages)
    n_figs = len(parsed.get_figures())
    n_tbls = len(parsed.get_tables())
    logger.info(
        "✅ [%s] Conversion done — %d pages, %d figures, %d tables",
        pdf_name, n_pages, n_figs, n_tbls,
    )

    # 3) 에셋 저장 (로컬)
    logger.info("💾 [%s] Saving assets locally", pdf_name)
    parsed.save_assets(local_output)

    # 원본 텍스트 마크다운
    text_path = local_output / f"{parsed.doc_name}_text.md"
    text_path.write_text(parsed.doc.export_to_markdown(), encoding="utf-8")

    # 4) LLM 요약
    page_summaries, image_summaries, table_summaries = {}, {}, {}
    if not no_summary:
        summarizer = BedrockSummarizer(model_id=model_id)

        logger.info("🔍 [%s] Summarizing pages... (%d)", pdf_name, n_pages)
        page_summaries = summarizer.summarize_pages(parsed)

        logger.info("🖼️  [%s] Summarizing figures... (%d)", pdf_name, n_figs)
        image_summaries = summarizer.summarize_figures(parsed, page_summaries)

        logger.info("📊 [%s] Summarizing tables... (%d)", pdf_name, n_tbls)
        table_summaries = summarizer.summarize_tables(parsed, page_summaries)

    # 5) 최종 마크다운 조립
    logger.info("📝 [%s] Building final markdown", pdf_name)
    builder = MarkdownBuilder(parsed, local_output)
    final_md = builder.build(page_summaries, image_summaries, table_summaries)

    final_md_path = local_output / f"{parsed.doc_name}_final.md"
    final_md_path.write_text(final_md, encoding="utf-8")

    # 6) S3에 업로드
    logger.info("📤 [%s] Uploading results to S3", pdf_name)
    s3_output_uri = s3_output_base.rstrip("/") + f"/{pdf_stem}/"
    uploaded_uris = s3.upload_directory(local_output, s3_output_uri)

    final_md_s3_uri = s3_output_uri + f"{parsed.doc_name}_final.md"

    elapsed = time.time() - t0
    logger.info("🎉 [%s] Done → %s (%.1fs total)", pdf_name, final_md_s3_uri, elapsed)

    # 7) 로컬 임시 파일 정리
    local_pdf.unlink(missing_ok=True)
    shutil.rmtree(local_output, ignore_errors=True)

    return final_md_s3_uri, uploaded_uris


def main():
    parser = argparse.ArgumentParser(
        description="Docling PDF 파서 + Bedrock LLM 요약 (S3 지원)"
    )
    parser.add_argument(
        "input",
        type=str,
        help="S3 PDF URI 또는 S3 폴더 URI (s3://bucket/path/file.pdf 또는 s3://bucket/pdfs/)",
    )
    parser.add_argument(
        "output",
        type=str,
        help="S3 출력 베이스 URI (s3://bucket/output/)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="폴더 모드 시 PDF 병렬 처리 수 (기본: 2)",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="LLM 요약 비활성화",
    )
    parser.add_argument(
        "--model-id",
        default="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        help="Bedrock 모델 ID",
    )
    parser.add_argument(
        "--table-mode",
        choices=["accurate", "fast"],
        default="accurate",
        help="TableFormer 모드",
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=None,
        help="로컬 임시 디렉토리 (기본: 시스템 temp)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG 로그 출력",
    )
    args = parser.parse_args()

    _setup_logging(args.verbose)

    # S3 URI 검증
    if not _is_s3_uri(args.input):
        logger.error("❌ Input must be S3 URI starting with s3://")
        sys.exit(1)
    if not _is_s3_uri(args.output):
        logger.error("❌ Output must be S3 URI starting with s3://")
        sys.exit(1)

    # 임시 디렉토리 설정
    if args.temp_dir:
        temp_base = args.temp_dir
        temp_base.mkdir(parents=True, exist_ok=True)
    else:
        temp_base = Path(tempfile.gettempdir()) / "pdf-parser"
        temp_base.mkdir(parents=True, exist_ok=True)

    s3 = S3Handler()

    # 단일 PDF 처리
    if args.input.lower().endswith(".pdf"):
        temp_dir = temp_base / "single"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            process_single_s3(
                args.input,
                args.output,
                args.model_id,
                args.no_summary,
                args.table_mode,
                temp_dir,
                args.verbose,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return

    # S3 폴더 일괄 처리
    logger.info("📂 Listing PDFs in %s", args.input)
    pdf_uris = s3.list_pdfs(args.input)

    if not pdf_uris:
        logger.error("❌ No PDF files found in S3 prefix: %s", args.input)
        sys.exit(1)

    logger.info(
        "📂 %d PDFs found, %d workers, starting parallel processing",
        len(pdf_uris),
        args.workers,
    )
    t0 = time.time()
    success, fail = 0, 0

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for i, pdf_uri in enumerate(pdf_uris):
            temp_dir = temp_base / f"worker_{i}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            fut = ex.submit(
                process_single_s3,
                pdf_uri,
                args.output,
                args.model_id,
                args.no_summary,
                args.table_mode,
                temp_dir,
                args.verbose,
            )
            futs[fut] = (pdf_uri, temp_dir)

        for f in as_completed(futs):
            pdf_uri, temp_dir = futs[f]
            try:
                f.result()
                success += 1
            except Exception as e:
                fail += 1
                logger.error("❌ [%s] Error: %s", pdf_uri, e)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    logger.info(
        "🏁 All done: %d/%d succeeded, %d failed (%.1fs total) → %s",
        success,
        len(pdf_uris),
        fail,
        time.time() - t0,
        args.output,
    )


if __name__ == "__main__":
    main()
