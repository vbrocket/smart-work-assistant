"""Pydantic models for the RAG pipeline: chunks, hits, citations, QA response."""

from __future__ import annotations

import hashlib
import uuid
from typing import List, Optional

from pydantic import BaseModel, Field


class ChunkRecord(BaseModel):
    """A single chunk (text clause or table row) with full metadata."""

    id: str = ""
    text: str
    doc_id: str = ""
    doc_name: str = ""
    doc_version: str = ""
    section_id: str = ""
    section_title: str = ""
    page_start: int = 0
    page_end: int = 0
    chunk_type: str = "text_clause"
    parent_section_id: str = ""
    sub_id: str = ""
    token_span: str = ""
    table_title: str = ""
    row_index: int = -1

    def stable_id(self) -> str:
        """Deterministic UUID derived from document + section + sub + row."""
        key = f"{self.doc_id}|{self.section_id}|{self.sub_id}|{self.row_index}"
        return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))

    def ensure_id(self) -> None:
        if not self.id:
            self.id = self.stable_id()

    def payload_dict(self) -> dict:
        """Return metadata dict suitable for Qdrant payload / BM25 store."""
        return self.model_dump(exclude={"text"})


class DocHit(BaseModel):
    """A single retrieval hit with score."""

    chunk_id: str
    text: str
    score: float = 0.0
    metadata: dict = Field(default_factory=dict)


class Citation(BaseModel):
    section_id: str = ""
    section_title: str = ""
    page: int = 0
    quote: str = ""


class RetrievalDebug(BaseModel):
    dense_top: List[str] = Field(default_factory=list)
    bm25_top: List[str] = Field(default_factory=list)
    fused_top: List[str] = Field(default_factory=list)
    reranked_top: List[str] = Field(default_factory=list)


class QAResponse(BaseModel):
    answer_ar: str = ""
    citations: List[Citation] = Field(default_factory=list)
    confidence: str = "low"
    retrieval_debug: Optional[RetrievalDebug] = None
