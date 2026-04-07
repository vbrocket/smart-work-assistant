"""
LLM Provider abstraction layer.

Provides a unified interface for LLM calls that works with
Ollama (local), HuggingFace Inference API (cloud), OpenRouter (cloud),
and vLLM (local OpenAI-compatible server).

Controlled by the LLM_BACKEND env variable ("ollama", "huggingface", "openrouter", or "vllm").
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from config import get_settings

logger = logging.getLogger("llm_provider")


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        stream: bool = False,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        """Send a chat-completion request and return the assistant message."""

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        """Yield token strings as they arrive from the LLM."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> str:
        """Single-turn text generation from a prompt string."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier for logging."""


# ---------------------------------------------------------------------------
# Ollama implementation
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Calls a local Ollama server."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: float = 120.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        stream: bool = False,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        url = f"{self._base_url}/api/chat"
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature, "top_p": top_p, "num_predict": max_tokens},
        }
        logger.info("LLM call | provider=Ollama | model=%s | messages=%d | temp=%.1f", self._model, len(messages), temperature)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if stream:
                return await self._collect_stream(client, url, payload)
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        import json as _json

        url = f"{self._base_url}/api/chat"
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "top_p": top_p, "num_predict": max_tokens},
        }
        logger.info("LLM stream | provider=Ollama | model=%s | messages=%d | temp=%.1f", self._model, len(messages), temperature)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = _json.loads(line)
                    token = ""
                    if "message" in data:
                        token = data["message"].get("content", "")
                    elif "response" in data:
                        token = data["response"]
                    if token:
                        yield token
                    if data.get("done"):
                        return

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> str:
        url = f"{self._base_url}/api/generate"
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        logger.info("LLM call | provider=Ollama | model=%s | generate | temp=%.1f", self._model, temperature)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    # -- helpers --

    @staticmethod
    async def _collect_stream(
        client: httpx.AsyncClient, url: str, payload: Dict[str, Any]
    ) -> str:
        import json as _json

        full = ""
        async with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    data = _json.loads(line)
                    if "message" in data:
                        full += data["message"].get("content", "")
                    elif "response" in data:
                        full += data["response"]
                    if data.get("done"):
                        break
        return full


# ---------------------------------------------------------------------------
# HuggingFace Inference API implementation
# ---------------------------------------------------------------------------

class HuggingFaceProvider(LLMProvider):
    """Calls HuggingFace Inference API (OpenAI-compatible chat endpoint)."""

    def __init__(
        self,
        token: str,
        model: str = "MiniMaxAI/MiniMax-M2.5",
        timeout: float = 180.0,
    ):
        if not token:
            raise ValueError(
                "HF_API_TOKEN is required when LLM_BACKEND=huggingface. "
                "Set it in your .env file."
            )
        self._token = token
        self._model = model
        self._timeout = timeout
        self._client: Optional[Any] = None

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            from huggingface_hub import InferenceClient
            self._client = InferenceClient(
                provider="auto",
                api_key=self._token,
                timeout=self._timeout,
            )
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        stream: bool = False,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        import asyncio

        client = self._get_client()
        logger.info("LLM call | provider=HuggingFace | model=%s | messages=%d | temp=%.1f | max_tokens=%d",
                     self._model, len(messages), temperature, max_tokens)

        def _call():
            response = client.chat_completion(
                model=self._model,
                messages=messages,
                temperature=temperature if temperature > 0 else 0.01,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=False,
            )
            return response.choices[0].message.content or ""

        return await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=self._timeout
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        import asyncio
        import queue as _queue

        client = self._get_client()
        logger.info("LLM stream | provider=HuggingFace | model=%s | messages=%d | temp=%.1f | max_tokens=%d",
                     self._model, len(messages), temperature, max_tokens)

        token_q: _queue.Queue = _queue.Queue()
        _DONE = object()

        def _producer():
            try:
                first_token = True
                for chunk in client.chat_completion(
                    model=self._model,
                    messages=messages,
                    temperature=temperature if temperature > 0 else 0.01,
                    top_p=top_p,
                    max_tokens=max_tokens,
                    stream=True,
                ):
                    if first_token:
                        logger.info("LLM stream | first token received from %s", self._model)
                        first_token = False
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    token = (delta.content if delta and delta.content else "") or ""
                    if token:
                        token_q.put(token)
            except Exception as exc:
                logger.error("LLM stream producer error: %s", exc, exc_info=True)
                token_q.put(exc)
            finally:
                token_q.put(_DONE)

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _producer)

        while True:
            item = await asyncio.to_thread(token_q.get, timeout=self._timeout)
            if item is _DONE:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> str:
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages, temperature=temperature)


