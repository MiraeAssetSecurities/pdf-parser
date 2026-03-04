"""
PDF 파싱 결과(_final.md)를 읽어 페이지/이미지/테이블 요약을 Bedrock으로 생성.

LangGraph 흐름 대응:
  split_pdf → layout_analyze → extract_elements
    → (image_crop, table_crop, extract_text) → page_summary
    → (image_summary, table_summary) → table_markdown → END

Strands는 graph 없이 함수 파이프라인으로 구현.
"""

import asyncio
import base64
import re
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel

load_dotenv()

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
REGION = "us-east-1"
OUTPUT_DIR = Path("output")

bedrock = BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.3, streaming=False)

# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------
@dataclass
class PageData:
    page_no: int
    text: str
    images: list[dict] = field(default_factory=list)   # {tag, path, bbox}
    tables: list[dict] = field(default_factory=list)   # {tag, md_path, img_path, bbox}


@dataclass
class SummaryResult:
    page_no: int
    page_summary: str = ""
    image_summaries: list[dict] = field(default_factory=list)  # {tag, summary}
    table_summaries: list[dict] = field(default_factory=list)  # {tag, summary, markdown}


# ---------------------------------------------------------------------------
# 1단계: _final.md 파싱 → PageData 목록
# ---------------------------------------------------------------------------
def parse_final_md(final_md_path: Path) -> list[PageData]:
    """_final.md를 파싱해 페이지별 PageData 반환."""
    content = final_md_path.read_text(encoding="utf-8")
    pages = []

    for page_block in re.finditer(
        r"\[page-(\d+)\](.*?)(?=\[page-\d+\]|\Z)", content, re.DOTALL
    ):
        page_no = int(page_block.group(1))
        body = page_block.group(2)

        # 이미지 추출
        images = []
        for m in re.finditer(
            r"<(figure-\d+-\w+)>\s*(<bbox[^>]+></bbox>)\s*!\[.*?\]\((.+?)\)\s*</figure-[^>]+>",
            body,
        ):
            images.append({"tag": m.group(1), "bbox": m.group(2), "path": OUTPUT_DIR / m.group(3)})

        # 테이블 추출
        tables = []
        for m in re.finditer(
            r"!\[(table-\d+)\]\((.+?)\)\s*<table-\d+>\s*(<bbox[^>]+></bbox>)\s*\n((?:\|.+\n?)+)",
            body,
        ):
            tables.append({
                "tag": m.group(1),
                "img_path": OUTPUT_DIR / m.group(2),
                "bbox": m.group(3),
                "markdown": m.group(4).strip(),
            })

        # 순수 텍스트 (figure/table 블록 제거)
        text = re.sub(r"<figure-[^>]+>.*?</figure-[^>]+>", "", body, flags=re.DOTALL)
        text = re.sub(r"!\[table-\d+\]\(.+?\)\s*<table-\d+>.*?</table-\d+>", "", text, flags=re.DOTALL)
        text = text.strip()

        pages.append(PageData(page_no=page_no, text=text, images=images, tables=tables))

    return pages


# ---------------------------------------------------------------------------
# 2단계: 이미지 → base64
# ---------------------------------------------------------------------------
def _img_to_b64(path: Path) -> str | None:
    if path.exists():
        return base64.standard_b64encode(path.read_bytes()).decode()
    return None


# ---------------------------------------------------------------------------
# 3단계: Bedrock 호출 헬퍼 (async)
# ---------------------------------------------------------------------------
async def _call_text(prompt: str) -> str:
    agent = Agent(model=bedrock)
    return re.sub(r"^#{1,6}\s+.+\n?", "", str(await agent.invoke_async(prompt)), flags=re.MULTILINE).strip()


async def _call_vision(prompt: str, img_b64: str) -> str:
    agent = Agent(model=bedrock)
    return re.sub(r"^#{1,6}\s+.+\n?", "", str(await agent.invoke_async([
        {"image": {"format": "png", "source": {"bytes": base64.b64decode(img_b64)}}},
        {"text": prompt},
    ])), flags=re.MULTILINE).strip()


# ---------------------------------------------------------------------------
# 4단계: 페이지 요약 (텍스트 + 이미지 + 테이블 컨텍스트)
# ---------------------------------------------------------------------------
async def summarize_page(page: PageData) -> str:
    context = page.text
    for t in page.tables:
        context += f"\n\n[테이블]\n{t['markdown']}"
    prompt = (
        "다음은 금융 문서의 한 페이지 내용입니다. "
        "핵심 내용을 3~5문장으로 한국어로 요약해주세요.\n\n"
        f"{context}"
    )
    return await _call_text(prompt)


