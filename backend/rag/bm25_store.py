"""Persistent BM25 lexical index over the same corpus as the vector store.

Uses rank_bm25.BM25Okapi with Arabic-normalised tokenisation.
The index is persisted to disk via pickle so server restarts do not rebuild.
"""

from __future__ import annotations

import logging
import os
import pickle
from typing import Dict, List, Optional

from .arabic_utils import tokenize_arabic
from .models import ChunkRecord, DocHit

logger = logging.getLogger("rag.bm25_store")


class BM25Store:
    """Lightweight BM25 index with disk persistence."""

    def __init__(self, index_path: str = "data/bm25_index.pkl"):
        self._index_path = index_path
        self._bm25 = None
        self._corpus_tokens: List[List[str]] = []
        self._doc_ids: List[str] = []
        self._doc_texts: List[str] = []
        self._doc_meta: List[dict] = []
        self._load()

    # ── Persistence ────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(self._index_path):
            return
        try:
            with open(self._index_path, "rb") as f:
                data = pickle.load(f)
            self._bm25 = data["bm25"]
            self._corpus_tokens = data["corpus_tokens"]
            self._doc_ids = data["doc_ids"]
            self._doc_texts = data["doc_texts"]
            self._doc_meta = data["doc_meta"]
            logger.info("Loaded BM25 index from %s (%d docs)", self._index_path, len(self._doc_ids))
        except Exception as e:
            logger.warning("Could not load BM25 index: %s", e)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._index_path), exist_ok=True)
        with open(self._index_path, "wb") as f:
            pickle.dump(
                {
                    "bm25": self._bm25,
                    "corpus_tokens": self._corpus_tokens,
                    "doc_ids": self._doc_ids,
                    "doc_texts": self._doc_texts,
                    "doc_meta": self._doc_meta,
                },
                f,
            )
        logger.info("Saved BM25 index to %s (%d docs)", self._index_path, len(self._doc_ids))

    # ── Build / Update ─────────────────────────────────────────────

    def build(self, records: List[ChunkRecord]) -> int:
        """(Re)build the BM25 index from scratch."""
        from rank_bm25 import BM25Okapi

        self._corpus_tokens = []
        self._doc_ids = []
        self._doc_texts = []
        self._doc_meta = []

        for rec in records:
            rec.ensure_id()
            tokens = tokenize_arabic(rec.text)
            self._corpus_tokens.append(tokens)
            self._doc_ids.append(rec.id)
            self._doc_texts.append(rec.text)
            self._doc_meta.append(rec.payload_dict())

        if self._corpus_tokens:
            self._bm25 = BM25Okapi(self._corpus_tokens)
        else:
            self._bm25 = None

        self._save()
        logger.info("Built BM25 index with %d documents", len(self._doc_ids))
        return len(self._doc_ids)

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 30) -> List[DocHit]:
        if self._bm25 is None or not self._doc_ids:
            return []

        query_tokens = tokenize_arabic(query)
        scores = self._bm25.get_scores(query_tokens)

        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        hits: List[DocHit] = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            hits.append(
                DocHit(
                    chunk_id=self._doc_ids[idx],
                    text=self._doc_texts[idx],
                    score=float(scores[idx]),
                    metadata=self._doc_meta[idx],
                )
            )
        return hits

    def get_siblings(self, section_id: str, doc_id: str = "") -> List[DocHit]:
        """Return chunks whose section_id starts with the given prefix."""
        prefix = section_id + "."
        hits: List[DocHit] = []
        for i, meta in enumerate(self._doc_meta):
            sid = meta.get("section_id", "")
            if sid == section_id or sid.startswith(prefix):
                if doc_id and meta.get("doc_id", "") != doc_id:
                    continue
                hits.append(DocHit(
                    chunk_id=self._doc_ids[i],
                    text=self._doc_texts[i],
                    score=0.0,
                    metadata=meta,
                ))
        return hits

    def get_nearby_tables(self, page: int, doc_id: str = "", page_margin: int = 1) -> List[DocHit]:
        """Return table-type chunks within +/- page_margin of the given page."""
        hits: List[DocHit] = []
        for i, meta in enumerate(self._doc_meta):
            if meta.get("chunk_type") not in ("table", "table_row"):
                continue
            pg = meta.get("page_start", 0)
            if abs(pg - page) > page_margin:
                continue
            if doc_id and meta.get("doc_id", "") != doc_id:
                continue
            hits.append(DocHit(
                chunk_id=self._doc_ids[i],
                text=self._doc_texts[i],
                score=0.0,
                metadata=meta,
            ))
        return hits

    def count(self) -> int:
        return len(self._doc_ids)
