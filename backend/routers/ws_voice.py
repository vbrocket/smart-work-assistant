"""
WebSocket Voice Pipeline — unified streaming endpoint.

Single persistent WebSocket at ``/ws/voice`` that handles the full
audio-in -> STT -> LLM -> TTS -> audio-out loop.

Protocol
--------
Client -> Server:
    JSON  {"type":"config","language":"ar","voice_mode":true}
    BYTES  raw audio chunk (webm/opus, ~100 ms each)
    JSON  {"type":"end_audio"}
    JSON  {"type":"cancel"}

Server -> Client:
    JSON  {"type":"partial_transcript","text":"..."}
    JSON  {"type":"transcript","text":"...","language":"ar","confidence":0.9}
    JSON  {"type":"route","intent":"policy_qa"}
    JSON  {"type":"token","content":"..."}
    JSON  {"type":"citations","citations":[...],"refs_text":"..."}
    JSON  {"type":"tts_start","sentence":"..."}
    BYTES  TTS audio for the preceding sentence
    JSON  {"type":"done","full_response":"..."}
    JSON  {"type":"error","message":"..."}
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from config import get_settings
from database import AsyncSessionLocal
from services.stt_service import get_stt_service
from services.llm_service import get_llm_service
from services.tts_service import get_tts_service
from services.rag_service import get_rag_service
from services.orchestrator import get_orchestrator, ROUTE_POLICY_QA, ROUTE_WORKSPACE

from services.logger import setup_logger
logger = setup_logger("ws_voice")
settings = get_settings()

router = APIRouter()

SENTENCE_RE = re.compile(r"[.!?؟。\n،؛:]")
MIN_SENTENCE_CHARS = 20
MAX_CHUNK_CHARS = 150
PARTIAL_INTERVAL_S = 2.5


async def _safe_send_json(ws: WebSocket, data: dict) -> bool:
    """Send JSON, return False if the connection is already closed."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_json(data)
            return True
    except Exception:
        pass
    return False


async def _safe_send_bytes(ws: WebSocket, data: bytes) -> bool:
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_bytes(data)
            return True
    except Exception:
        pass
    return False


