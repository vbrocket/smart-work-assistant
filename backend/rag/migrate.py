"""Migration tools: OldRagAdapter, shadow mode, JSONL import.

Allows querying the old ChromaDB-based RAG alongside the new system and
importing existing chunks/vectors from a JSONL file.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from .embedder import Embedder
from .models import ChunkRecord, DocHit

logger = logging.getLogger("rag.migrate")


class OldRagAdapter:
    """Adapter to query the legacy ChromaDB-based RAG for shadow-mode comparison."""

    def __init__(self, chroma_dir: str = "data/chroma_db", collection_name: str = "company_policy"):
        self._client = None
        self._collection = None
        self._chroma_dir = chroma_dir
        self._collection_name = collection_name

    def _init(self) -> bool:
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._chroma_dir)
            self._collection = self._client.get_collection(self._collection_name)
            return True
        except Exception as e:
            logger.warning("Old RAG adapter init failed (expected if chromadb removed): %s", e)
            return False

    def query(self, question: str, top_k: int = 5) -> Dict[str, Any]:
        """Query the old ChromaDB collection. Returns raw ChromaDB result dict."""
        if self._collection is None:
            if not self._init():
                return {"documents": [], "metadatas": [], "distances": []}

        try:
            results = self._collection.query(query_texts=[question], n_results=top_k)
            return results
        except Exception as e:
            logger.error("Old RAG query failed: %s", e)
            return {"documents": [], "metadatas": [], "distances": []}


class ShadowMode:
    """Run both old and new RAG, compare citation overlap, and log deltas."""

    def __init__(self, old_adapter: OldRagAdapter, log_path: str = "data/shadow_log.jsonl"):
        self.old = old_adapter
        self.log_path = log_path

    def compare(
        self,
        question: str,
        new_hits: List[DocHit],
        old_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compare new retrieval results with old RAG results."""
        if old_result is None:
            old_result = self.old.query(question)

        old_texts = []
        if old_result.get("documents"):
            old_texts = old_result["documents"][0] if old_result["documents"] else []

        new_texts = [h.text[:200] for h in new_hits]

        # Simple overlap: count shared text fragments
        overlap = 0
        for nt in new_texts:
            for ot in old_texts:
                if nt[:80] in ot or ot[:80] in nt:
                    overlap += 1
                    break

        delta = {
            "question": question,
            "old_count": len(old_texts),
            "new_count": len(new_hits),
            "overlap": overlap,
            "new_ids": [h.chunk_id for h in new_hits],
        }

        self._log(delta)
        return delta

    def _log(self, entry: dict) -> None:
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def import_jsonl(
    jsonl_path: str,
    vector_store,
    bm25_store,
    embedder: Embedder,
) -> int:
    """Import chunks from a JSONL file. Each line: {id, text, metadata, vector?}.

    If vector is missing, re-embeds the text.
    """
    records: List[ChunkRecord] = []
    vectors: List[List[float]] = []
    texts_to_embed: List[int] = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("Skipping invalid JSON on line %d: %s", line_num, e)
                continue

            rec = ChunkRecord(
                id=obj.get("id", ""),
                text=obj.get("text", ""),
                **{k: v for k, v in obj.get("metadata", {}).items() if k in ChunkRecord.model_fields},
            )
            rec.ensure_id()
            records.append(rec)

            vec = obj.get("vector")
            if vec and isinstance(vec, list):
                vectors.append(vec)
            else:
                vectors.append([])
                texts_to_embed.append(len(records) - 1)

    # Re-embed missing vectors
    if texts_to_embed:
        logger.info("Re-embedding %d texts without vectors ...", len(texts_to_embed))
        texts = [records[i].text for i in texts_to_embed]
        new_vecs = await embedder.embed_texts(texts)
        for idx, vec in zip(texts_to_embed, new_vecs):
            vectors[idx] = vec

    # Store
    vector_store.upsert(records, vectors)
    bm25_store.build(records)

    logger.info("Imported %d records from %s", len(records), jsonl_path)
    return len(records)
