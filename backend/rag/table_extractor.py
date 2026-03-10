"""Extract tables from PDF pages and convert each row into a searchable ChunkRecord."""

from __future__ import annotations

import logging
import unicodedata
from typing import List, Optional

from .models import ChunkRecord

logger = logging.getLogger("rag.table_extractor")

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False


def _row_to_kv_text(headers: List[str], row: List[str], table_title: str = "") -> str:
    """Convert a table row into normalized key-value text.

    E.g. "Table: بدل النقل | Grade: 55 | Max: 1200 | Notes: ..."
    """
    parts: List[str] = []
    if table_title:
        parts.append(f"Table: {table_title}")
    for h, v in zip(headers, row):
        h_clean = h.strip() if h else f"col"
        v_clean = v.strip() if v else ""
        if v_clean:
            parts.append(f"{h_clean}: {v_clean}")
    return " | ".join(parts)


def _guess_table_title(page_text: str, table_bbox_y0: float) -> str:
    """Heuristic: look for text just above the table bounding box."""
    lines = page_text.split("\n")
    candidate = ""
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            candidate = stripped
            break
    if len(candidate) > 120:
        return ""
    return candidate


class TableExtractor:
    """Extract tables from a PDF file and yield ChunkRecords for each row."""

    def extract(
        self,
        pdf_path: str,
        doc_id: str = "",
        doc_name: str = "",
        doc_version: str = "",
    ) -> List[ChunkRecord]:
        if not PYMUPDF_AVAILABLE:
            logger.warning("PyMuPDF not available; skipping table extraction")
            return []

        records: List[ChunkRecord] = []

        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.error("Failed to open PDF for table extraction: %s", e)
            return []

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

                headers = [unicodedata.normalize("NFKC", str(c)) if c else f"col{i}" for i, c in enumerate(data[0])]
                raw_title = _guess_table_title(page_text, tbl.bbox[1]) if hasattr(tbl, "bbox") else ""
                table_title = unicodedata.normalize("NFKC", raw_title)

                for row_idx, row in enumerate(data[1:], start=1):
                    cells = [unicodedata.normalize("NFKC", str(c)) if c else "" for c in row]
                    if not any(c.strip() for c in cells):
                        continue

                    text = _row_to_kv_text(headers, cells, table_title)
                    rec = ChunkRecord(
                        text=text,
                        doc_id=doc_id,
                        doc_name=doc_name,
                        doc_version=doc_version,
                        section_id="",
                        section_title="",
                        page_start=page_num,
                        page_end=page_num,
                        chunk_type="table_row",
                        table_title=table_title,
                        row_index=row_idx,
                    )
                    rec.ensure_id()
                    records.append(rec)

        doc.close()
        logger.info("Extracted %d table rows from %s", len(records), doc_name)
        return records
