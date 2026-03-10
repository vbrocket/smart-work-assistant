"""
Unified Speech-to-Text Service.

Supports two backends controlled by ``STT_BACKEND``:
  - ``whisper`` — local faster-whisper / openai-whisper (default)
  - ``openrouter`` — Gemini 2.5 Pro via OpenRouter audio API

Both expose the same interface used by the WebSocket voice pipeline:
  transcribe_buffer(audio_bytes, language) -> (text, language, confidence)
  transcribe_partial(audio_bytes, language) -> text
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import subprocess
import tempfile
import os
from typing import Optional, Tuple

from config import get_settings
from services.logger import setup_logger

logger = setup_logger("stt")
settings = get_settings()


def _convert_webm_to_wav(webm_bytes: bytes) -> bytes:
    """Convert webm/opus audio to WAV using ffmpeg (in-memory)."""
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as src:
        src.write(webm_bytes)
        src_path = src.name
    dst_path = src_path.replace(".webm", ".wav")
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-y", "-i", src_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", dst_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")
            logger.warning("ffmpeg webm->wav failed: %s", stderr)
            return webm_bytes
        with open(dst_path, "rb") as f:
            return f.read()
    except Exception as exc:
        logger.warning("ffmpeg conversion error: %s", exc)
        return webm_bytes
    finally:
        for p in (src_path, dst_path):
            try:
                os.unlink(p)
            except OSError:
                pass


class GeminiSTTService:
    """Transcribe audio via OpenRouter using Gemini 2.5 Pro."""

    def __init__(self):
        self._client = None
        self._model = settings.or_stt_model

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=60.0,
            )
        return self._client

    async def transcribe_buffer(
        self,
        audio_buffer: bytes,
        language: Optional[str] = None,
        suffix: str = ".webm",
    ) -> Tuple[str, str, float]:
        logger.info(
            "Gemini STT | model=%s | buf=%d bytes | lang=%s",
            self._model, len(audio_buffer), language,
        )
        wav_bytes = await asyncio.get_event_loop().run_in_executor(
            None, _convert_webm_to_wav, audio_buffer,
        )
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")
        logger.debug("Gemini STT | wav=%d bytes | b64=%d chars", len(wav_bytes), len(b64_audio))

        lang_hint = {"ar": "Arabic", "en": "English"}.get(language, "")
        prompt = "Transcribe this audio exactly as spoken. Return ONLY the transcribed text, nothing else."
        if lang_hint:
            prompt = f"Transcribe this {lang_hint} audio exactly as spoken. Return ONLY the transcribed text, nothing else."

        client = self._get_client()
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": b64_audio,
                                "format": "wav",
                            },
                        },
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=2048,
        )
        text = (response.choices[0].message.content or "").strip()
        text = text.strip('"\'')

        detected_lang = language or "ar"
        if text:
            arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
            total_alpha = sum(1 for c in text if c.isalpha())
            if total_alpha > 0:
                detected_lang = "ar" if arabic_chars / total_alpha > 0.3 else "en"

        logger.info("Gemini STT result | text='%s' | lang=%s", text[:100], detected_lang)
        return text, detected_lang, 0.95

    async def transcribe_partial(
        self,
        audio_buffer: bytes,
        language: Optional[str] = None,
        suffix: str = ".webm",
    ) -> str:
        """Partial transcription — uses Gemini too (slower but accurate)."""
        try:
            text, _, _ = await self.transcribe_buffer(
                audio_buffer, language=language, suffix=suffix,
            )
            return text
        except Exception as exc:
            logger.debug("Gemini partial STT failed (non-fatal): %s", exc)
            return ""


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_stt_service = None


def get_stt_service():
    """Return the configured STT service singleton."""
    global _stt_service
    if _stt_service is not None:
        return _stt_service

    backend = settings.stt_backend.lower()
    if backend == "openrouter":
        logger.info("STT backend: OpenRouter (%s)", settings.or_stt_model)
        _stt_service = GeminiSTTService()
    else:
        logger.info("STT backend: Whisper (local, model=%s)", settings.whisper_model)
        from services.whisper_service import WhisperService
        _stt_service = WhisperService()

    return _stt_service
