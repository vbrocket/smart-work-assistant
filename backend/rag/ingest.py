"""Full ingestion pipeline: load PDF -> section chunk -> table extract -> embed -> store."""

from __future__ import annotations

import logging
import os
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio

from .bm25_store import BM25Store
from .chunker import SectionChunker
from .embedder import Embedder
from .models import ChunkRecord
from .table_extractor import TableExtractor
from .vector_store import VectorStoreBase

_CHARS_PER_TOKEN = 4

logger = logging.getLogger("rag.ingest")


# ── LLM Table Enrichment ──────────────────────────────────────────

_TABLE_ENRICH_PROMPT = """\
أعد كتابة هذا الجدول بالعربية الفصحى الواضحة.
- صحّح أي أخطاء إملائية أو كلمات مقلوبة أو حروف مشوهة
- أضف بجانب كل مسمى وظيفي رسمي المرادفات الشائعة بين قوسين
  مثال: "مدراء الإدارات ومن في حكمهم (مدير إدارة، مدير القسم)"
- أضف بجانب أي مصطلح رسمي غامض شرحاً مختصراً بين قوسين
- حافظ على جميع الأرقام والمبالغ والنسب كما هي بالضبط — لا تغير أي رقم
- حافظ على بنية الجدول (نفس عدد الأعمدة والصفوف)
- أخرج جدول markdown فقط، بدون أي شرح أو مقدمة أو خاتمة

الجدول:
"""


async def _enrich_table_with_llm(llm_provider, raw_text: str) -> Optional[str]:
    """Send a raw table chunk to the LLM for cleanup and synonym enrichment.

    Returns the enriched text, or None on failure.
    """
    try:
        result = await asyncio.wait_for(
            llm_provider.chat(
                messages=[
                    {"role": "system", "content": _TABLE_ENRICH_PROMPT + raw_text},
                    {"role": "user", "content": "أعد كتابة الجدول"},
                ],
                temperature=0.0,
                max_tokens=2048,
                enable_thinking=False,
            ),
            timeout=90.0,
        )
        cleaned = result.strip()
        # Strip Qwen3 chain-of-thought <think>...</think> blocks
        import re as _re
        cleaned = _re.sub(r"<think>.*?</think>\s*", "", cleaned, flags=_re.DOTALL)
        cleaned = cleaned.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        if "|" not in cleaned or len(cleaned) < 20:
            logger.warning("LLM enrichment returned non-table output (%d chars), skipping", len(cleaned))
            return None

        return cleaned
    except asyncio.TimeoutError:
        logger.warning("LLM table enrichment timed out")
        return None
    except Exception as e:
        logger.warning("LLM table enrichment failed: %s", e)
        return None


async def _enrich_all_tables(llm_provider, table_chunks: List[ChunkRecord]) -> int:
    """Enrich all table chunks using the LLM. Returns count of successfully enriched tables."""
    if not table_chunks:
        return 0

    enriched_count = 0
    for i, rec in enumerate(table_chunks):
        logger.info("Enriching table %d/%d (page %d, section %s)...",
                     i + 1, len(table_chunks), rec.page_start, rec.section_id)
        rec.raw_table_text = rec.text

        enriched = await _enrich_table_with_llm(llm_provider, rec.text)
        if enriched:
            prefix_parts = []
            if rec.table_title:
                prefix_parts.append(rec.table_title)
            if rec.section_id:
                prefix_parts.append(f"(القسم {rec.section_id})")
            prefix = " ".join(prefix_parts) + "\n\n" if prefix_parts else ""

            rec.text = prefix + enriched
            enriched_count += 1
            logger.info("  Table %d enriched successfully (%d chars)", i + 1, len(rec.text))
        else:
            logger.info("  Table %d kept raw (enrichment failed)", i + 1)

    return enriched_count

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False


