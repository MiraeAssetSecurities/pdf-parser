"""PDF 파서 CLI — 단일 PDF 또는 폴더 일괄 병렬 파싱.

사용법:
    # 단일 PDF
    python run.py sample.pdf -o output

    # 폴더 일괄 처리 (PDF 병렬 파싱)
    python run.py ./pdfs/ -o output --workers 4

    # LLM 요약 비활성화 (Docling 추출만)
    python run.py sample.pdf -o output --no-summary

    # Bedrock 모델 변경 (서울 리전)
    python run.py sample.pdf -o output --model-id ap-northeast-2.anthropic.claude-haiku-4-5-20251001-v1:0
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger("pdf_parser")


def _setup_logging(verbose: bool = False):
    fmt = "%(asctime)s %(levelname)-5s %(name)s — %(message)s"
    logging.basicConfig(level=logging.WARNING, format=fmt, datefmt="%H:%M:%S")
    # 우리 로거만 INFO/DEBUG로 설정
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def parse_single(
    pdf_path: Path,
    output_dir: Path,
    model_id: str,
    no_summary: bool,
    table_mode: str,
    verbose: bool = False,
    use_accelerator: bool = False,
) -> Path:
    """단일 PDF 파싱 → 최종 마크다운 저장. 반환: 저장 경로."""
    # 자식 프로세스에서도 로깅 설정 필요
    _setup_logging(verbose)
    from src.converter import DoclingConverter
    from src.summarizer import BedrockSummarizer
    from src.markdown_builder import MarkdownBuilder

    pdf_path = Path(pdf_path)
    name = pdf_path.name
    doc_output = output_dir / pdf_path.stem
    doc_output.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1) Docling 변환
    logger.info("📄 [%s] Docling conversion started (accelerator=%s)", name, use_accelerator)
    converter = DoclingConverter(table_mode=table_mode, use_accelerator=use_accelerator)
    parsed = converter.convert(pdf_path)
    n_pages = len(parsed.doc.pages)
    n_figs = len(parsed.get_figures())
    n_tbls = len(parsed.get_tables())
    logger.info(
        "✅ [%s] Conversion done — %d pages, %d figures, %d tables (%.1fs)",
        name, n_pages, n_figs, n_tbls, time.time() - t0,
    )

    # 2) 에셋 저장
    logger.info("💾 [%s] Saving assets (table md/img, figure img)", name)
    parsed.save_assets(doc_output)

    # 2.5) 원본 텍스트 마크다운 저장
    text_path = doc_output / f"{parsed.doc_name}_text.md"
    text_path.write_text(parsed.doc.export_to_markdown(), encoding="utf-8")
    logger.info("📃 [%s] Raw text saved → %s", name, text_path)

    # 3) LLM 요약
    page_summaries, image_summaries, table_summaries = {}, {}, {}
    if not no_summary:
        summarizer = BedrockSummarizer(model_id=model_id)

        logger.info("🔍 [%s] Summarizing pages... (%d pages)", name, n_pages)
        t1 = time.time()
        page_summaries = summarizer.summarize_pages(parsed)
        logger.info("✅ [%s] Page summaries done (%.1fs)", name, time.time() - t1)

        logger.info("🖼️  [%s] Summarizing figures... (%d)", name, n_figs)
        t1 = time.time()
        image_summaries = summarizer.summarize_figures(parsed, page_summaries)
        logger.info("✅ [%s] Figure summaries done (%.1fs)", name, time.time() - t1)

        logger.info("📊 [%s] Summarizing tables... (%d)", name, n_tbls)
        t1 = time.time()
        table_summaries = summarizer.summarize_tables(parsed, page_summaries)
        logger.info("✅ [%s] Table summaries done (%.1fs)", name, time.time() - t1)

    # 4) 최종 마크다운 조립
    logger.info("📝 [%s] Building final markdown", name)
    builder = MarkdownBuilder(parsed, doc_output)
    final_md = builder.build(page_summaries, image_summaries, table_summaries)

    out_path = doc_output / f"{parsed.doc_name}_final.md"
    out_path.write_text(final_md, encoding="utf-8")
    elapsed = time.time() - t0
    logger.info("🎉 [%s] Done → %s (%.1fs total)", name, out_path, elapsed)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Docling PDF 파서 + Bedrock LLM 요약")
    parser.add_argument("input", type=Path, help="PDF 파일 또는 PDF가 들어있는 폴더 경로")
    parser.add_argument("-o", "--output", type=Path, default=Path("output"), help="출력 디렉토리 (기본: output)")
    parser.add_argument("--workers", type=int, default=2, help="폴더 모드 시 PDF 병렬 처리 수 (기본: 2)")
    parser.add_argument("--no-summary", action="store_true", help="LLM 요약 비활성화")
    parser.add_argument("--model-id", default="ap-northeast-2.anthropic.claude-haiku-4-5-20251001-v1:0", help="Bedrock 모델 ID")
    parser.add_argument("--table-mode", choices=["accurate", "fast"], default="accurate", help="TableFormer 모드")
    parser.add_argument("--use-accelerator", action="store_true", help="CPU accelerator 활성화 (num_threads=4)")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG 로그 출력")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    args.output.mkdir(parents=True, exist_ok=True)

    # 단일 PDF
    if args.input.is_file():
        parse_single(args.input, args.output, args.model_id, args.no_summary, args.table_mode, args.verbose, args.use_accelerator)
        return

    # 폴더 일괄 처리
    if args.input.is_dir():
        pdfs = sorted(args.input.glob("*.pdf"))
        if not pdfs:
            logger.error("❌ No PDF files found in folder: %s", args.input)
            sys.exit(1)

        logger.info("📂 %d PDFs found, %d workers, starting parallel processing", len(pdfs), args.workers)
        t0 = time.time()
        success, fail = 0, 0

        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {
                ex.submit(parse_single, p, args.output, args.model_id, args.no_summary, args.table_mode, args.verbose, args.use_accelerator): p
                for p in pdfs
            }
            for f in as_completed(futs):
                pdf = futs[f]
                try:
                    f.result()
                    success += 1
                except Exception as e:
                    fail += 1
                    logger.error("❌ [%s] Error: %s", pdf.name, e)

        logger.info(
            "🏁 All done: %d/%d succeeded, %d failed (%.1fs total) → %s",
            success, len(pdfs), fail, time.time() - t0, args.output,
        )
        return

    logger.error("❌ Input path does not exist: %s", args.input)
    sys.exit(1)


if __name__ == "__main__":
    main()
