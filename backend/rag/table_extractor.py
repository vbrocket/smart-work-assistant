"""Extract tables from PDF pages and convert each table into a searchable ChunkRecord.

Uses PyMuPDF's find_tables() with multiple strategies, fixes RTL Arabic text in
cells, and serialises each table as a complete Markdown block (not row-per-chunk)
so the full table context is preserved for retrieval.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import List, Optional, Tuple

from .models import ChunkRecord

logger = logging.getLogger("rag.table_extractor")

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

# Approximate token counter (1 token ~ 4 chars for Arabic)
_CHARS_PER_TOKEN = 4
_MAX_TABLE_TOKENS = 1800


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _is_arabic_char(ch: str) -> bool:
    try:
        return "ARABIC" in unicodedata.name(ch, "")
    except ValueError:
        return False


def _arabic_ratio(s: str) -> float:
    alpha = [c for c in s if c.isalpha()]
    if not alpha:
        return 0.0
    return sum(1 for c in alpha if _is_arabic_char(c)) / len(alpha)


_ARABIC_FIXES = [
    (re.compile(r"اإل"), "الإ"),
    (re.compile(r"\bيف\b"), "في"),
    (re.compile(r"\bىلع\b"), "على"),
    (re.compile(r"\bنم\b"), "من"),
    (re.compile(r"\bىلإ\b"), "إلى"),
    (re.compile(r"\bنأ\b"), "أن"),
    (re.compile(r"\bنإ\b"), "إن"),
    (re.compile(r"\bريغ\b"), "غير"),
    (re.compile(r"\bعم\b"), "مع"),
    (re.compile(r"\bدق\b"), "قد"),
    (re.compile(r"\bلك\b"), "كل"),
    (re.compile(r"\bدعب\b"), "بعد"),
    (re.compile(r"\bلبق\b"), "قبل"),
    (re.compile(r"\bنيب\b"), "بين"),
    (re.compile(r"\bىتح\b"), "حتى"),
]


def _normalize_arabic_text(text: str) -> str:
    """Fix common PyMuPDF Arabic extraction artifacts (broken lam-alef, reversed short words)."""
    if not text or _arabic_ratio(text) < 0.2:
        return text
    for pattern, replacement in _ARABIC_FIXES:
        text = pattern.sub(replacement, text)
    return text


def _fix_cell_text(text: str) -> str:
    """Normalise and fix character-reversed Arabic within a single table cell.

    PyMuPDF extracts Arabic PDF table cells in visual LTR order, reversing both
    character order within words AND word order.  We reverse chars per-word and
    then reverse the overall word order to restore correct RTL reading order.
    Also collapses whitespace in purely numeric cells (e.g. "1 1" -> "11").
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.strip())

    collapsed = re.sub(r"\s+", "", text)
    if collapsed and all(c.isdigit() or c in ".,%+-" for c in collapsed):
        return collapsed

    if _arabic_ratio(text) < 0.3:
        return text

    lines = text.split("\n")
    fixed_lines = []
    for line in lines:
        words = line.split()
        fixed_words = []
        for w in words:
            if _arabic_ratio(w) > 0.4:
                fixed_words.append(w[::-1])
            else:
                fixed_words.append(w)
        fixed_words.reverse()
        fixed_lines.append(" ".join(fixed_words))
    return " ".join(fixed_lines)


def _table_to_markdown(headers: List[str], rows: List[List[str]]) -> str:
    """Render a table as a Markdown table string."""
    if not headers and not rows:
        return ""

    clean_headers = [h if h else f"col{i}" for i, h in enumerate(headers)]

    md_lines = []
    md_lines.append("| " + " | ".join(clean_headers) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(clean_headers)) + " |")

    for row in rows:
        cells = row + [""] * (len(clean_headers) - len(row))
        cells = cells[:len(clean_headers)]
        escaped = [c.replace("|", "/").replace("\n", " ") for c in cells]
        md_lines.append("| " + " | ".join(escaped) + " |")

    return "\n".join(md_lines)


_HEADER_NOISE_RE = re.compile(r"(دليل|صفحة|الصفحة|ليلد|UG-)", re.IGNORECASE)


def _guess_table_context(page_text: str, table_bbox_y0: float) -> Tuple[str, str]:
    """Find the section heading and title text above a table.

    Returns (section_id, context_text).  context_text is the nearest
    non-empty line above the table bounding box.
    Ignores single-number IDs (e.g. '65') that come from page headers/footers.
    """
    lines = page_text.split("\n")
    section_id = ""
    context_line = ""
    clause_re = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+")

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not context_line and len(stripped) < 150:
            if not _HEADER_NOISE_RE.search(stripped):
                context_line = stripped
        m = clause_re.match(stripped)
        if m:
            candidate = m.group(1)
            if "." not in candidate:
                if _HEADER_NOISE_RE.search(stripped):
                    continue
                try:
                    if int(candidate) > 20:
                        continue
                except ValueError:
                    pass
            section_id = candidate
            if not context_line:
                context_line = stripped
            break

    return section_id, context_line