# ---------------------------------------------------------------------------
# OpenRouter implementation (Llama 4 Scout default)
# ---------------------------------------------------------------------------

class OpenRouterProvider(LLMProvider):
    """Calls OpenRouter API (OpenAI-compatible). Default model: meta-llama/llama-4-scout."""

    def __init__(
        self,
        api_key: str,
        model: str = "meta-llama/llama-4-scout",
        timeout: float = 180.0,
    ):
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_BACKEND=openrouter. "
                "Set it in your .env file."
            )
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client: Optional[Any] = None

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self._api_key,
                timeout=self._timeout,
            )
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        stream: bool = False,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        client = self._get_client()
        logger.info(
            "LLM call | provider=OpenRouter | model=%s | messages=%d | temp=%.1f | max_tokens=%d",
            self._model, len(messages), temperature, max_tokens,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature if temperature > 0 else 0.01,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=False,
        )
        return response.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 4096,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        logger.info(
            "LLM stream | provider=OpenRouter | model=%s | messages=%d | temp=%.1f | max_tokens=%d",
            self._model, len(messages), temperature, max_tokens,
        )
        stream = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature if temperature > 0 else 0.01,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
        )
        first_token = True
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            token = (delta.content if delta and delta.content else "") or ""
            if token:
                if first_token:
                    logger.info("LLM stream | first token received from %s", self._model)
                    first_token = False
                yield token

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> str:
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages, temperature=temperature)


# ---------------------------------------------------------------------------
# vLLM local server (OpenAI-compatible)
# ---------------------------------------------------------------------------