def _fix_reversed_arabic(text: str) -> str:
    """Detect and fix reversed Arabic text extracted from broken PDFs.

    Many Arabic PDFs store text in visual (LTR) word order instead of logical
    (RTL) order.  This results in each line having its words reversed after
    extraction.  We detect this by checking whether common Arabic prefixes
    appear more often at word-starts when the word order is reversed, and if
    so, reverse the word order on every predominantly-Arabic line.
    """

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

    def _word_order_reversed_score(line: str) -> tuple:
        """Return (score_fwd, score_rev) for word-order reversal detection."""
        stripped = line.strip()
        if not stripped or _arabic_ratio(stripped) < 0.3:
            return (0, 0)
        words = stripped.split()
        if len(words) < 2:
            return (0, 0)
        rev_words = list(reversed(words))
        prefixes = ("ال", "و", "ب", "ف", "ل", "ك", "من", "في", "على", "عن")
        # Also check if clause numbers are at the end (reversed) vs beginning
        import re
        clause_re = re.compile(r"^\d+(\.\d+)*$")
        score_fwd = sum(1 for w in words if any(w.startswith(p) for p in prefixes))
        score_rev = sum(1 for w in rev_words if any(w.startswith(p) for p in prefixes))
        # Clause number at end of line (original) means it should be at start
        if words and clause_re.match(words[-1]):
            score_rev += 2
        if words and clause_re.match(words[0]):
            score_fwd += 2
        return (score_fwd, score_rev)

    lines = text.split("\n")
    sample = [l for l in lines if l.strip() and _arabic_ratio(l) > 0.3][:30]
    if not sample:
        return text

    total_fwd = 0
    total_rev = 0
    for l in sample:
        sf, sr = _word_order_reversed_score(l)
        total_fwd += sf
        total_rev += sr

    if total_rev <= total_fwd:
        return text

    logger.info("Detected word-order reversed Arabic text – reversing word order per line")
    fixed = []
    for line in lines:
        stripped = line.strip()
        if stripped and _arabic_ratio(stripped) > 0.3:
            words = stripped.split()
            fixed.append(" ".join(reversed(words)))
        else:
            fixed.append(line)
    return "\n".join(fixed)


def _extract_doc_version(filename: str) -> str:
    """Try to pull a version number from the filename (e.g. '5.0', '1.02')."""
    import re
    m = re.search(r"(\d+\.\d+)", filename)
    return m.group(1) if m else ""


def _build_page_section_map(text_chunks: List[Any]) -> Dict[int, List[str]]:
    """Build a mapping of page_num -> ordered list of section_ids from text chunks.

    Returns all dotted section_ids found on each page in document order.
    The table extractor uses the first (earliest) section as fallback, which
    is more accurate on pages that span multiple top-level sections.
    """
    from collections import defaultdict
    page_sids: Dict[int, List[str]] = defaultdict(list)
    for chunk in text_chunks:
        pg = chunk.page_start
        sid = chunk.section_id
        if pg and sid and "." in sid:
            parts = sid.split(".")
            prefix = ".".join(parts[:2]) if len(parts) >= 2 else sid
            if not page_sids[pg] or page_sids[pg][-1] != prefix:
                page_sids[pg].append(prefix)

    return dict(page_sids)


def _extract_text_excluding_bboxes(page: Any, bboxes: list) -> str:
    """Extract text from a PyMuPDF page, skipping content within table bounding boxes.

    Uses the 'dict' output to get per-block coordinates and filters out any text
    block whose vertical centre falls inside a table region.
    """
    try:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    except Exception:
        return page.get_text("text") or ""

    kept_lines: list = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        bx0, by0, bx1, by1 = block["bbox"]
        block_cy = (by0 + by1) / 2
        in_table = False
        for tb in bboxes:
            tx0, ty0, tx1, ty1 = tb[:4]
            if tx0 <= bx0 and bx1 <= tx1 and ty0 <= block_cy <= ty1:
                in_table = True
                break
        if in_table:
            continue
        for line in block.get("lines", []):
            spans_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if spans_text.strip():
                kept_lines.append(spans_text)

    return "\n".join(kept_lines)