def _split_large_table(
    headers: List[str],
    rows: List[List[str]],
    title: str,
    max_tokens: int = _MAX_TABLE_TOKENS,
) -> List[str]:
    """If a table is too large, split into multiple markdown blocks with headers repeated."""
    full_md = _table_to_markdown(headers, rows)
    if _estimate_tokens(full_md) <= max_tokens:
        return [full_md]

    chunks = []
    current_rows: List[List[str]] = []
    current_tokens = _estimate_tokens(_table_to_markdown(headers, []))

    for row in rows:
        row_md = "| " + " | ".join(row) + " |"
        row_tokens = _estimate_tokens(row_md)

        if current_tokens + row_tokens > max_tokens and current_rows:
            chunks.append(_table_to_markdown(headers, current_rows))
            current_rows = []
            current_tokens = _estimate_tokens(_table_to_markdown(headers, []))

        current_rows.append(row)
        current_tokens += row_tokens

    if current_rows:
        chunks.append(_table_to_markdown(headers, current_rows))

    return chunks


def _build_table_title(headers: List[str], section_id: str, page_num: int) -> str:
    """Build a clean, readable title from the table headers and section context."""
    meaningful = [h for h in headers if h and not h.startswith("col")]
    if meaningful:
        title = "جدول: " + " | ".join(meaningful[:4])
    else:
        title = f"جدول صفحة {page_num}"
    return title


class TableExtractor:
    """Extract tables from a PDF and yield whole-table ChunkRecords."""

    def extract(
        self,
        pdf_path: str,
        doc_id: str = "",
        doc_name: str = "",
        doc_version: str = "",
        page_section_map: Optional[dict] = None,
    ) -> List[ChunkRecord]:
        if not PYMUPDF_AVAILABLE:
            logger.warning("PyMuPDF not available; skipping table extraction")
            return []

        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.error("Failed to open PDF for table extraction: %s", e)
            return []

        records: List[ChunkRecord] = []
        table_idx = 0

        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            try:
                tables = page.find_tables()
            except Exception:
                continue

            if not tables or not tables.tables:
                continue

            page_text = page.get_text("text") or ""

            for tbl in tables.tables:
                try:
                    data = tbl.extract()
                except Exception:
                    continue

                if not data or len(data) < 2:
                    continue

                non_empty_rows = [
                    r for r in data[1:]
                    if any((c or "").strip() for c in r)
                ]
                if not non_empty_rows:
                    continue

                headers = [
                    _normalize_arabic_text(_fix_cell_text(str(c))) if c else f"col{i}"
                    for i, c in enumerate(data[0])
                ]
                rows = [
                    [_normalize_arabic_text(_fix_cell_text(str(c))) if c else "" for c in row]
                    for row in non_empty_rows
                ]

                bbox_y0 = tbl.bbox[1] if hasattr(tbl, "bbox") else 0
                section_id, context_line = _guess_table_context(page_text, bbox_y0)

                if not section_id or ("." not in section_id):
                    fallback_list = (page_section_map or {}).get(page_num, [])
                    if isinstance(fallback_list, str):
                        fallback_list = [fallback_list] if fallback_list else []
                    if fallback_list:
                        fallback = fallback_list[0]
                        if "." in fallback or not section_id:
                            logger.debug(
                                "Table on page %d: using fallback section_id '%s' from %s (was '%s')",
                                page_num, fallback, fallback_list, section_id,
                            )
                            section_id = fallback

                table_title = _build_table_title(headers, section_id, page_num)

                md_chunks = _split_large_table(headers, rows, table_title)

                for chunk_i, md_text in enumerate(md_chunks):
                    prefix_parts = []
                    if table_title:
                        prefix_parts.append(table_title)
                    if section_id:
                        prefix_parts.append(f"(القسم {section_id})")

                    full_text = ""
                    if prefix_parts:
                        full_text = " ".join(prefix_parts) + "\n\n"
                    full_text += md_text

                    sub_id = f"T{table_idx}" if len(md_chunks) == 1 else f"T{table_idx}{chr(65 + chunk_i)}"

                    rec = ChunkRecord(
                        text=full_text,
                        doc_id=doc_id,
                        doc_name=doc_name,
                        doc_version=doc_version,
                        section_id=section_id,
                        section_title=table_title,
                        page_start=page_num,
                        page_end=page_num,
                        chunk_type="table",
                        table_title=table_title,
                        row_index=table_idx,
                        sub_id=sub_id,
                        parent_section_id=section_id.rsplit(".", 1)[0] if "." in section_id else "",
                    )
                    rec.ensure_id()
                    records.append(rec)

                table_idx += 1

        doc.close()
        logger.info("Extracted %d table chunks from %d tables in %s", len(records), table_idx, doc_name)
        return records

    def get_table_bboxes(self, pdf_path: str) -> dict:
        """Return {page_num: [bbox_list]} for all detected tables.

        Used by the ingestion pipeline to exclude table regions from text extraction.
        """
        if not PYMUPDF_AVAILABLE:
            return {}

        result: dict = {}
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            return {}

        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            try:
                tables = page.find_tables()
            except Exception:
                continue
            if tables and tables.tables:
                bboxes = []
                for tbl in tables.tables:
                    if hasattr(tbl, "bbox"):
                        bboxes.append(tbl.bbox)
                if bboxes:
                    result[page_num] = bboxes

        doc.close()
        return result