class VLLMProvider(LLMProvider):
    """Calls a local vLLM server via the OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8001/v1",
        model: str = "Qwen/Qwen3-30B-A3B",
        timeout: float = 180.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._client: Optional[Any] = None

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                base_url=self._base_url,
                api_key="not-needed",
                timeout=self._timeout,
            )
        return self._client

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Remove thinking blocks that Qwen models may emit.

        Handles both explicit <think>...</think> and implicit thinking
        (no opening tag, just a closing </think>).
        """
        import re
        text = re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
        if "</think>" in text:
            text = text.split("</think>", 1)[1]
        return text.strip()

    def _build_extra_body(self, thinking: bool) -> dict:
        """Build the extra_body dict for vLLM 0.19+.

        With --reasoning-parser qwen3, thinking is enabled by default.
        We only need chat_template_kwargs to *disable* it.
        """
        body: dict = {}
        if not thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        stream: bool = False,
        max_tokens: int = 2048,
        enable_thinking: Optional[bool] = None,
    ) -> str:
        thinking = enable_thinking if enable_thinking is not None else True
        client = self._get_client()
        logger.info(
            "LLM call | provider=vLLM | model=%s | messages=%d | temp=%.1f | max_tokens=%d | thinking=%s",
            self._model, len(messages), temperature, max_tokens, thinking,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature if temperature > 0 else 0.01,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=False,
            extra_body=self._build_extra_body(thinking),
        )
        msg = response.choices[0].message
        raw = msg.content or ""
        return self._strip_thinking(raw)

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        thinking = enable_thinking if enable_thinking is not None else True
        client = self._get_client()
        logger.info(
            "LLM stream | provider=vLLM | model=%s | messages=%d | temp=%.1f | max_tokens=%d | thinking=%s",
            self._model, len(messages), temperature, max_tokens, thinking,
        )
        stream = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature if temperature > 0 else 0.01,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
            extra_body=self._build_extra_body(thinking),
        )
        first_token = True
        in_reasoning = False
        # Continuous English-spill filter: buffer content in ~sentence
        # chunks and redirect English-majority text into <think> blocks.
        _content_buf = ""
        _in_spill = False  # True when we've opened a spill <think> block

        def _is_english_heavy(text: str) -> bool:
            """Return True if text has > 50% Latin alphabetic chars."""
            alpha = [c for c in text if c.isalpha()]
            if len(alpha) < 5:
                return False
            latin = sum(1 for c in alpha if c.isascii())
            return (latin / len(alpha)) > 0.5

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning", None) or None
            if reasoning is not None:
                if _in_spill:
                    _in_spill = False
                    yield "</think>"
                if _content_buf:
                    yield _content_buf
                    _content_buf = ""
                if not in_reasoning:
                    in_reasoning = True
                    yield "<think>"
                yield reasoning
                continue

            if in_reasoning:
                in_reasoning = False
                yield "</think>"

            token = (delta.content if delta and delta.content else "") or ""
            if not token:
                continue

            _content_buf += token

            # Flush on sentence boundaries or when buffer is large enough
            while True:
                # Find a sentence boundary in the buffer
                cut = -1
                for delim in ["\n", ". ", ".\n", "؟ ", "。"]:
                    pos = _content_buf.find(delim)
                    if pos != -1:
                        cut = pos + len(delim)
                        break
                if cut == -1 and len(_content_buf) > 200:
                    cut = 200
                if cut == -1:
                    break

                segment = _content_buf[:cut]
                _content_buf = _content_buf[cut:]

                if _is_english_heavy(segment):
                    if not _in_spill:
                        _in_spill = True
                        yield "<think>"
                    yield segment
                else:
                    if _in_spill:
                        _in_spill = False
                        yield "</think>"
                    if first_token:
                        logger.info("LLM stream | first token received from %s", self._model)
                        first_token = False
                    yield segment

        # Flush remaining buffer
        if _content_buf:
            if _is_english_heavy(_content_buf):
                if not _in_spill:
                    yield "<think>"
                    _in_spill = True
                yield _content_buf
            else:
                if _in_spill:
                    _in_spill = False
                    yield "</think>"
                if first_token:
                    logger.info("LLM stream | first token received from %s", self._model)
                yield _content_buf

        if in_reasoning or _in_spill:
            yield "</think>"

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> str:
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages, temperature=temperature, enable_thinking=False)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider: Optional[LLMProvider] = None


def create_llm_provider() -> LLMProvider:
    """Create or return the cached LLM provider based on settings."""
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    backend = settings.llm_backend.lower()

    if backend == "vllm":
        logger.info("LLM backend: vLLM (%s @ %s)", settings.vllm_llm_model, settings.vllm_llm_url)
        _provider = VLLMProvider(
            base_url=settings.vllm_llm_url,
            model=settings.vllm_llm_model,
        )
    elif backend == "huggingface":
        logger.info("LLM backend: HuggingFace (%s)", settings.hf_llm_model)
        _provider = HuggingFaceProvider(
            token=settings.hf_api_token,
            model=settings.hf_llm_model,
        )
    elif backend == "openrouter":
        logger.info("LLM backend: OpenRouter (%s)", settings.or_llm_model)
        _provider = OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            model=settings.or_llm_model,
        )
    else:
        logger.info("LLM backend: Ollama (%s @ %s)", settings.ollama_model, settings.ollama_host)
        _provider = OllamaProvider(
            base_url=settings.ollama_host,
            model=settings.ollama_model,
        )
    return _provider
