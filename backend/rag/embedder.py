"""Multi-backend embedding: Ollama (local), HuggingFace, and OpenRouter.

The active backend is selected by `EMBED_BACKEND` (falls back to
`LLM_BACKEND` when unset).  All expose the same `embed_texts` /
`embed_single` API so callers don't need any if/else branching.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, List, Optional

import httpx

from config import get_settings

logger = logging.getLogger("rag.embedder")


class BaseEmbedder(ABC):
    """Shared interface for all embedding backends."""

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts, batching automatically."""

    @abstractmethod
    async def embed_single(self, text: str) -> List[float]:
        """Embed a single text."""


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaEmbedder(BaseEmbedder):
    """Generate embeddings using an Ollama-hosted model (bge-m3 by default)."""

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "bge-m3",
        batch_size: int = 8,
        timeout: float = 600.0,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        all_embeddings: List[List[float]] = []
        total = len(texts)
        total_batches = (total + self.batch_size - 1) // self.batch_size

        for i in range(0, total, self.batch_size):
            batch = texts[i: i + self.batch_size]
            batch_num = i // self.batch_size + 1
            logger.info("Embed | provider=Ollama | model=%s | batch %d/%d (%d texts)", self.model, batch_num, total_batches, len(batch))
            embs = await self._embed_batch(batch)
            all_embeddings.extend(embs)

        return all_embeddings

    async def embed_single(self, text: str) -> List[float]:
        logger.info("Embed | provider=Ollama | model=%s | single query", self.model)
        result = await self._embed_batch([text])
        return result[0]

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        url = f"{self.ollama_url}/api/embed"
        payload = {"model": self.model, "input": texts}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                body = response.text[:500]
                logger.error(
                    "Ollama embed failed | status=%d | model=%s | batch=%d | body=%s",
                    response.status_code, self.model, len(texts), body,
                )
                raise RuntimeError(
                    f"Ollama embedding failed (HTTP {response.status_code}): {body}"
                )
            data = response.json()

        embeddings = data.get("embeddings", [])
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}"
            )
        return embeddings


# ---------------------------------------------------------------------------
# HuggingFace Inference API
# ---------------------------------------------------------------------------

