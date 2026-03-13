"""Cross-page table merger for Docling OCR output.

Docling OCR processes PDFs page-by-page, so tables spanning multiple pages
are split into separate tables. This module detects and merges them by
analyzing the markdown output.

Detection criteria:
  1. Adjacent pages (page N bottom table + page N+1 top table)
  2. Same column count
  3. Second table has no header (separator row) -> likely a continuation
  4. Header similarity check for tables that both have headers

Usage:
    from pdf_parser.table_merger import merge_cross_page_tables

    raw_md = parsed_doc.export_to_markdown(page_break_placeholder="<!-- page-break -->")
    merged_md, stats = merge_cross_page_tables(raw_md)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("pdf_parser.table_merger")

PAGE_BREAK = "<!-- page-break -->"

# Separator row pattern: | --- | --- | or | :---: | ---: |
_SEP_RE = re.compile(
    r"^\|[\s\-:]+(?:\|[\s\-:]+)+\|?\s*$"
)


@dataclass
class TableBlock:
    """A markdown table found in one page."""

    content: str
    page_num: int          # 0-based
    start_line: int        # line index within the page
    end_line: int
    rows: list[str] = field(default_factory=list)
    has_header: bool = False

    def __post_init__(self):
        self.rows = [
            line.strip()
            for line in self.content.strip().splitlines()
            if line.strip().startswith("|")
        ]
        # Header exists if 2nd or 3rd row is a separator
        for row in self.rows[:3]:
            if _SEP_RE.match(row):
                self.has_header = True
                break

    @property
    def col_count(self) -> int:
        if not self.rows:
            return 0
        return max(row.count("|") - 1 for row in self.rows)

    @property
    def header_row(self) -> Optional[str]:
        return self.rows[0] if self.rows else None

    @property
    def data_rows(self) -> list[str]:
        """Return rows excluding header and separator."""
        if not self.has_header or len(self.rows) < 2:
            return self.rows
        # Find separator index
        for i, row in enumerate(self.rows):
            if _SEP_RE.match(row):
                return self.rows[i + 1:]
        return self.rows[1:]

    @property
    def is_at_page_bottom(self) -> bool:
        """Heuristic: table is near the bottom of the page content."""
        return True  # Will be checked by position in page lines

    @property
    def is_at_page_top(self) -> bool:
        """Heuristic: table starts near the top of the page content."""
        return self.start_line <= 3  # Within first few lines


def _extract_tables_from_page(page_content: str, page_num: int) -> list[TableBlock]:
    """Extract all markdown tables from a single page's content."""
    tables = []
    lines = page_content.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("|") and "|" in line[1:]:
            table_start = i
            table_lines = [lines[i]]
            i += 1

            while i < len(lines):
                next_line = lines[i].strip()
                if next_line.startswith("|") and "|" in next_line[1:]:
                    table_lines.append(lines[i])
                    i += 1
                elif next_line == "":
                    # Look ahead: if next non-empty line is also a table row, continue
                    lookahead = i + 1
                    while lookahead < len(lines) and lines[lookahead].strip() == "":
                        lookahead += 1
                    if lookahead < len(lines) and lines[lookahead].strip().startswith("|"):
                        table_lines.append(lines[i])
                        i += 1
                    else:
                        break
                else:
                    break

            tables.append(TableBlock(
                content="\n".join(table_lines),
                page_num=page_num,
                start_line=table_start,
                end_line=i - 1,
            ))
        else:
            i += 1

    return tables


def _header_similarity(h1: str, h2: str) -> float:
    """Jaccard similarity between two header row strings."""
    if not h1 or not h2:
        return 0.0

    # Normalize: lowercase, split by |, strip whitespace
    cells1 = {c.strip().lower() for c in h1.split("|") if c.strip()}
    cells2 = {c.strip().lower() for c in h2.split("|") if c.strip()}

    if not cells1 or not cells2:
        return 0.0

    intersection = len(cells1 & cells2)
    union = len(cells1 | cells2)
    return intersection / union if union > 0 else 0.0


def _can_merge(t1: TableBlock, t2: TableBlock) -> bool:
    """Determine if t2 is a continuation of t1 across a page break."""
    # Must be consecutive pages
    if t2.page_num != t1.page_num + 1:
        return False

    # Must have same column count
    if t1.col_count != t2.col_count:
        return False

    # t2 should be near the top of its page
    if not t2.is_at_page_top:
        return False

    # Case 1: t2 has no header -> strong signal it's a continuation
    if not t2.has_header:
        return True

    # Case 2: Both have headers with high similarity -> repeated header across pages
    if t1.has_header and t2.has_header:
        sim = _header_similarity(t1.header_row, t2.header_row)
        if sim > 0.7:
            logger.info(
                "Header similarity %.2f between page %d and %d -> merge",
                sim, t1.page_num + 1, t2.page_num + 1,
            )
            return True

    return False