# ---------------------------------------------------------------------------
# 5단계: 이미지 요약
# ---------------------------------------------------------------------------
async def summarize_image(img_info: dict) -> dict:
    b64 = _img_to_b64(img_info["path"])
    if b64 is None:
        return {"tag": img_info["tag"], "summary": "(이미지 파일 없음)"}
    summary = await _call_vision(
        "이 금융 문서 이미지의 내용을 2~3문장으로 한국어로 설명해주세요.", b64
    )
    return {"tag": img_info["tag"], "summary": summary}


# ---------------------------------------------------------------------------
# 6단계: 테이블 요약
# ---------------------------------------------------------------------------
async def summarize_table(tbl: dict) -> dict:
    b64 = _img_to_b64(tbl["img_path"])
    if b64:
        summary = await _call_vision(
            "이 금융 테이블의 주요 수치와 의미를 2~3문장으로 한국어로 설명해주세요.", b64
        )
    else:
        summary = await _call_text(
            "다음 마크다운 테이블의 주요 수치와 의미를 2~3문장으로 한국어로 설명해주세요.\n\n"
            f"{tbl['markdown']}"
        )
    return {"tag": tbl["tag"], "summary": summary, "markdown": tbl["markdown"]}


# ---------------------------------------------------------------------------
# 7단계: 결과 저장
# ---------------------------------------------------------------------------
def save_summaries(results: list[SummaryResult], out_path: Path) -> None:
    lines = []
    for r in results:
        lines.append(f"## 페이지 {r.page_no:03d}\n")
        lines.append(f"### 페이지 요약\n{r.page_summary}\n")

        if r.image_summaries:
            lines.append("### 이미지 요약")
            for img in r.image_summaries:
                lines.append(f"**{img['tag']}**: {img['summary']}")
            lines.append("")

        if r.table_summaries:
            lines.append("### 테이블 요약")
            for tbl in r.table_summaries:
                lines.append(f"**{tbl['tag']}**: {tbl['summary']}")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"요약 저장: {out_path}")


# ---------------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------------
async def _process_page(page: PageData) -> SummaryResult:
    """한 페이지의 텍스트/이미지/테이블 요약을 병렬 실행."""
    print(f"  [페이지 {page.page_no}] 요약 생성 중...")
    tasks = [summarize_page(page)] + \
            [summarize_image(img) for img in page.images] + \
            [summarize_table(tbl) for tbl in page.tables]

    results = await asyncio.gather(*tasks)

    result = SummaryResult(page_no=page.page_no)
    result.page_summary = results[0]
    n_img = len(page.images)
    result.image_summaries = list(results[1:1 + n_img])
    result.table_summaries = list(results[1 + n_img:])
    return result


def save_enriched(final_md_path: Path, results: list[SummaryResult]) -> None:
    """_final.md에 요약 태그를 삽입한 _enriched.md 저장."""
    content = final_md_path.read_text(encoding="utf-8")

    summary_map: dict[str, str] = {}
    for r in results:
        for img in r.image_summaries:
            summary_map[img["tag"]] = img["summary"]
        for tbl in r.table_summaries:
            summary_map[tbl["tag"]] = tbl["summary"]

    def inject(tag: str, original: str) -> str:
        summary = summary_map.get(tag, "")
        return f"<{tag}-summary>{summary}</{tag}-summary>\n{original}" if summary else original

    content = re.sub(
        r"(<(figure-\d+-\w+)>)",
        lambda m: inject(m.group(2), m.group(0)),
        content,
    )
    content = re.sub(
        r"(<(table-\d+)>)",
        lambda m: inject(m.group(2), m.group(0)),
        content,
    )

    out_path = final_md_path.parent / final_md_path.name.replace("_final.md", "_enriched.md")
    out_path.write_text(content, encoding="utf-8")
    print(f"enriched 저장: {out_path}")


async def _run_async(final_md_path: Path) -> list[SummaryResult]:
    print(f"파싱 중: {final_md_path}")
    pages = parse_final_md(final_md_path)
    print(f"  {len(pages)}개 페이지 감지")

    results = await asyncio.gather(*[_process_page(p) for p in pages])
    results = sorted(results, key=lambda r: r.page_no)

    out_path = final_md_path.parent / final_md_path.name.replace("_final.md", "_summary.md")
    save_summaries(results, out_path)
    save_enriched(final_md_path, results)
    return results


def run(final_md_path: Path) -> list[SummaryResult]:
    return asyncio.run(_run_async(final_md_path))


if __name__ == "__main__":
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else next(OUTPUT_DIR.glob("*_final.md"), None)
    if path is None:
        print("사용법: python summarizer.py <path_to_final.md>")
        sys.exit(1)
    run(path)