class IngestionPipeline:
    """Orchestrates document loading, chunking, table extraction, embedding, and storage."""

    def __init__(
        self,
        vector_store: VectorStoreBase,
        bm25_store: BM25Store,
        embedder: Embedder,
        documents_dir: str = "documents",
        chunk_max_tokens: int = 1100,
        chunk_overlap_tokens: int = 100,
        llm_provider: Any = None,
    ):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.embedder = embedder
        self.documents_dir = documents_dir
        self.chunker = SectionChunker(
            max_tokens=chunk_max_tokens,
            overlap_tokens=chunk_overlap_tokens,
        )
        self.table_extractor = TableExtractor()
        self._llm_provider = llm_provider
        self._last_ingestion: Optional[str] = None

    async def ingest(self) -> Dict[str, Any]:
        """Run the full ingestion pipeline over all documents."""
        logger.info("Starting ingestion from %s ...", self.documents_dir)

        all_records: List[ChunkRecord] = []
        source_files: List[str] = []

        if not os.path.isdir(self.documents_dir):
            logger.warning("Documents directory not found: %s", self.documents_dir)
            return {"status": "no_documents", "documents": 0, "chunks": 0}

        for filename in sorted(os.listdir(self.documents_dir)):
            filepath = os.path.join(self.documents_dir, filename)
            if not os.path.isfile(filepath):
                continue

            ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
            doc_id = filename
            doc_version = _extract_doc_version(filename)

            if ext == "pdf":
                table_bboxes = self.table_extractor.get_table_bboxes(filepath)
                pages = self._load_pdf(filepath, filename, table_bboxes)
                text_chunks = self.chunker.chunk_pages(
                    pages, doc_id=doc_id, doc_name=filename, doc_version=doc_version,
                )
                page_section_map = _build_page_section_map(text_chunks)
                table_chunks = self.table_extractor.extract(
                    filepath, doc_id=doc_id, doc_name=filename,
                    doc_version=doc_version, page_section_map=page_section_map,
                )
                logger.info("Table enrichment check: llm_provider=%s, table_chunks=%d",
                            type(self._llm_provider).__name__ if self._llm_provider else "None",
                            len(table_chunks))
                if self._llm_provider and table_chunks:
                    n_enriched = await _enrich_all_tables(self._llm_provider, table_chunks)
                    logger.info("LLM-enriched %d/%d tables for %s", n_enriched, len(table_chunks), filename)

                section_titles = self.chunker._build_section_title_map(
                    self.chunker._extract_sections(pages)
                )
                for tc in table_chunks:
                    header = self.chunker._build_context_header(
                        tc.section_id, tc.table_title or tc.section_title, section_titles,
                    )
                    if header and not tc.text.startswith("["):
                        tc.text = f"{header}\n{tc.text}"

                all_records.extend(text_chunks)
                all_records.extend(table_chunks)
                source_files.append(filename)
            elif ext in ("docx",):
                pages = self._load_docx(filepath, filename)
                text_chunks = self.chunker.chunk_pages(
                    pages, doc_id=doc_id, doc_name=filename, doc_version=doc_version,
                )
                all_records.extend(text_chunks)
                source_files.append(filename)
            elif ext in ("txt", "md", "text"):
                pages = self._load_text(filepath, filename)
                text_chunks = self.chunker.chunk_pages(
                    pages, doc_id=doc_id, doc_name=filename, doc_version=doc_version,
                )
                all_records.extend(text_chunks)
                source_files.append(filename)
            else:
                logger.debug("Skipping unsupported file: %s", filename)

        if not all_records:
            return {"status": "no_chunks", "documents": len(source_files), "chunks": 0}

        for rec in all_records:
            rec.ensure_id()

        # Embed all texts
        texts = [r.text for r in all_records]
        embeddings = await self.embedder.embed_texts(texts)

        # Clear and rebuild stores
        self.vector_store.delete_collection()
        self.vector_store.upsert(all_records, embeddings)
        self.bm25_store.build(all_records)

        self._last_ingestion = datetime.utcnow().isoformat()

        # Write marker so startup can detect backend mismatches
        self._write_backend_marker()

        n_text = sum(1 for r in all_records if r.chunk_type == "text_clause")
        n_table = sum(1 for r in all_records if r.chunk_type == "table")
        n_table_row = sum(1 for r in all_records if r.chunk_type == "table_row")

        logger.info(
            "Ingestion complete | files=%d | chunks=%d (text=%d, table=%d, table_row=%d)",
            len(source_files),
            len(all_records),
            n_text,
            n_table,
            n_table_row,
        )

        return {
            "status": "success",
            "documents": len(source_files),
            "chunks": len(all_records),
            "text_chunks": n_text,
            "table_chunks": n_table,
            "table_rows": n_table_row,
            "source_files": source_files,
            "ingested_at": self._last_ingestion,
        }

    # ── Backend marker ─────────────────────────────────────────────

    BACKEND_MARKER = "embed_backend.txt"

    def _marker_path(self) -> str:
        data_dir = os.path.dirname(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "_")
        )
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, self.BACKEND_MARKER)

    def _write_backend_marker(self) -> None:
        from config import get_settings
        s = get_settings()
        backend = s.effective_embed_backend
        if backend == "huggingface":
            model = s.hf_embed_model
        elif backend == "openrouter":
            model = s.or_embed_model
        else:
            model = s.ollama_embed_model
        marker = f"{backend}:{model}"
        try:
            with open(self._marker_path(), "w", encoding="utf-8") as f:
                f.write(marker)
            logger.info("Wrote embedding backend marker: %s", marker)
        except Exception as e:
            logger.warning("Could not write backend marker: %s", e)

    def get_stored_backend(self) -> Optional[str]:
        """Return the backend name that was used for the current stored embeddings."""
        path = self._marker_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        return None

    # ── Status ───────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        doc_files: List[str] = []
        if os.path.isdir(self.documents_dir):
            doc_files = [
                f for f in os.listdir(self.documents_dir)
                if os.path.isfile(os.path.join(self.documents_dir, f))
                and f.rsplit(".", 1)[-1].lower() in ("pdf", "docx", "txt", "md", "text")
            ]

        return {
            "indexed_chunks": self.vector_store.count(),
            "bm25_docs": self.bm25_store.count(),
            "document_files": doc_files,
            "document_count": len(doc_files),
            "last_ingestion": self._last_ingestion,
            "documents_dir": self.documents_dir,
            "embed_backend": self.get_stored_backend(),
        }

    # ── Document loaders ───────────────────────────────────────────

    @staticmethod
    def _load_pdf(
        filepath: str,
        filename: str,
        table_bboxes: Optional[Dict[int, list]] = None,
    ) -> List[Dict[str, Any]]:
        """Load PDF pages, optionally redacting table regions to avoid double-ingestion."""
        if not PYMUPDF_AVAILABLE:
            logger.error("pymupdf not installed – cannot read PDF files")
            return []
        pages = []
        try:
            doc = fitz.open(filepath)
            for i, page in enumerate(doc):
                page_num = i + 1
                bboxes = (table_bboxes or {}).get(page_num, [])
                if bboxes:
                    text = _extract_text_excluding_bboxes(page, bboxes)
                else:
                    text = page.get_text("text") or ""
                if text.strip():
                    text = _fix_reversed_arabic(text)
                    text = unicodedata.normalize("NFKC", text)
                    pages.append({"text": text, "page": page_num})
            doc.close()
        except Exception as e:
            logger.error("Failed to read PDF %s: %s", filename, e)
        return pages

    @staticmethod
    def _load_docx(filepath: str, filename: str) -> List[Dict[str, Any]]:
        if not DOCX_AVAILABLE:
            logger.error("python-docx not installed – cannot read DOCX files")
            return []
        try:
            doc = DocxDocument(filepath)
            full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            if full_text.strip():
                full_text = unicodedata.normalize("NFKC", full_text)
                return [{"text": full_text, "page": 1}]
        except Exception as e:
            logger.error("Failed to read DOCX %s: %s", filename, e)
        return []

    @staticmethod
    def _load_text(filepath: str, filename: str) -> List[Dict[str, Any]]:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
            if text.strip():
                text = unicodedata.normalize("NFKC", text)
                return [{"text": text, "page": 1}]
        except Exception as e:
            logger.error("Failed to read text file %s: %s", filename, e)
        return []