def _merge_two_tables(t1: TableBlock, t2: TableBlock) -> str:
    """Merge t2 into t1, returning combined markdown table."""
    rows1 = t1.rows.copy()
    rows2_data = t2.data_rows  # Excludes duplicated header/separator

    # If t2 has no header, use all rows
    if not t2.has_header:
        rows2_data = t2.rows

    return "\n".join(rows1 + rows2_data)


def merge_cross_page_tables(
    markdown: str,
    page_separator: str = PAGE_BREAK,
) -> tuple[str, dict]:
    """Detect and merge cross-page tables in Docling markdown output.

    Args:
        markdown: Full markdown string with page-break placeholders.
        page_separator: The string used to separate pages.

    Returns:
        (merged_markdown, stats) where stats contains merge details.
    """
    pages = markdown.split(page_separator)

    # Extract tables from all pages
    all_tables: list[TableBlock] = []
    for page_num, page_content in enumerate(pages):
        page_tables = _extract_tables_from_page(page_content, page_num)
        all_tables.extend(page_tables)

    if not all_tables:
        return markdown, {"total_tables": 0, "merged_groups": 0, "details": []}

    # Find merge groups: chain of tables that should be merged
    merge_groups: list[list[int]] = []
    merged_indices: set[int] = set()

    for i in range(len(all_tables)):
        if i in merged_indices:
            continue

        # Get last table on this page
        current = all_tables[i]
        group = [i]

        # Look for continuation on next page(s)
        for j in range(i + 1, len(all_tables)):
            if j in merged_indices:
                continue
            candidate = all_tables[j]
            prev = all_tables[group[-1]]

            if _can_merge(prev, candidate):
                group.append(j)
                merged_indices.add(j)
            elif candidate.page_num > prev.page_num + 1:
                break  # No point looking further

        if len(group) > 1:
            merge_groups.append(group)
            merged_indices.update(group)

    # Build stats
    stats = {
        "total_tables": len(all_tables),
        "merged_groups": len(merge_groups),
        "details": [],
    }

    if not merge_groups:
        logger.info("No cross-page tables detected (%d tables total)", len(all_tables))
        return markdown, stats

    # Apply merges page by page
    # Track which tables to replace / remove per page
    # Key: page_num -> list of (table_block, action)
    #   action: "replace" with merged content, or "remove"
    page_actions: dict[int, list[tuple[TableBlock, str, str]]] = {}

    for group in merge_groups:
        tables_in_group = [all_tables[idx] for idx in group]

        # Build merged table content
        merged_content = tables_in_group[0].content
        for next_table in tables_in_group[1:]:
            merged_content = _merge_two_tables(
                TableBlock(merged_content, 0, 0, 0),
                next_table,
            )

        # First table in group: replace with merged content
        first = tables_in_group[0]
        page_actions.setdefault(first.page_num, []).append(
            (first, "replace", merged_content)
        )

        # Subsequent tables: remove
        for t in tables_in_group[1:]:
            page_actions.setdefault(t.page_num, []).append(
                (t, "remove", "")
            )

        # Stats
        header_preview = first.header_row or "N/A"
        if len(header_preview) > 60:
            header_preview = header_preview[:60] + "..."

        detail = {
            "from_page": first.page_num + 1,
            "to_page": tables_in_group[-1].page_num + 1,
            "tables_merged": len(tables_in_group),
            "header_preview": header_preview,
        }
        stats["details"].append(detail)
        logger.info(
            "Merging %d tables across pages %d-%d: %s",
            len(tables_in_group), detail["from_page"], detail["to_page"],
            header_preview,
        )

    # Rebuild pages with modifications
    result_pages = []
    for page_num, page_content in enumerate(pages):
        if page_num not in page_actions:
            result_pages.append(page_content)
            continue

        lines = page_content.split("\n")
        actions = sorted(page_actions[page_num], key=lambda a: a[0].start_line, reverse=True)

        for table_block, action, replacement in actions:
            start = table_block.start_line
            end = table_block.end_line + 1

            if action == "replace":
                lines[start:end] = replacement.split("\n")
            elif action == "remove":
                lines[start:end] = []

        result_pages.append("\n".join(lines))

    merged_markdown = page_separator.join(result_pages)
    return merged_markdown, stats
