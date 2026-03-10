"""Cross-encoder reranker — OpenRouter LLM, HuggingFace API, or local CrossEncoder.

Backend selection (``effective_reranker_backend``):
  * **openrouter** — uses the configured LLM to score relevance via a
    structured prompt.  Fast, no extra model, Arabic-aware.
  * **huggingface** — batched cross-encoder call to the HF Inference API.
  * **ollama** / anything else — loads sentence-transformers locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, List, Optional, Tuple

import httpx
import requests as _req

from .models import DocHit

logger = logging.getLogger("rag.reranker")

_global_reranker = None

_RERANK_SYSTEM = (
    "You are a relevance scorer. Given a QUERY and a DOCUMENT, output ONLY a "
    "JSON object: {\"score\": <float 0-10>, \"relevant\": <bool>}. "
    "Score 0 = completely irrelevant, 10 = perfect match. "
    "No explanation, no markdown, just the JSON."
)

_RERANK_USER = "QUERY: {query}\n\nDOCUMENT:\n{doc}"


class Reranker:
    """Wraps multiple reranking backends behind a single ``rerank()`` API."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model_name = model_name
        self._backend: Optional[str] = None
        self._local_model = None
        self._hf_token: Optional[str] = None
        self._or_client: Any = None
        self._or_model: Optional[str] = None
        self._vllm_url: Optional[str] = None
        self._vllm_model: Optional[str] = None

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def _resolve_backend(self) -> None:
        if self._backend is not None:
            return
        from config import get_settings
        settings = get_settings()
        backend = settings.effective_reranker_backend

        if backend == "vllm":
            self._backend = "vllm"
            self._vllm_url = settings.vllm_rerank_url.rstrip("/")
            self._vllm_model = settings.vllm_rerank_model
            logger.info(
                "Reranker backend: vLLM (%s @ %s)",
                self._vllm_model, self._vllm_url,
            )
        elif backend == "openrouter" and settings.openrouter_api_key:
            self._backend = "openrouter"
            self._or_model = settings.or_llm_model
            logger.info(
                "Reranker backend: OpenRouter LLM (%s)", self._or_model,
            )
        elif backend == "huggingface" and settings.hf_api_token:
            self._backend = "huggingface"
            self._hf_token = settings.hf_api_token
            logger.info(
                "Reranker backend: HuggingFace Inference API (%s)",
                self.model_name,
            )
        else:
            self._backend = "local"
            logger.info(
                "Reranker backend: local CrossEncoder (%s)", self.model_name,
            )

    # ------------------------------------------------------------------
    # vLLM — dedicated cross-encoder via /rerank endpoint
    # ------------------------------------------------------------------

    async def _rerank_vllm(
        self, query: str, hits: List[DocHit], top_k: int,
    ) -> List[DocHit]:
        t0 = time.time()
        url = f"{self._vllm_url}/rerank"
        payload = {
            "model": self._vllm_model,
            "query": query,
            "documents": [h.text for h in hits],
            "top_n": top_k,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        out: List[DocHit] = []
        for item in results[:top_k]:
            idx = item["index"]
            score = float(item.get("relevance_score", item.get("score", 0)))
            hit = hits[idx]
            hit.score = score
            out.append(hit)

        elapsed = time.time() - t0
        logger.info(
            "vLLM reranker done | pairs=%d | top=%.4f | %.1fs",
            len(hits), out[0].score if out else 0, elapsed,
        )
        return out

    # ------------------------------------------------------------------
    # OpenRouter — LLM-as-reranker (parallel scoring)
    # ------------------------------------------------------------------

    def _get_or_client(self):
        if self._or_client is None:
            from openai import OpenAI
            from config import get_settings
            settings = get_settings()
            self._or_client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=30.0,
            )
        return self._or_client

    async def _rerank_openrouter(
        self, query: str, hits: List[DocHit], top_k: int,
    ) -> List[DocHit]:
        t0 = time.time()
        client = self._get_or_client()
        doc_limit = 300

        def _score_one(hit: DocHit) -> Tuple[float, DocHit]:
            truncated = hit.text[:doc_limit * 4] if len(hit.text) > doc_limit * 4 else hit.text
            try:
                resp = client.chat.completions.create(
                    model=self._or_model,
                    messages=[
                        {"role": "system", "content": _RERANK_SYSTEM},
                        {"role": "user", "content": _RERANK_USER.format(
                            query=query, doc=truncated,
                        )},
                    ],
                    temperature=0,
                    max_tokens=50,
                )
                raw = resp.choices[0].message.content.strip()
                raw = re.sub(r"```json\s*", "", raw)
                raw = re.sub(r"```\s*$", "", raw)
                data = json.loads(raw)
                return (float(data.get("score", 0)), hit)
            except Exception as exc:
                logger.debug("OR reranker score error: %s", exc)
                return (0.0, hit)

        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, _score_one, h) for h in hits]
        results = await asyncio.gather(*tasks)

        scored = list(results)
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, h in scored[:top_k]:
            h.score = s
            out.append(h)

        elapsed = time.time() - t0
        logger.info(
            "OR reranker done | pairs=%d | top=%.2f | %.1fs",
            len(hits), out[0].score if out else 0, elapsed,
        )
        return out

    # ------------------------------------------------------------------
    # HuggingFace Inference API — single batched POST (Pro plan)
    # ------------------------------------------------------------------

    def _rerank_hf(
        self, query: str, hits: List[DocHit], top_k: int,
    ) -> List[DocHit]:
        t0 = time.time()
        headers = {
            "Authorization": f"Bearer {self._hf_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": [{"text": query, "text_pair": h.text} for h in hits],
            "parameters": {"function_to_apply": "none"},
        }
        url = f"https://router.huggingface.co/hf-inference/models/{self.model_name}"
        logger.debug("HF reranker request | pairs=%d", len(hits))

        last_err = None
        resp = None
        for attempt in range(3):
            try:
                resp = _req.post(url, headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                last_err = None
                break
            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    "HF reranker attempt %d failed (%s), retry in %ds",
                    attempt + 1, e, wait,
                )
                time.sleep(wait)
        if last_err is not None:
            raise last_err

        results = resp.json()

        inner = results[0] if (isinstance(results, list) and
                               len(results) == 1 and
                               isinstance(results[0], list)) else results

        scored: List[Tuple[float, DocHit]] = []
        for hit, item in zip(hits, inner):
            score = item["score"] if isinstance(item, dict) else float(item)
            scored.append((score, hit))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, h in scored[:top_k]:
            h.score = s
            out.append(h)

        elapsed = time.time() - t0
        logger.info(
            "HF reranker done | pairs=%d | top=%.4f | %.1fs",
            len(hits), out[0].score if out else 0, elapsed,
        )
        return out

    # ------------------------------------------------------------------
    # Local sentence-transformers path
    # ------------------------------------------------------------------

    def _load_local(self) -> None:
        if self._local_model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
            import torch

            from config import get_settings
            _settings = get_settings()
            gpu_reserved = _settings.tts_backend.lower() in ("xtts", "namaa")
            device = "cpu" if gpu_reserved else (
                "cuda" if torch.cuda.is_available() else "cpu"
            )
            logger.info("Loading reranker %s on %s ...", self.model_name, device)
            self._local_model = CrossEncoder(self.model_name, device=device)
            logger.info("Reranker loaded successfully")
        except ImportError:
            logger.error(
                "sentence-transformers or torch not installed. "
                "Install with: pip install sentence-transformers torch"
            )
            raise

    def _rerank_local(
        self, query: str, hits: List[DocHit], top_k: int,
    ) -> List[DocHit]:
        self._load_local()
        pairs = [[query, h.text] for h in hits]
        scores = self._local_model.predict(pairs)

        scored: List[Tuple[float, DocHit]] = []
        for score, hit in zip(scores, hits):
            scored.append((float(score), hit))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, h in scored[:top_k]:
            h.score = s
            out.append(h)
        return out

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rerank(
        self,
        query: str,
        hits: List[DocHit],
        top_k: int = 8,
    ) -> List[DocHit]:
        """Score each hit against query and return the top_k by reranker score."""
        if not hits:
            return []

        self._resolve_backend()

        if self._backend == "vllm":
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(1) as pool:
                        future = pool.submit(
                            asyncio.run,
                            self._rerank_vllm(query, hits, top_k),
                        )
                        return future.result(timeout=60)
                return asyncio.run(
                    self._rerank_vllm(query, hits, top_k)
                )
            except Exception as e:
                logger.warning("vLLM reranker failed (%s), falling back to local", e)
                return self._rerank_local(query, hits, top_k)

        if self._backend == "openrouter":
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(1) as pool:
                        future = pool.submit(
                            asyncio.run,
                            self._rerank_openrouter(query, hits, top_k),
                        )
                        return future.result(timeout=60)
                return asyncio.run(
                    self._rerank_openrouter(query, hits, top_k)
                )
            except Exception as e:
                logger.warning("OR reranker failed (%s), returning unsorted", e)
                return hits[:top_k]

        if self._backend == "huggingface":
            try:
                return self._rerank_hf(query, hits, top_k)
            except Exception as e:
                logger.warning(
                    "HF reranker failed (%s), falling back to local", e,
                )
                return self._rerank_local(query, hits, top_k)

        return self._rerank_local(query, hits, top_k)


def get_reranker(model_name: str = "BAAI/bge-reranker-v2-m3") -> Reranker:
    global _global_reranker
    if _global_reranker is None:
        _global_reranker = Reranker(model_name=model_name)
    return _global_reranker
