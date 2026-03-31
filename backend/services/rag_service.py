"""RAG Service - Facade that delegates to the new backend/rag/ package.

Keeps the same singleton API (get_rag_service()) so existing callers (routers,
voice handler) continue to work without changes to their import paths.
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional

from config import get_settings
from rag.bm25_store import BM25Store
from rag.embedder import create_embedder
from rag.ingest import IngestionPipeline
from rag.models import DocHit, QAResponse, RetrievalTrace
from rag.qa import QAEngine
from rag.reranker import get_reranker
from rag.retriever import HybridRetriever
from rag.vector_store import create_vector_store
from services.llm_provider import create_llm_provider

from services.logger import setup_logger

logger = logging.getLogger("services.rag")
setup_logger("rag")
settings = get_settings()


class RAGService:
    """High-level RAG service wiring together all components from backend/rag/."""

    def __init__(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.documents_dir = os.path.join(base_dir, settings.documents_dir)

        self._vector_store = create_vector_store(
            qdrant_host=settings.qdrant_host,
            qdrant_port=settings.qdrant_port,
            collection=settings.qdrant_collection,
            fallback=settings.qdrant_fallback,
            data_dir=os.path.join(base_dir, "data", "faiss_store"),
        )

        bm25_path = os.path.join(base_dir, settings.bm25_index_path)
        self._bm25_store = BM25Store(index_path=bm25_path)

        self._embedder = create_embedder()

        try:
            self._reranker = get_reranker(model_name=settings.reranker_model)
        except Exception as e:
            logger.warning("Reranker not available, running without reranking: %s", e)
            self._reranker = None

        self._retriever = HybridRetriever(
            vector_store=self._vector_store,
            bm25_store=self._bm25_store,
            embedder=self._embedder,
            reranker=self._reranker,
            dense_top_k=settings.rag_dense_top_k,
            bm25_top_k=settings.rag_bm25_top_k,
            rerank_top_k=settings.rag_rerank_top_k,
            final_top_k=settings.rag_final_top_k,
            fusion_method=settings.rag_fusion_method,
        )

        self._pipeline = IngestionPipeline(
            vector_store=self._vector_store,
            bm25_store=self._bm25_store,
            embedder=self._embedder,
            documents_dir=self.documents_dir,
            chunk_max_tokens=settings.rag_chunk_max_tokens,
            chunk_overlap_tokens=settings.rag_chunk_overlap_tokens,
        )

        self._qa = QAEngine(provider=create_llm_provider())

    # ── Ingestion ──────────────────────────────────────────────────

    async def ingest(self) -> Dict[str, Any]:
        return await self._pipeline.ingest()

    # ── Retrieval ──────────────────────────────────────────────────

    async def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """Legacy-compatible search returning list of dicts."""
        hits, _debug = await self._retriever.retrieve(query)
        results = []
        for h in hits:
            results.append({
                "text": h.text,
                "source": h.metadata.get("doc_name", ""),
                "page": h.metadata.get("page_start", 0),
                "section_id": h.metadata.get("section_id", ""),
                "section_title": h.metadata.get("section_title", ""),
                "chunk_type": h.metadata.get("chunk_type", "text_clause"),
                "distance": 1.0 - h.score,
            })
        return results

    async def search_hits(self, query: str) -> tuple[List[DocHit], Any]:
        """Return raw DocHit objects and RetrievalDebug."""
        return await self._retriever.retrieve(query)

    # ── Grounded QA ────────────────────────────────────────────────

    async def answer(self, question: str) -> QAResponse:
        """Full pipeline: retrieve + grounded QA."""
        hits, debug = await self._retriever.retrieve(question)
        return await self._qa.answer(question, hits, debug)

    async def query_trace(self, question: str) -> tuple[QAResponse, RetrievalTrace]:
        """Full pipeline with detailed retrieval trace for the debug UI."""
        hits, trace = await self._retriever.retrieve_with_trace(question)
        debug = None
        qa_result = await self._qa.answer(question, hits, debug)
        return qa_result, trace

    # ── Status ─────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        return self._pipeline.get_status()

    # ── Accessors for advanced use ─────────────────────────────────

    @property
    def vector_store(self):
        return self._vector_store

    @property
    def bm25_store(self):
        return self._bm25_store

    @property
    def embedder(self):
        return self._embedder

    @property
    def retriever(self):
        return self._retriever

    @property
    def qa_engine(self):
        return self._qa


# ── Singleton ──────────────────────────────────────────────────────

_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