class _VoiceSession:
    """Per-connection state machine."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.language: str = "ar"
        self.voice_mode: bool = True
        self.audio_buffer = io.BytesIO()
        self.cancelled = False
        self.pipeline_running = False
        self._last_partial_time: float = 0.0
        self._partial_task: Optional[asyncio.Task] = None

    def reset(self):
        self.audio_buffer = io.BytesIO()
        self.cancelled = False
        self.pipeline_running = False
        self._last_partial_time = 0.0
        if self._partial_task and not self._partial_task.done():
            self._partial_task.cancel()
        self._partial_task = None

    async def handle_audio_chunk(self, data: bytes):
        if self.pipeline_running:
            return
        self.audio_buffer.write(data)
        now = time.monotonic()
        buf_size = self.audio_buffer.tell()
        logger.debug("WS audio chunk | size=%d total_buf=%d", len(data), buf_size)
        if (
            now - self._last_partial_time >= PARTIAL_INTERVAL_S
            and buf_size > 8_000
            and (self._partial_task is None or self._partial_task.done())
        ):
            self._last_partial_time = now
            snapshot = self.audio_buffer.getvalue()
            logger.info("WS partial STT triggered | buf=%d bytes", len(snapshot))
            self._partial_task = asyncio.create_task(
                self._send_partial(snapshot)
            )

    async def _send_partial(self, audio_bytes: bytes):
        try:
            stt = get_stt_service()
            text = await stt.transcribe_partial(
                audio_bytes, language=self.language
            )
            logger.info("WS partial STT result | text='%s'", text[:80] if text else "")
            if text and not self.cancelled:
                await _safe_send_json(
                    self.ws, {"type": "partial_transcript", "text": text}
                )
        except Exception as exc:
            logger.warning("Partial STT error: %s", exc)

    async def run_final_transcription(self) -> tuple[str, str, float]:
        buf = self.audio_buffer.getvalue()
        logger.info("WS final STT | buf=%d bytes", len(buf))
        if len(buf) < 1000:
            logger.warning("WS final STT skipped — buffer too small (%d bytes)", len(buf))
            return ("", self.language, 0.0)
        stt = get_stt_service()
        text, lang, conf = await stt.transcribe_buffer(buf, language=self.language)
        logger.info("WS final STT result | text='%s' lang=%s conf=%.2f", text[:100] if text else "", lang, conf)
        return text, lang, conf

    async def run_pipeline(self, transcript: str):
        """Orchestrator -> LLM stream -> TTS stream."""
        from routers.voice import _sync_and_fetch_context

        ws = self.ws
        llm_service = get_llm_service()
        rag_service = get_rag_service()
        tts_service = get_tts_service()
        orchestrator = get_orchestrator()

        emails_data, tasks_data, events_data = [], [], []
        async with AsyncSessionLocal() as db:
            emails_data, tasks_data, events_data = await _sync_and_fetch_context(db)

        rag_has_docs = rag_service.get_status().get("indexed_chunks", 0) > 0

        decision = await orchestrator.route(
            message=transcript,
            language=self.language,
            available_context={
                "has_policy_docs": rag_has_docs,
                "has_emails": bool(emails_data),
                "has_tasks": bool(tasks_data),
                "has_calendar": bool(events_data),
            },
        )
        logger.info(
            "WS pipeline | intent=%s conf=%.2f voice=%s",
            decision.intent, decision.confidence, self.voice_mode,
        )
        await _safe_send_json(ws, {"type": "route", "intent": decision.intent})

        if self.cancelled:
            return

        full_response = ""
        token_buffer = ""
        _in_think = False
        _think_buf = ""

        async def _send_token(ws, tok: str):
            """Send token, filtering <think> blocks: thinking tokens go as
            type=thinking (not sent to TTS), normal tokens as type=token."""
            nonlocal _in_think, _think_buf, full_response
            i = 0
            while i < len(tok):
                if not _in_think:
                    start = tok.find("<think>", i)
                    if start == -1:
                        chunk = tok[i:]
                        full_response += chunk
                        await _safe_send_json(ws, {"type": "token", "content": chunk})
                        await drain_tokens(chunk)
                        break
                    else:
                        before = tok[i:start]
                        if before:
                            full_response += before
                            await _safe_send_json(ws, {"type": "token", "content": before})
                            await drain_tokens(before)
                        _in_think = True
                        _think_buf = ""
                        await _safe_send_json(ws, {"type": "thinking_start"})
                        i = start + len("<think>")
                else:
                    end = tok.find("</think>", i)
                    if end == -1:
                        _think_buf += tok[i:]
                        await _safe_send_json(ws, {"type": "thinking", "content": tok[i:]})
                        break
                    else:
                        chunk = tok[i:end]
                        if chunk:
                            _think_buf += chunk
                            await _safe_send_json(ws, {"type": "thinking", "content": chunk})
                        _in_think = False
                        await _safe_send_json(ws, {"type": "thinking_end"})
                        i = end + len("</think>")

        async def flush_sentence(sentence: str):
            """Synthesise one sentence and push as binary frame."""
            nonlocal full_response
            clean = sentence.strip()
            if not clean or len(clean) < 3:
                return
            logger.info("WS TTS start | len=%d text='%s'", len(clean), clean[:60])
            await _safe_send_json(ws, {"type": "tts_start", "sentence": clean})
            try:
                audio = await tts_service.synthesize(
                    text=clean, language=self.language, gender="male",
                )
                if audio:
                    logger.info("WS TTS done | audio=%d bytes", len(audio))
                    await _safe_send_bytes(ws, audio)
                else:
                    logger.warning("WS TTS returned empty audio")
            except Exception as exc:
                logger.warning("TTS failed for sentence: %s", exc)

        async def drain_tokens(token: str):
            """Buffer tokens, flush complete sentences for TTS."""
            nonlocal token_buffer
            if self.cancelled:
                return
            token_buffer += token
            while True:
                m = SENTENCE_RE.search(token_buffer)
                if m is not None and len(token_buffer[:m.end()]) >= MIN_SENTENCE_CHARS:
                    sentence = token_buffer[: m.end()]
                    token_buffer = token_buffer[m.end():]
                    if self.voice_mode:
                        await flush_sentence(sentence)
                    continue
                if len(token_buffer) >= MAX_CHUNK_CHARS:
                    space = token_buffer.rfind(" ", MIN_SENTENCE_CHARS, MAX_CHUNK_CHARS)
                    cut = space if space > 0 else MAX_CHUNK_CHARS
                    sentence = token_buffer[:cut]
                    token_buffer = token_buffer[cut:].lstrip()
                    if self.voice_mode:
                        await flush_sentence(sentence)
                    continue
                break

        if decision.intent == ROUTE_POLICY_QA and rag_has_docs:
            hits, debug_info = await rag_service.search_hits(transcript)
            qa = rag_service.qa_engine
            raw_tokens = ""
            async for evt in qa.answer_stream(
                transcript, hits, debug_info, voice_mode=self.voice_mode
            ):
                if self.cancelled:
                    break
                if evt["type"] == "token":
                    raw_tokens += evt["content"]
                    await _send_token(ws, evt["content"])
                elif evt["type"] == "meta":
                    answer_text = evt.get("answer_ar", raw_tokens)
                    citations = evt.get("citations", [])
                    confidence = evt.get("confidence", "low")
                    is_not_found = (
                        (answer_text == "غير موجود"
                         or "لم أجد معلومات" in answer_text)
                        and confidence == "low"
                    )
                    if is_not_found:
                        full_response = ""
                        token_buffer = ""
                        await _safe_send_json(ws, {"type": "clear"})
                        async for tok in llm_service.chat_stream(
                            transcript, self.language,
                            voice_mode=self.voice_mode,
                        ):
                            if self.cancelled:
                                break
                            await _send_token(ws, tok)
                    else:
                        full_response = answer_text
                        if citations:
                            refs = "\n\nالمراجع:"
                            for c in citations:
                                ref = f"\n- البند {c['section_id']}"
                                if c.get("section_title"):
                                    ref += f" ({c['section_title']})"
                                ref += f"، صفحة {c['page']}"
                                if c.get("quote"):
                                    ref += f': \"{c["quote"]}\"'
                                refs += ref
                            full_response += refs
                            await _safe_send_json(
                                ws,
                                {
                                    "type": "citations",
                                    "citations": citations,
                                    "refs_text": refs,
                                },
                            )
        elif decision.intent == ROUTE_WORKSPACE:
            async for tok in llm_service.contextual_chat_stream(
                message=transcript,
                emails=emails_data, tasks=tasks_data, events=events_data,
                language=self.language, voice_mode=self.voice_mode,
            ):
                if self.cancelled:
                    break
                await _send_token(ws, tok)
        else:
            async for tok in llm_service.chat_stream(
                transcript, self.language, voice_mode=self.voice_mode,
            ):
                if self.cancelled:
                    break
                await _send_token(ws, tok)

        if token_buffer.strip() and self.voice_mode and not self.cancelled:
            await flush_sentence(token_buffer)
            token_buffer = ""

        if not self.cancelled:
            await _safe_send_json(
                ws, {"type": "done", "full_response": full_response}
            )


@router.websocket("/ws/voice")
async def voice_websocket(ws: WebSocket):
    await ws.accept()
    session = _VoiceSession(ws)
    logger.info("WS voice connected")

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                chunk = message["bytes"]
                if session.audio_buffer.tell() == 0:
                    logger.info("WS first audio chunk received | size=%d", len(chunk))
                await session.handle_audio_chunk(chunk)
                continue

            raw_text = message.get("text")
            if not raw_text:
                continue

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "config":
                session.language = data.get("language", session.language)
                session.voice_mode = data.get("voice_mode", session.voice_mode)
                logger.info(
                    "WS config | lang=%s voice_mode=%s",
                    session.language, session.voice_mode,
                )

            elif msg_type == "end_audio":
                logger.info("WS end_audio received | total_buf=%d bytes", session.audio_buffer.tell())
                session.pipeline_running = True

                async def _run_end_audio_pipeline():
                    try:
                        text, lang, conf = await session.run_final_transcription()
                        if not text.strip():
                            await _safe_send_json(ws, {
                                "type": "transcript",
                                "text": "",
                                "language": session.language,
                                "confidence": 0.0,
                            })
                            session.reset()
                            return

                        await _safe_send_json(ws, {
                            "type": "transcript",
                            "text": text,
                            "language": lang,
                            "confidence": conf,
                        })

                        await session.run_pipeline(text)
                    except Exception as exc:
                        logger.error("WS pipeline error: %s", exc, exc_info=True)
                        await _safe_send_json(
                            ws, {"type": "error", "message": str(exc)}
                        )
                    finally:
                        session.reset()

                asyncio.create_task(_run_end_audio_pipeline())

            elif msg_type == "cancel":
                session.cancelled = True
                session.reset()
                await _safe_send_json(ws, {"type": "done", "full_response": ""})

    except WebSocketDisconnect:
        logger.info("WS voice disconnected")
    except Exception as exc:
        logger.error("WS voice unexpected error: %s", exc, exc_info=True)
    finally:
        session.reset()
