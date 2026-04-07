"""Hybrid retriever: dense (Qdrant/FAISS) + lexical (BM25), RRF fusion, cross-encoder rerank."""

from __future__ import annotations

import logging
import unicodedata
from typing import Dict, List, Optional

from .bm25_store import BM25Store
from .embedder import Embedder
from .models import DocHit, RetrievalDebug, RetrievalTrace, ScoredHitInfo
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
    dense_weight: float = 0.4,
    bm25_weight: float = 0.6,
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
        max_context_chunks: int = 30,
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

        # 4b) BM25 rescue: ensure top BM25 hits are not dropped by the
        # reranker, since lexical matches are often highly relevant for
        # Arabic policy queries.
        reranked_ids = {h.chunk_id for h in reranked}
        bm25_rescue_budget = 3
        for bm25_hit in bm25_hits[:5]:
            if bm25_rescue_budget <= 0:
                break
            if bm25_hit.chunk_id not in reranked_ids:
                bm25_hit.score = 0.001
                reranked.append(bm25_hit)
                reranked_ids.add(bm25_hit.chunk_id)
                bm25_rescue_budget -= 1

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

    async def retrieve_with_trace(self, query: str) -> tuple[list[DocHit], RetrievalTrace]:
        """Like retrieve(), but returns a full scored trace for the debug UI."""
        import time
        timings: dict = {}
        query = unicodedata.normalize("NFKC", query)

        def _to_info(hits: list[DocHit], limit: int = 15) -> list[ScoredHitInfo]:
            return [
                ScoredHitInfo(
                    chunk_id=h.chunk_id,
                    score=round(h.score, 5),
                    section_id=h.metadata.get("section_id", ""),
                    section_title=h.metadata.get("section_title", ""),
                    page=h.metadata.get("page_start", 0),
                    chunk_type=h.metadata.get("chunk_type", ""),
                    text_preview=h.text[:200],
                )
                for h in hits[:limit]
            ]

        t0 = time.time()
        query_vec = await self.embedder.embed_single(query)
        dense_hits = self.vector_store.search(query_vec, top_k=self.dense_top_k)
        timings["dense_ms"] = round((time.time() - t0) * 1000)

        t0 = time.time()
        bm25_hits = self.bm25_store.search(query, top_k=self.bm25_top_k)
        timings["bm25_ms"] = round((time.time() - t0) * 1000)

        t0 = time.time()
        if self.fusion_method == "weighted":
            fused = weighted_merge(dense_hits, bm25_hits)
        else:
            fused = reciprocal_rank_fusion([dense_hits, bm25_hits])
        timings["fusion_ms"] = round((time.time() - t0) * 1000)

        t0 = time.time()
        candidates = fused[: self.rerank_top_k]
        if self.reranker is not None and candidates:
            reranked = self.reranker.rerank(query, candidates, top_k=self.final_top_k)
        else:
            reranked = candidates[: self.final_top_k]

        # BM25 rescue (same as retrieve())
        reranked_ids = {h.chunk_id for h in reranked}
        bm25_rescue_budget = 3
        for bm25_hit in bm25_hits[:5]:
            if bm25_rescue_budget <= 0:
                break
            if bm25_hit.chunk_id not in reranked_ids:
                bm25_hit.score = 0.001
                reranked.append(bm25_hit)
                reranked_ids.add(bm25_hit.chunk_id)
                bm25_rescue_budget -= 1
        timings["rerank_ms"] = round((time.time() - t0) * 1000)

        t0 = time.time()
        if self.expand_context and reranked:
            final = self._expand_siblings(reranked)
        else:
            final = reranked
        timings["expand_ms"] = round((time.time() - t0) * 1000)

        from .qa import _build_context_block
        context_text = _build_context_block(final)

        trace = RetrievalTrace(
            query=query,
            dense_hits=_to_info(dense_hits),
            bm25_hits=_to_info(bm25_hits),
            fused_hits=_to_info(fused),
            reranked_hits=_to_info(reranked),
            final_hits=_to_info(final),
            context_text=context_text[:3000],
            timing_ms=timings,
        )
        return final, trace

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
        """Expand reranked hits with child/sibling chunks and nearby tables.

        1. Section-based: pull in children/siblings sharing section_id or
           parent_section_id (depth >= 2 to avoid whole top-level sections).
        2. Page-proximity: pull in table chunks within +/- 1 page of any
           reranked hit so relevant tables are not missed.

        Keeps ALL original reranked hits plus fills remaining slots
        (up to max_context_chunks) with the closest siblings/tables,
        sorted by document order.
        """
        original_ids = {h.chunk_id for h in hits}
        seen_ids: set = set(original_ids)
        siblings_pool: List[DocHit] = []

        sections_checked: set = set()
        for hit in hits:
            if hit.score < 0:
                continue
            meta = hit.metadata
            if meta.get("chunk_type", "").startswith("table"):
                continue
            parent = meta.get("parent_section_id", "")
            sid = meta.get("section_id", "")
            doc_id = meta.get("doc_id", "")

            lookups = set()
            sid_depth = len(sid.split(".")) if sid else 0
            if sid and sid not in sections_checked and sid_depth >= 3:
                lookups.add(sid)
            parent_depth = len(parent.split(".")) if parent else 0
            if parent and parent_depth >= 3 and parent not in sections_checked:
                lookups.add(parent)

            for lookup_id in lookups:
                sections_checked.add(lookup_id)
                for sib in self.bm25_store.get_siblings(lookup_id, doc_id=doc_id):
                    if sib.chunk_id not in seen_ids:
                        sib.score = -0.01
                        siblings_pool.append(sib)
                        seen_ids.add(sib.chunk_id)

        pages_checked: set = set()
        hit_section_prefixes: set = set()
        for h in hits:
            if h.score < 0:
                continue
            sid = h.metadata.get("section_id", "")
            if sid:
                parts = sid.split(".")
                if len(parts) >= 3:
                    hit_section_prefixes.add(".".join(parts[:3]))
        table_count = 0
        max_tables = 3
        for hit in hits:
            if table_count >= max_tables:
                break
            if hit.score < 0:
                continue
            meta = hit.metadata
            page = meta.get("page_start", 0)
            doc_id = meta.get("doc_id", "")
            if page and page not in pages_checked:
                pages_checked.add(page)
                for tbl_hit in self.bm25_store.get_nearby_tables(page, doc_id=doc_id):
                    if table_count >= max_tables:
                        break
                    if tbl_hit.chunk_id in seen_ids:
                        continue
                    tbl_sid = tbl_hit.metadata.get("section_id", "")
                    if tbl_sid:
                        tbl_parts = tbl_sid.split(".")
                        tbl_prefix = ".".join(tbl_parts[:3]) if len(tbl_parts) >= 3 else ".".join(tbl_parts[:2]) if len(tbl_parts) >= 2 else ""
                        if not tbl_prefix or tbl_prefix not in hit_section_prefixes:
                            continue
                    tbl_hit.score = -0.02
                    siblings_pool.append(tbl_hit)
                    seen_ids.add(tbl_hit.chunk_id)
                    table_count += 1

        hit_pages = {h.metadata.get("page_start", 0) for h in hits}

        def _sibling_sort_key(h: DocHit) -> tuple:
            pg = h.metadata.get("page_start", 0)
            page_dist = min((abs(pg - hp) for hp in hit_pages), default=999)
            is_table = 0 if h.metadata.get("chunk_type", "").startswith("table") else 1
            return (page_dist, is_table, self._section_parts(h.metadata.get("section_id", "")))

        siblings_pool.sort(key=_sibling_sort_key)

        remaining_slots = max(0, self.max_context_chunks - len(hits))

        # Prioritize children of higher-scored reranked hits by giving each
        # parent section a fair share of sibling slots.
        selected_siblings: List[DocHit] = []
        if remaining_slots > 0 and siblings_pool:
            by_parent: dict[str, list[DocHit]] = {}
            for sib in siblings_pool:
                sid = sib.metadata.get("section_id", "")
                parent = ".".join(sid.split(".")[:3]) if len(sid.split(".")) >= 3 else sid.split(".")[0]
                by_parent.setdefault(parent, []).append(sib)

            hit_scores: dict[str, float] = {}
            for h in hits:
                sid = h.metadata.get("section_id", "")
                parent = ".".join(sid.split(".")[:3]) if len(sid.split(".")) >= 3 else sid.split(".")[0]
                hit_scores[parent] = max(hit_scores.get(parent, 0), h.score)

            parents_ordered = sorted(by_parent.keys(), key=lambda p: hit_scores.get(p, 0), reverse=True)
            per_parent = max(5, remaining_slots // max(len(parents_ordered), 1))
            seen_selected: set = set()
            for parent in parents_ordered:
                for sib in by_parent[parent][:per_parent]:
                    if len(selected_siblings) >= remaining_slots:
                        break
                    if sib.chunk_id not in seen_selected:
                        selected_siblings.append(sib)
                        seen_selected.add(sib.chunk_id)
                if len(selected_siblings) >= remaining_slots:
                    break
            # Fill any remaining slots from leftover siblings
            for sib in siblings_pool:
                if len(selected_siblings) >= remaining_slots:
                    break
                if sib.chunk_id not in seen_selected:
                    selected_siblings.append(sib)
                    seen_selected.add(sib.chunk_id)

        combined = list(hits) + selected_siblings
        combined.sort(key=lambda h: self._section_parts(h.metadata.get("section_id", "")))

        n_tables = sum(1 for s in selected_siblings if s.metadata.get("chunk_type", "").startswith("table"))
        logger.debug(
            "Context expansion: %d original + %d siblings (%d tables) -> %d total",
            len(hits), len(selected_siblings), n_tables, len(combined),
        )
        return combined
