"""Vector store abstraction: Qdrant (primary) with SQLite+FAISS fallback.

Both backends expose the same interface so callers never need to know which is active.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import numpy as np

try:
    import faiss as _faiss
    FAISS_AVAILABLE = True
except ImportError:
    _faiss = None
    FAISS_AVAILABLE = False

from .models import ChunkRecord, DocHit

logger = logging.getLogger("rag.vector_store")

# ──────────────────────────── Abstract base ────────────────────────────


class VectorStoreBase(ABC):
    @abstractmethod
    def upsert(self, records: List[ChunkRecord], vectors: List[List[float]]) -> int:
        ...

    @abstractmethod
    def search(self, query_vector: List[float], top_k: int = 30) -> List[DocHit]:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

    @abstractmethod
    def delete_collection(self) -> None:
        ...

    def get_siblings(self, section_id: str, doc_id: str = "") -> List[DocHit]:
        """Return all chunks whose section_id starts with *section_id*.

        Used by the retriever for context expansion (e.g. given section 4.4.3,
        pull in 4.4.3.1, 4.4.3.2, etc.).
        Default implementation returns empty; backends override if supported.
        """
        return []


# ──────────────────────────── Qdrant backend ───────────────────────────


class QdrantVectorStore(VectorStoreBase):
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection: str = "hr_policy_rag",
    ):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self.collection = collection
        self._client = QdrantClient(host=host, port=port, timeout=60)
        self._Distance = Distance
        self._VectorParams = VectorParams
        self._dim: Optional[int] = None

    def _ensure_collection(self, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        collections = [c.name for c in self._client.get_collections().collections]
        if self.collection not in collections:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", self.collection, dim)
        self._dim = dim

    def upsert(self, records: List[ChunkRecord], vectors: List[List[float]]) -> int:
        from qdrant_client.models import PointStruct

        if not records:
            return 0
        self._ensure_collection(len(vectors[0]))

        points = []
        for rec, vec in zip(records, vectors):
            rec.ensure_id()
            points.append(
                PointStruct(
                    id=rec.id,
                    vector=vec,
                    payload={**rec.payload_dict(), "text": rec.text},
                )
            )

        batch_size = 100
        for i in range(0, len(points), batch_size):
            self._client.upsert(
                collection_name=self.collection,
                points=points[i: i + batch_size],
            )
        logger.info("Upserted %d vectors into Qdrant '%s'", len(points), self.collection)
        return len(points)

    def search(self, query_vector: List[float], top_k: int = 30) -> List[DocHit]:
        results = self._client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            with_payload=True,
        )
        hits: List[DocHit] = []
        for r in results:
            payload = r.payload or {}
            hits.append(
                DocHit(
                    chunk_id=str(r.id),
                    text=payload.pop("text", ""),
                    score=r.score,
                    metadata=payload,
                )
            )
        return hits

    def count(self) -> int:
        try:
            info = self._client.get_collection(self.collection)
            return info.points_count or 0
        except Exception:
            return 0

    def delete_collection(self) -> None:
        try:
            self._client.delete_collection(self.collection)
            logger.info("Deleted Qdrant collection '%s'", self.collection)
        except Exception as e:
            logger.warning("Could not delete collection: %s", e)


# ──────────────────────── FAISS + SQLite fallback ──────────────────────


class FaissVectorStore(VectorStoreBase):
    """Local fallback using FAISS for vectors and SQLite for metadata."""

    def __init__(self, data_dir: str = "data/faiss_store"):
        if not FAISS_AVAILABLE:
            raise RuntimeError(
                "faiss-cpu is not installed or cannot be imported. "
                "Install with: pip install faiss-cpu"
            )
        os.makedirs(data_dir, exist_ok=True)
        self._data_dir = data_dir
        self._index_path = os.path.join(data_dir, "index.faiss")
        self._db_path = os.path.join(data_dir, "meta.sqlite3")
        self._index = None
        self._init_sqlite()
        self._load_index()

    def _ensure_dir(self) -> None:
        os.makedirs(self._data_dir, exist_ok=True)

    def _init_sqlite(self) -> None:
        self._ensure_dir()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "  row_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  chunk_id TEXT UNIQUE,"
            "  text TEXT,"
            "  metadata TEXT"
            ")"
        )
        conn.commit()
        conn.close()

    def _load_index(self) -> None:
        try:
            if os.path.exists(self._index_path):
                self._index = _faiss.read_index(self._index_path)
                logger.info("Loaded FAISS index (%d vectors)", self._index.ntotal)
        except Exception as e:
            logger.debug("No existing FAISS index: %s", e)

    def _save_index(self) -> None:
        if self._index is not None:
            self._ensure_dir()
            _faiss.write_index(self._index, self._index_path)

    def upsert(self, records: List[ChunkRecord], vectors: List[List[float]]) -> int:
        if not records:
            return 0
        dim = len(vectors[0])

        self.delete_collection()
        self._init_sqlite()

        self._index = _faiss.IndexFlatIP(dim)
        arr = np.array(vectors, dtype=np.float32)
        _faiss.normalize_L2(arr)
        self._index.add(arr)

        conn = sqlite3.connect(self._db_path)
        for rec in records:
            rec.ensure_id()
            conn.execute(
                "INSERT OR REPLACE INTO chunks (chunk_id, text, metadata) VALUES (?, ?, ?)",
                (rec.id, rec.text, json.dumps(rec.payload_dict(), ensure_ascii=False)),
            )
        conn.commit()
        conn.close()

        self._save_index()
        logger.info("Upserted %d vectors into FAISS fallback", len(records))
        return len(records)

    def search(self, query_vector: List[float], top_k: int = 30) -> List[DocHit]:
        if self._index is None or self._index.ntotal == 0:
            return []

        qv = np.array([query_vector], dtype=np.float32)
        _faiss.normalize_L2(qv)
        scores, indices = self._index.search(qv, min(top_k, self._index.ntotal))

        conn = sqlite3.connect(self._db_path)
        hits: List[DocHit] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            row_id = int(idx) + 1
            cur = conn.execute(
                "SELECT chunk_id, text, metadata FROM chunks WHERE row_id = ?",
                (row_id,),
            )
            row = cur.fetchone()
            if row:
                meta = json.loads(row[2]) if row[2] else {}
                hits.append(DocHit(chunk_id=row[0], text=row[1], score=float(score), metadata=meta))
        conn.close()
        return hits

    def count(self) -> int:
        if self._index is not None:
            return self._index.ntotal
        return 0

    def get_siblings(self, section_id: str, doc_id: str = "") -> List[DocHit]:
        """Return chunks whose section_id starts with the given prefix."""
        conn = sqlite3.connect(self._db_path)
        prefix = section_id + "."
        cur = conn.execute(
            "SELECT chunk_id, text, metadata FROM chunks"
        )
        hits: List[DocHit] = []
        for row in cur.fetchall():
            meta = json.loads(row[2]) if row[2] else {}
            sid = meta.get("section_id", "")
            if sid == section_id or sid.startswith(prefix):
                if doc_id and meta.get("doc_id", "") != doc_id:
                    continue
                hits.append(DocHit(chunk_id=row[0], text=row[1], score=0.0, metadata=meta))
        conn.close()
        return hits

    def delete_collection(self) -> None:
        self._index = None
        for p in (self._index_path, self._db_path):
            if os.path.exists(p):
                os.remove(p)
        self._init_sqlite()
        logger.info("Cleared FAISS fallback store")


# ──────────────────────── Factory ──────────────────────────────────────


def create_vector_store(
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
    collection: str = "hr_policy_rag",
    fallback: bool = True,
    data_dir: str = "data/faiss_store",
) -> VectorStoreBase:
    """Try Qdrant first; fall back to FAISS if unavailable and fallback=True."""
    try:
        store = QdrantVectorStore(host=qdrant_host, port=qdrant_port, collection=collection)
        store._client.get_collections()  # connectivity check
        logger.info("Connected to Qdrant at %s:%d", qdrant_host, qdrant_port)
        return store
    except Exception as e:
        logger.warning("Qdrant unavailable (%s)", e)
        if fallback:
            if not FAISS_AVAILABLE:
                raise RuntimeError(
                    f"Qdrant is unavailable ({e}) and faiss-cpu is not installed. "
                    "Either start Qdrant or install faiss-cpu: pip install faiss-cpu"
                )
            logger.info("Falling back to local FAISS+SQLite vector store")
            return FaissVectorStore(data_dir=data_dir)
        raise RuntimeError(f"Qdrant is unavailable and fallback is disabled: {e}")
