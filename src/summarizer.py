"""Bedrock 멀티모달 LLM을 이용한 페이지/이미지/테이블 요약."""

from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import boto3

from .utils import get_location

logger = logging.getLogger("pdf_parser.summarizer")

SUMMARY_PROMPT = """Analyze this image and respond ONLY with the JSON below. No other text.
{
  "summary": "3-5 sentence summary of key content (in Korean)",
  "entities": ["entity1", "entity2", ...]
}
For entities, extract key named entities such as person names, organization names, product names, technical terms, and numerical metrics.
The page-level summary context may be provided below. Use it to produce a more accurate summary and entity extraction.
IMPORTANT: Write the summary value in Korean."""

TABLE_SUMMARY_PROMPT = """Analyze this table image and respond ONLY with the JSON below. No other text.
{
  "summary": "2-3 sentence summary of the table content (in Korean)",
  "entities": ["entity1", "entity2", ...],
  "category": "table_category"
}
For entities, extract key named entities such as person names, organization names, product names, technical terms, and numerical metrics from the table.
For category, choose exactly one from: financial_statement, comparison, statistics, performance_metrics, configuration, schedule, pricing, inventory, survey_results, reference, other
The page-level summary context may be provided below. Use it to produce a more accurate summary and entity extraction.
IMPORTANT: Write the summary value in Korean."""


class BedrockSummarizer:
    """Bedrock Claude 멀티모달 LLM으로 요약 생성."""

    def __init__(
        self,
        model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        max_workers: int = 10,
        region_name: str | None = None,
    ):
        self.model_id = model_id
        self.max_workers = max_workers
        kwargs = {}
        if region_name:
            kwargs["region_name"] = region_name
        self._client = boto3.client("bedrock-runtime", **kwargs)

    def _call_vision(self, img_pil, prompt: str) -> dict:
        """PIL 이미지 + 프롬프트 → JSON dict 반환."""
        buf = BytesIO()
        img_pil.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        resp = self._client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            }),
        )
        text = json.loads(resp["body"].read())["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)

    def _get_page_context(self, element, doc, page_summaries: dict) -> str:
        loc = get_location(element, doc)
        if not loc:
            return ""
        info = page_summaries.get(loc["page_no"], {})
        s = info.get("summary", "") if isinstance(info, dict) else ""
        return f"\n\n[Page {loc['page_no']} context] {s}" if s else ""

    # ------------------------------------------------------------------
    def summarize_pages(self, parsed_doc) -> dict[int, dict]:
        """페이지별 요약을 병렬 생성."""
        doc = parsed_doc.doc
        total = len(doc.pages)
        results: dict[int, dict] = {}

        def _work(pn, page):
            return pn, self._call_vision(page.image.pil_image, SUMMARY_PROMPT)

        with ThreadPoolExecutor(max_workers=min(total, self.max_workers)) as ex:
            futs = {ex.submit(_work, pn, pg): pn for pn, pg in doc.pages.items()}
            for f in as_completed(futs):
                pn = futs[f]
                try:
                    page_no, res = f.result()
                    results[page_no] = res
                    logger.debug("  📄 Page %d/%d summary done", page_no, total)
                except Exception as e:
                    results[pn] = {"summary": f"Summary generation failed: {e}", "entities": []}
                    logger.warning("  ⚠️ Page %d/%d summary failed: %s", pn, total, e)
        return results

    # ------------------------------------------------------------------
    def summarize_figures(self, parsed_doc, page_summaries: dict) -> dict[int, dict]:
        """Figure별 요약을 병렬 생성."""
        doc = parsed_doc.doc
        figures = parsed_doc.get_figures()
        total = len(figures)
        results: dict[int, dict] = {}

        def _work(idx, element):
            img = element.get_image(doc)
            if img is None:
                return idx, {"summary": "No image", "entities": []}
            ctx = self._get_page_context(element, doc, page_summaries)
            return idx, self._call_vision(img, SUMMARY_PROMPT + ctx)

        with ThreadPoolExecutor(max_workers=min(total or 1, self.max_workers)) as ex:
            futs = {ex.submit(_work, idx, el): idx for idx, el, _ in figures}
            for f in as_completed(futs):
                i = futs[f]
                try:
                    idx, res = f.result()
                    results[idx] = res
                    logger.debug("  🖼️  Figure %d/%d summary done", idx, total)
                except Exception as e:
                    results[i] = {"summary": f"Summary generation failed: {e}", "entities": []}
                    logger.warning("  ⚠️ Figure %d/%d summary failed: %s", i, total, e)
        return results

    # ------------------------------------------------------------------
    def summarize_tables(self, parsed_doc, page_summaries: dict) -> dict[int, dict]:
        """테이블별 요약을 병렬 생성."""
        doc = parsed_doc.doc
        tables = parsed_doc.get_tables()
        total = len(tables)
        results: dict[int, dict] = {}

        def _work(idx, element):
            img = element.get_image(doc)
            if img is None:
                return idx, {"summary": "No image", "entities": [], "category": "other"}
            ctx = self._get_page_context(element, doc, page_summaries)
            return idx, self._call_vision(img, TABLE_SUMMARY_PROMPT + ctx)

        with ThreadPoolExecutor(max_workers=min(total or 1, self.max_workers)) as ex:
            futs = {ex.submit(_work, idx, el): idx for idx, el in tables}
            for f in as_completed(futs):
                i = futs[f]
                try:
                    idx, res = f.result()
                    results[idx] = res
                    logger.debug("  📊 Table %d/%d summary done", idx, total)
                except Exception as e:
                    results[i] = {"summary": f"Summary generation failed: {e}", "entities": [], "category": "other"}
                    logger.warning("  ⚠️ Table %d/%d summary failed: %s", i, total, e)
        return results
