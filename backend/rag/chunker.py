"""Section-based chunker for Arabic policy documents.

Detects numbered clauses and key Arabic headings, creating one chunk per clause.
Handles sub-splitting for oversized clauses and never mixes section IDs.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from .models import ChunkRecord

logger = logging.getLogger("rag.chunker")

# ── Regex patterns for clause / section detection ──────────────────────

# Multi-level numbering: 4.4.7.2, 1.2, etc.
RE_MULTI_LEVEL = re.compile(r"^\s*(\d+(?:\.\d+)+)\s+")
# Single-level numbering: "1 -", "2–", "3 "
RE_SINGLE_LEVEL = re.compile(r"^\s*(\d+)\s*[\-\u2013]?\s+")

# Key Arabic headings that mark new sections
ARABIC_HEADINGS = [
    "الغرض",
    "نطاق",
    "التعريفات",
    "بيان السياسة",
    "المسؤوليات",
    "المرفقات",
    "الأهداف",
    "الإجراءات",
    "النطاق",
    "المقدمة",
    "التطبيق",
    "الاستثناءات",
    "الشروط",
    "الأحكام العامة",
    "المراجع",
    "الصلاحيات",
]

_HEADING_PATTERN = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in ARABIC_HEADINGS) + r")\s*[:.]?\s*$",
    re.MULTILINE,
)

# Approximate token counter (rough: 1 token ≈ 4 chars for Arabic)
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _split_sentences(text: str) -> List[str]:
    """Split Arabic / English text into sentences."""
    parts = re.split(r"(?<=[.؟!。\n])\s+", text)
    return [p for p in parts if p.strip()]


_RE_NUMBER_THEN_CLAUSE = re.compile(
    r"^\s*\d+\s+(\d+(?:\.\d+)+)\s+"
)


def _detect_section(line: str) -> Optional[Tuple[str, str]]:
    """Return (section_id, rest_of_line) if this line starts a new section."""
    m = RE_MULTI_LEVEL.match(line)
    if m:
        return m.group(1), line[m.end():].strip()
    # Handle PDF lines like "200 4.4.3.2.1 ريال ..." where a value
    # precedes the real clause number.
    m = _RE_NUMBER_THEN_CLAUSE.match(line)
    if m:
        return m.group(1), line[m.end():].strip()
    m = RE_SINGLE_LEVEL.match(line)
    if m:
        num = int(m.group(1))
        if num <= 30:
            return m.group(1), line[m.end():].strip()
    m = _HEADING_PATTERN.match(line)
    if m:
        return m.group(1), ""
    return None


def _parent_section(sid: str) -> str:
    parts = sid.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else ""


def _sub_split(
    text: str,
    section_id: str,
    max_tokens: int,
    overlap_tokens: int,
) -> List[Tuple[str, str, str]]:
    """Split text into sub-chunks within the same section_id.

    Returns list of (sub_id_letter, sub_text, token_span_str).
    """
    sentences = _split_sentences(text)
    if not sentences:
        return [("A", text, f"0-{_estimate_tokens(text)}")]

    chunks: List[Tuple[str, str, str]] = []
    current_sents: List[str] = []
    current_len = 0
    letter_idx = 0
    token_offset = 0

    for sent in sentences:
        sent_tokens = _estimate_tokens(sent)
        if current_len + sent_tokens > max_tokens and current_sents:
            chunk_text = " ".join(current_sents)
            sub_letter = chr(ord("A") + letter_idx)
            span = f"{token_offset}-{token_offset + current_len}"
            chunks.append((sub_letter, chunk_text, span))
            token_offset += current_len

            # Overlap: keep trailing sentences up to overlap_tokens
            overlap_sents: List[str] = []
            overlap_len = 0
            for s in reversed(current_sents):
                st = _estimate_tokens(s)
                if overlap_len + st > overlap_tokens:
                    break
                overlap_sents.insert(0, s)
                overlap_len += st
            token_offset -= overlap_len

            current_sents = overlap_sents[:]
            current_len = overlap_len
            letter_idx += 1

        current_sents.append(sent)
        current_len += sent_tokens

    if current_sents:
        chunk_text = " ".join(current_sents)
        sub_letter = chr(ord("A") + letter_idx)
        span = f"{token_offset}-{token_offset + current_len}"
        chunks.append((sub_letter, chunk_text, span))

    return chunks


MIN_CHUNK_TOKENS = 60


def _section_depth(sid: str) -> int:
    """Return nesting depth: '4' -> 1, '4.4' -> 2, '4.4.3' -> 3, etc."""
    if not sid or not sid[0].isdigit():
        return 0
    return len(sid.split("."))


class SectionChunker:
    """Parse policy document pages into section-based chunks.

    After initial per-clause splitting, a merge pass combines tiny sibling
    sub-clauses under their parent section when the combined text fits
    within max_tokens.  This prevents single-line clauses like 4.4.3.2.1
    from becoming isolated chunks that are too small for meaningful retrieval.
    """

    def __init__(
        self,
        max_tokens: int = 1100,
        overlap_tokens: int = 100,
        min_chunk_tokens: int = MIN_CHUNK_TOKENS,
    ):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.min_chunk_tokens = min_chunk_tokens

    def chunk_pages(
        self,
        pages: List[dict],
        doc_id: str = "",
        doc_name: str = "",
        doc_version: str = "",
    ) -> List[ChunkRecord]:
        """Convert a list of page dicts (text, page) into ChunkRecords.

        Each page dict has keys: text (str), page (int).
        """
        sections = self._extract_sections(pages)
        merged = self._merge_small_siblings(sections)

        section_titles = self._build_section_title_map(sections)

        records: List[ChunkRecord] = []

        for sec in merged:
            header = self._build_context_header(
                sec["section_id"], sec["section_title"], section_titles,
            )
            body = sec["text"]
            contextualized = f"{header}\n{body}" if header else body

            tok_est = _estimate_tokens(contextualized)
            if tok_est <= self.max_tokens:
                rec = ChunkRecord(
                    text=contextualized,
                    doc_id=doc_id,
                    doc_name=doc_name,
                    doc_version=doc_version,
                    section_id=sec["section_id"],
                    section_title=sec["section_title"],
                    page_start=sec["page_start"],
                    page_end=sec["page_end"],
                    chunk_type="text_clause",
                    parent_section_id=_parent_section(sec["section_id"]),
                )
                rec.ensure_id()
                records.append(rec)
            else:
                subs = _sub_split(
                    body,
                    sec["section_id"],
                    self.max_tokens - _estimate_tokens(header) - 5,
                    self.overlap_tokens,
                )
                for sub_letter, sub_text, span in subs:
                    rec = ChunkRecord(
                        text=f"{header}\n{sub_text}" if header else sub_text,
                        doc_id=doc_id,
                        doc_name=doc_name,
                        doc_version=doc_version,
                        section_id=sec["section_id"],
                        section_title=sec["section_title"],
                        page_start=sec["page_start"],
                        page_end=sec["page_end"],
                        chunk_type="text_clause",
                        parent_section_id=_parent_section(sec["section_id"]),
                        sub_id=sub_letter,
                        token_span=span,
                    )
                    rec.ensure_id()
                    records.append(rec)

        logger.info(
            "Chunked %d pages -> %d sections -> %d merged -> %d records (doc=%s)",
            len(pages), len(sections), len(merged), len(records), doc_name,
        )
        return records

    @staticmethod
    def _build_section_title_map(sections: List[dict]) -> dict:
        """Collect section_id -> title from all sections before merging."""
        title_map: dict = {}
        for sec in sections:
            sid = sec["section_id"]
            title = sec.get("section_title", "")
            if sid and title and sid != title:
                title_map[sid] = title
        return title_map

    @staticmethod
    def _build_context_header(
        section_id: str,
        section_title: str,
        title_map: dict,
    ) -> str:
        """Build a breadcrumb header like: [القسم 1.3 | الإجازات > 1.3.1 | الإجازة السنوية]"""
        if not section_id or not section_id[0].isdigit():
            return ""

        parts = section_id.split(".")
        if len(parts) < 2:
            return ""

        breadcrumbs: List[str] = []
        for depth in range(1, len(parts)):
            ancestor_id = ".".join(parts[:depth])
            ancestor_title = title_map.get(ancestor_id, "")
            if ancestor_title:
                breadcrumbs.append(f"{ancestor_id} {ancestor_title}")
            else:
                breadcrumbs.append(ancestor_id)

        current_label = f"{section_id} {section_title}" if section_title and section_title != section_id else section_id
        breadcrumbs.append(current_label)

        return "[" + " > ".join(breadcrumbs) + "]"

    def _merge_small_siblings(self, sections: List[dict]) -> List[dict]:
        """Merge consecutive tiny sibling sections under the same parent.

        Example: if sections 4.4.3.2.1, 4.4.3.2.2, 4.4.3.2.3 each have < min_chunk_tokens
        and share parent 4.4.3.2, combine them into a single chunk under section_id 4.4.3.2
        as long as the total stays within max_tokens.
        """
        if not sections:
            return sections

        merged: List[dict] = []
        buf: List[dict] = []
        buf_parent: str = ""
        buf_tokens: int = 0

        def _flush():
            nonlocal buf, buf_parent, buf_tokens
            if not buf:
                return
            if len(buf) == 1:
                merged.append(buf[0])
            else:
                combined_text = "\n".join(s["text"] for s in buf)
                parent_sid = buf_parent
                parent_title = ""
                for s in buf:
                    if s["section_id"] == parent_sid:
                        parent_title = s["section_title"]
                        break
                if not parent_title:
                    parent_title = buf[0]["section_title"]

                merged.append({
                    "section_id": parent_sid,
                    "section_title": parent_title,
                    "text": combined_text,
                    "page_start": buf[0]["page_start"],
                    "page_end": buf[-1]["page_end"],
                })
            buf = []
            buf_parent = ""
            buf_tokens = 0

        for sec in sections:
            sid = sec["section_id"]
            parent = _parent_section(sid)
            tok = _estimate_tokens(sec["text"])

            is_small = tok < self.min_chunk_tokens
            same_parent = parent and parent == buf_parent
            would_fit = buf_tokens + tok <= self.max_tokens

            if is_small and same_parent and would_fit:
                buf.append(sec)
                buf_tokens += tok
            elif is_small and not buf and parent:
                buf = [sec]
                buf_parent = parent
                buf_tokens = tok
            else:
                _flush()
                if is_small and parent:
                    buf = [sec]
                    buf_parent = parent
                    buf_tokens = tok
                else:
                    merged.append(sec)

        _flush()
        return merged

    def _extract_sections(self, pages: List[dict]) -> List[dict]:
        """Walk page lines and group into sections."""
        sections: List[dict] = []
        current_id = "0"
        current_title = ""
        current_lines: List[str] = []
        current_page_start = 1
        current_page_end = 1

        for page_info in pages:
            page_num = page_info.get("page", 1)
            text = page_info.get("text", "")

            for line in text.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue

                det = _detect_section(stripped)
                if det is not None:
                    if current_lines:
                        sections.append({
                            "section_id": current_id,
                            "section_title": current_title,
                            "text": "\n".join(current_lines),
                            "page_start": current_page_start,
                            "page_end": current_page_end,
                        })
                    current_id, rest = det
                    current_title = rest or current_id
                    current_lines = [stripped]
                    current_page_start = page_num
                    current_page_end = page_num
                else:
                    current_lines.append(stripped)
                    current_page_end = page_num

        if current_lines:
            sections.append({
                "section_id": current_id,
                "section_title": current_title,
                "text": "\n".join(current_lines),
                "page_start": current_page_start,
                "page_end": current_page_end,
            })

        return sections
