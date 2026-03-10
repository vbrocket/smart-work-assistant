"""Hybrid retriever: dense (Qdrant/FAISS) + lexical (BM25), RRF fusion, cross-encoder rerank."""

from __future__ import annotations

import logging
import unicodedata
from typing import Dict, List, Optional

from .bm25_store import BM25Store
from .embedder import Embedder
from .models import DocHit, RetrievalDebug
from .reranker import Reranker
from .vector_store import VectorStoreBase

logger = logging.getLogger("rag.retriever")


def reciprocal_rank_fusion(
    result_lists: List[List[DocHit]],
    k: int = 60,
) -> List[DocHit]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion.

    RRF score for doc d = sum over lists L of  1 / (k + rank_L(d))
    """
    scores: Dict[str, float] = {}
    docs_by_id: Dict[str, DocHit] = {}

    for hits in result_lists:
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            if hit.chunk_id not in docs_by_id:
                docs_by_id[hit.chunk_id] = hit

    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    fused: List[DocHit] = []
    for cid in sorted_ids:
        doc = docs_by_id[cid]
        doc.score = scores[cid]
        fused.append(doc)
    return fused


def weighted_merge(
    dense_hits: List[DocHit],
    bm25_hits: List[DocHit],
    dense_weight: float = 0.6,
    bm25_weight: float = 0.4,
) -> List[DocHit]:
    """Merge two lists with normalised-score weighted combination."""
    def _normalize(hits: List[DocHit]) -> None:
        if not hits:
            return
        max_s = max(h.score for h in hits) or 1.0
        min_s = min(h.score for h in hits)
        rng = max_s - min_s or 1.0
        for h in hits:
            h.score = (h.score - min_s) / rng

    _normalize(dense_hits)
    _normalize(bm25_hits)

    scores: Dict[str, float] = {}
    docs_by_id: Dict[str, DocHit] = {}
    for h in dense_hits:
        scores[h.chunk_id] = dense_weight * h.score
        docs_by_id[h.chunk_id] = h
    for h in bm25_hits:
        scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + bm25_weight * h.score
        if h.chunk_id not in docs_by_id:
            docs_by_id[h.chunk_id] = h

    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    merged: List[DocHit] = []
    for cid in sorted_ids:
        doc = docs_by_id[cid]
        doc.score = scores[cid]
        merged.append(doc)
    return merged


class HybridRetriever:
    """Orchestrates dense + BM25 retrieval, fusion, reranking, and context expansion."""

    def __init__(
        self,
        vector_store: VectorStoreBase,
        bm25_store: BM25Store,
        embedder: Embedder,
        reranker: Optional[Reranker] = None,
        dense_top_k: int = 30,
        bm25_top_k: int = 30,
        rerank_top_k: int = 20,
        final_top_k: int = 8,
        fusion_method: str = "rrf",
        expand_context: bool = True,
        max_context_chunks: int = 10,
    ):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.embedder = embedder
        self.reranker = reranker
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.rerank_top_k = rerank_top_k
        self.final_top_k = final_top_k
        self.fusion_method = fusion_method
        self.expand_context = expand_context
        self.max_context_chunks = max_context_chunks

    async def retrieve(self, query: str) -> tuple[List[DocHit], RetrievalDebug]:
        """Run the full hybrid retrieval pipeline and return hits + debug info."""
        query = unicodedata.normalize("NFKC", query)

        # 1) Dense search
        query_vec = await self.embedder.embed_single(query)
        dense_hits = self.vector_store.search(query_vec, top_k=self.dense_top_k)
        logger.debug("Dense search returned %d hits", len(dense_hits))

        # 2) BM25 search
        bm25_hits = self.bm25_store.search(query, top_k=self.bm25_top_k)
        logger.debug("BM25 search returned %d hits", len(bm25_hits))

        # 3) Fusion
        if self.fusion_method == "weighted":
            fused = weighted_merge(dense_hits, bm25_hits)
        else:
            fused = reciprocal_rank_fusion([dense_hits, bm25_hits])
        logger.debug("Fused results: %d hits", len(fused))

        # 4) Rerank
        candidates = fused[: self.rerank_top_k]
        if self.reranker is not None and candidates:
            reranked = self.reranker.rerank(query, candidates, top_k=self.final_top_k)
        else:
            reranked = candidates[: self.final_top_k]
        logger.debug(
            "Reranked top sids: %s",
            [(h.metadata.get("section_id", "?"), f"{h.score:.3f}") for h in reranked],
        )

        # 5) Context expansion: pull in sibling/child chunks that share the
        #    same parent section as the top hits so the QA engine sees the
        #    full picture (e.g. all sub-clauses of 4.4.3 when one was hit).
        if self.expand_context and reranked:
            reranked = self._expand_siblings(reranked)

        logger.info(
            "Retrieval complete | dense=%d bm25=%d fused=%d reranked=%d",
            len(dense_hits), len(bm25_hits), len(fused), len(reranked),
        )

        debug = RetrievalDebug(
            dense_top=[h.chunk_id for h in dense_hits[:10]],
            bm25_top=[h.chunk_id for h in bm25_hits[:10]],
            fused_top=[h.chunk_id for h in fused[:10]],
            reranked_top=[h.chunk_id for h in reranked],
        )
        return reranked, debug

    @staticmethod
    def _section_parts(sid: str) -> tuple:
        parts = []
        for p in sid.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    def _expand_siblings(self, hits: List[DocHit]) -> List[DocHit]:
        """Expand reranked hits with child/sibling chunks.

        Only expands using the hit's own section_id (to find children) and
        parent_section_id when deep enough (depth >= 2) to avoid pulling in
        entire top-level sections.

        After expansion, keeps ALL original reranked hits plus fills remaining
        slots (up to max_context_chunks) with the closest siblings, sorted by
        document order.
        """
        original_ids = {h.chunk_id for h in hits}
        seen_ids: set = set(original_ids)
        siblings_pool: List[DocHit] = []

        sections_checked: set = set()
        for hit in hits:
            meta = hit.metadata
            parent = meta.get("parent_section_id", "")
            sid = meta.get("section_id", "")
            doc_id = meta.get("doc_id", "")

            lookups = set()
            if sid and sid not in sections_checked:
                lookups.add(sid)
            parent_depth = len(parent.split(".")) if parent else 0
            if parent and parent_depth >= 2 and parent not in sections_checked:
                lookups.add(parent)

            for lookup_id in lookups:
                sections_checked.add(lookup_id)
                for sib in self.bm25_store.get_siblings(lookup_id, doc_id=doc_id):
                    if sib.chunk_id not in seen_ids:
                        sib.score = -0.01
                        siblings_pool.append(sib)
                        seen_ids.add(sib.chunk_id)

        siblings_pool.sort(key=lambda h: self._section_parts(h.metadata.get("section_id", "")))

        remaining_slots = max(0, self.max_context_chunks - len(hits))
        selected_siblings = siblings_pool[:remaining_slots]

        combined = list(hits) + selected_siblings
        combined.sort(key=lambda h: self._section_parts(h.metadata.get("section_id", "")))

        logger.debug(
            "Context expansion: %d original + %d siblings -> %d total",
            len(hits), len(selected_siblings), len(combined),
        )
        return combined