class HuggingFaceEmbedder(BaseEmbedder):
    """Generate embeddings via the HuggingFace Inference API.

    For instruct-style models (e.g. multilingual-e5-large-instruct) the
    ``embed_texts`` method automatically prepends ``"passage: "`` and
    ``embed_single`` prepends ``"query: "`` for optimal retrieval quality.
    """

    _INSTRUCT_MODELS = {"intfloat/multilingual-e5-large-instruct", "intfloat/e5-large-instruct"}

    def __init__(
        self,
        token: str,
        model: str = "intfloat/multilingual-e5-large-instruct",
        batch_size: int = 8,
    ):
        if not token:
            raise ValueError(
                "HF_API_TOKEN is required when LLM_BACKEND=huggingface. "
                "Set it in your .env file."
            )
        self._token = token
        self._model = model
        self.batch_size = batch_size
        self._client: Optional[Any] = None
        self._is_instruct = model in self._INSTRUCT_MODELS

    def _get_client(self):
        if self._client is None:
            from huggingface_hub import InferenceClient
            self._client = InferenceClient(
                provider="auto",
                api_key=self._token,
            )
        return self._client

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed document texts (prefixed with 'passage: ' for instruct models)."""
        if self._is_instruct:
            texts = [f"passage: {t}" for t in texts]

        all_embeddings: List[List[float]] = []
        total = len(texts)
        total_batches = (total + self.batch_size - 1) // self.batch_size

        for i in range(0, total, self.batch_size):
            batch = texts[i: i + self.batch_size]
            batch_num = i // self.batch_size + 1
            logger.info("Embed | provider=HuggingFace | model=%s | batch %d/%d (%d texts)", self._model, batch_num, total_batches, len(batch))
            embs = await self._embed_batch(batch)
            all_embeddings.extend(embs)

        return all_embeddings

    async def embed_single(self, text: str) -> List[float]:
        """Embed a query text (prefixed with 'query: ' for instruct models)."""
        if self._is_instruct:
            text = f"query: {text}"
        logger.info("Embed | provider=HuggingFace | model=%s | single query", self._model)
        result = await self._embed_batch([text])
        return result[0]

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        import time as _time
        client = self._get_client()

        def _call():
            results = []
            for text in texts:
                last_err = None
                for attempt in range(3):
                    try:
                        vec = client.feature_extraction(
                            text,
                            model=self._model,
                        )
                        if hasattr(vec, "tolist"):
                            vec = vec.tolist()
                        if vec and isinstance(vec[0], list):
                            vec = vec[0]
                        results.append(vec)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        wait = 2 ** attempt
                        logger.warning(
                            "HF embed attempt %d failed (%s), retry in %ds",
                            attempt + 1, e, wait,
                        )
                        _time.sleep(wait)
                if last_err is not None:
                    raise last_err
            return results

        return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# OpenRouter Embeddings API
# ---------------------------------------------------------------------------

class OpenRouterEmbedder(BaseEmbedder):
    """Generate embeddings via OpenRouter (OpenAI-compatible embeddings endpoint)."""

    def __init__(
        self,
        api_key: str,
        model: str = "baai/bge-m3",
        batch_size: int = 8,
        timeout: float = 60.0,
    ):
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_BACKEND=openrouter. "
                "Set it in your .env file."
            )
        self._api_key = api_key
        self._model = model
        self.batch_size = batch_size
        self._timeout = timeout
        self._client: Optional[Any] = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self._api_key,
                timeout=self._timeout,
            )
        return self._client

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        all_embeddings: List[List[float]] = []
        total = len(texts)
        total_batches = (total + self.batch_size - 1) // self.batch_size

        for i in range(0, total, self.batch_size):
            batch = texts[i: i + self.batch_size]
            batch_num = i // self.batch_size + 1
            logger.info(
                "Embed | provider=OpenRouter | model=%s | batch %d/%d (%d texts)",
                self._model, batch_num, total_batches, len(batch),
            )
            embs = await self._embed_batch(batch)
            all_embeddings.extend(embs)

        return all_embeddings

    async def embed_single(self, text: str) -> List[float]:
        logger.info("Embed | provider=OpenRouter | model=%s | single query", self._model)
        result = await self._embed_batch([text])
        return result[0]

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()

        def _call():
            response = client.embeddings.create(
                model=self._model,
                input=texts,
            )
            return [item.embedding for item in response.data]

        return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# vLLM local embedding server (OpenAI-compatible)
# ---------------------------------------------------------------------------

class VLLMEmbedder(BaseEmbedder):
    """Generate embeddings via a local vLLM server (OpenAI embeddings API)."""

    def __init__(
        self,
        base_url: str = "http://localhost:8002/v1",
        model: str = "BAAI/bge-m3",
        batch_size: int = 32,
        timeout: float = 60.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.batch_size = batch_size
        self._timeout = timeout
        self._client: Optional[Any] = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self._base_url,
                api_key="not-needed",
                timeout=self._timeout,
            )
        return self._client

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        all_embeddings: List[List[float]] = []
        total = len(texts)
        total_batches = (total + self.batch_size - 1) // self.batch_size

        for i in range(0, total, self.batch_size):
            batch = texts[i: i + self.batch_size]
            batch_num = i // self.batch_size + 1
            logger.info(
                "Embed | provider=vLLM | model=%s | batch %d/%d (%d texts)",
                self._model, batch_num, total_batches, len(batch),
            )
            embs = await self._embed_batch(batch)
            all_embeddings.extend(embs)

        return all_embeddings

    async def embed_single(self, text: str) -> List[float]:
        logger.info("Embed | provider=vLLM | model=%s | single query", self._model)
        result = await self._embed_batch([text])
        return result[0]

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()

        def _call():
            response = client.embeddings.create(
                model=self._model,
                input=texts,
            )
            return [item.embedding for item in response.data]

        return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# Backwards-compatibility alias
# ---------------------------------------------------------------------------

Embedder = OllamaEmbedder


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_embedder() -> BaseEmbedder:
    """Create the correct embedder based on EMBED_BACKEND (or LLM_BACKEND)."""
    settings = get_settings()
    backend = settings.effective_embed_backend

    if backend == "vllm":
        logger.info("Embedder backend: vLLM (%s @ %s)", settings.vllm_embed_model, settings.vllm_embed_url)
        return VLLMEmbedder(
            base_url=settings.vllm_embed_url,
            model=settings.vllm_embed_model,
            batch_size=settings.embedding_batch_size,
        )

    if backend == "huggingface":
        logger.info("Embedder backend: HuggingFace (%s)", settings.hf_embed_model)
        return HuggingFaceEmbedder(
            token=settings.hf_api_token,
            model=settings.hf_embed_model,
            batch_size=settings.embedding_batch_size,
        )

    if backend == "openrouter":
        logger.info("Embedder backend: OpenRouter (%s)", settings.or_embed_model)
        return OpenRouterEmbedder(
            api_key=settings.openrouter_api_key,
            model=settings.or_embed_model,
            batch_size=settings.embedding_batch_size,
        )

    logger.info("Embedder backend: Ollama (%s @ %s)", settings.ollama_embed_model, settings.ollama_host)
    return OllamaEmbedder(
        ollama_url=settings.ollama_host,
        model=settings.ollama_embed_model,
        batch_size=settings.embedding_batch_size,
    )
