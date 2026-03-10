"""
Voice Router - Handles speech-to-text, text-to-speech, and chat endpoints
"""
import json as _json

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, List
import io

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from database import get_db, User, Email, Task, TaskStatus, CalendarEvent
from services.stt_service import get_stt_service
from services.tts_service import get_tts_service, TTSService
from services.llm_service import get_llm_service, LLMService
from services.rag_service import get_rag_service
from services.orchestrator import get_orchestrator, ROUTE_POLICY_QA, ROUTE_WORKSPACE
from services.logger import get_chat_logger, log_request, log_response, log_error

settings = get_settings()

router = APIRouter()
logger = get_chat_logger()


async def get_current_user(db: AsyncSession) -> Optional[User]:
    """Get the first authenticated user (POC: single user)."""
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    return result.scalar_one_or_none()


async def _sync_and_fetch_context(db: AsyncSession) -> tuple[list, list, list]:
    """Sync fresh data from Outlook, then return (emails, tasks, events).

    Silently returns cached/empty data on sync failures so the chat
    pipeline always proceeds.
    """
    from datetime import datetime as dt_cls
    from services.outlook_service import get_outlook_service

    user = await get_current_user(db)
    if not user:
        return [], [], []

    outlook = get_outlook_service()

    if user.access_token:
        try:
            token = await outlook.refresh_token_if_needed(db, user)
        except Exception as e:
            logger.warning("Outlook token refresh failed (stale session): %s", e)
            user.access_token = None
            user.refresh_token = None
            user.token_expires_at = None
            await db.commit()
            token = None

        if token:
            try:
                await outlook.sync_emails_to_db(db=db, user=user, access_token=token, limit=20)
            except Exception as e:
                logger.warning("Email sync skipped: %s", e)
            try:
                from datetime import date
                await outlook.sync_events_to_db(db, user, token, target_date=date.today(), days_ahead=14)
            except Exception as e:
                logger.warning("Calendar sync skipped: %s", e)

    email_result = await db.execute(
        select(Email).where(Email.user_id == user.id)
        .order_by(Email.received_at.desc()).limit(15)
    )
    emails_data = [
        {"sender_name": e.sender_name, "sender_email": e.sender_email,
         "subject": e.subject, "body_preview": e.body_preview,
         "received_at": e.received_at.isoformat() if e.received_at else None,
         "is_read": e.is_read, "urgency": e.urgency}
        for e in email_result.scalars().all()
    ]

    task_result = await db.execute(
        select(Task).where(
            Task.user_id == user.id,
            Task.status.in_([TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVED]),
        ).order_by(Task.created_at.desc()).limit(15)
    )
    tasks_data = [
        {"title": t.title, "description": t.description,
         "status": t.status.value, "priority": t.priority.value,
         "due_date": t.due_date.isoformat() if t.due_date else None}
        for t in task_result.scalars().all()
    ]

    from datetime import timedelta as _td
    today_start = dt_cls.combine(dt_cls.now().date(), dt_cls.min.time())
    range_end = dt_cls.combine(dt_cls.now().date() + _td(days=14), dt_cls.max.time())
    event_result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.user_id == user.id,
            CalendarEvent.start_time >= today_start,
            CalendarEvent.start_time <= range_end,
        ).order_by(CalendarEvent.start_time).limit(50)
    )
    events_data = [
        {"subject": ev.subject,
         "start_time": ev.start_time.isoformat() if ev.start_time else None,
         "end_time": ev.end_time.isoformat() if ev.end_time else None,
         "location": ev.location, "is_online": ev.is_online,
         "online_meeting_url": ev.online_meeting_url,
         "organizer": ev.organizer_name, "status": ev.status}
        for ev in event_result.scalars().all()
    ]

    logger.info("Context fetched: %d emails, %d tasks, %d events",
                len(emails_data), len(tasks_data), len(events_data))
    return emails_data, tasks_data, events_data


class TranscriptionResponse(BaseModel):
    text: str
    language: str
    confidence: Optional[float] = None


class TTSRequest(BaseModel):
    text: str
    language: Optional[str] = "en"  # 'en' or 'ar'
    gender: Optional[str] = "male"  # 'male' or 'female'


class ChatRequest(BaseModel):
    message: str
    language: Optional[str] = "en"
    include_context: Optional[bool] = True
    voice_mode: Optional[bool] = False


class ChatResponse(BaseModel):
    response: str
    language: str


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: Optional[str] = Form(None)
):
    """
    Transcribe audio file to text using Whisper.
    Supports Arabic and English with auto-detection or explicit language hint.
    
    Accepts: audio/webm, audio/ogg, audio/mp3, audio/wav, audio/m4a
    """
    content_type = audio.content_type or ""
    if not any(t in content_type for t in ["audio", "video/webm"]):
        pass
    
    # Validate language hint if provided
    valid_languages = {'en', 'ar'}
    lang_hint = language if language in valid_languages else None
    
    try:
        audio_data = await audio.read()
        
        if len(audio_data) == 0:
            raise HTTPException(status_code=400, detail="Empty audio file")
        
        stt = get_stt_service()
        text, detected_lang, confidence = await stt.transcribe_buffer(audio_data, language=lang_hint)
        
        if not text:
            return TranscriptionResponse(
                text="",
                language=lang_hint or "en",
                confidence=0.0
            )
        
        return TranscriptionResponse(
            text=text,
            language=detected_lang,
            confidence=confidence
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@router.post("/speak")
async def text_to_speech(request: TTSRequest):
    """
    Convert text to speech using Edge TTS.
    Returns audio stream (MP3 format).
    
    Available voices:
    - Arabic: ar-SA-HamedNeural (male), ar-SA-ZariyahNeural (female)
    - English: en-US-GuyNeural (male), en-US-JennyNeural (female)
    """
    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")
    
    try:
        tts_service = get_tts_service()
        logger.info("TTS /speak | backend=%s | lang=%s | gender=%s | text_len=%d",
                     settings.tts_backend, request.language, request.gender, len(request.text))
        audio_data = await tts_service.synthesize(
            text=request.text,
            language=request.language or "en",
            gender=request.gender or "male"
        )

        backend_lower = settings.tts_backend.lower()
        is_wav = (
            backend_lower == "namaa"
            and (request.language or "en").startswith("ar")
        )
        media_type = "audio/wav" if is_wav else "audio/mpeg"
        ext = "wav" if is_wav else "mp3"
        
        if not audio_data:
            logger.info("TTS /speak skipped (text too short)")
            return Response(content=b"", media_type=media_type, status_code=204)

        logger.info("TTS /speak OK | bytes=%d | media=%s", len(audio_data), media_type)
        return Response(
            content=audio_data,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename=speech.{ext}"
            }
        )
        
    except Exception as e:
        logger.error("TTS /speak FAILED: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


@router.post("/speak/stream")
async def text_to_speech_stream(request: TTSRequest):
    """
    Convert text to speech with streaming response.
    """
    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")
    
    try:
        tts_service = get_tts_service()
        
        async def audio_stream():
            async for chunk in tts_service.stream_audio(
                text=request.text,
                language=request.language or "en",
                gender=request.gender or "male"
            ):
                yield chunk
        
        return StreamingResponse(
            audio_stream(),
            media_type="audio/mpeg"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS streaming failed: {str(e)}")


async def _handle_policy_qa(
    llm_service: LLMService,
    rag_service,
    message: str,
    language: str,
) -> str:
    """Handle a policy-related question via hybrid retrieval + grounded QA."""
    extracted = await llm_service.try_extract_profile_from_message(message)
    if extracted:
        llm_service.update_employee_profile(extracted)

    try:
        qa_result = await rag_service.answer(message)
    except Exception as e:
        logger.error("Grounded QA failed (exception), falling back to general chat: %s", e, exc_info=True)
        return await llm_service.chat(message=message, language=language)

    logger.info(
        "QA result | confidence=%s | citations=%d | answer_len=%d",
        qa_result.confidence,
        len(qa_result.citations),
        len(qa_result.answer_ar),
    )

    if qa_result.answer_ar == "غير موجود" and qa_result.confidence == "low":
        logger.warning("QA returned 'not found', falling back to general chat")
        return await llm_service.chat(message=message, language=language)

    # Format grounded answer with citations
    answer = qa_result.answer_ar
    if qa_result.citations:
        answer += "\n\nالمراجع:"
        for c in qa_result.citations:
            ref = f"\n- البند {c.section_id}"
            if c.section_title:
                ref += f" ({c.section_title})"
            ref += f"، صفحة {c.page}"
            if c.quote:
                ref += f': "{c.quote}"'
            answer += ref

    return answer


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def voice_chat_stream(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Stream chat response as Server-Sent Events (SSE).

    Event types:
        token   – incremental LLM text  {"type":"token","content":"..."}
        citations – policy QA refs       {"type":"citations","citations":[...]}
        done    – final event            {"type":"done","full_response":"...","language":"..."}
        error   – on failure             {"type":"error","message":"..."}
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    log_request(logger, "/chat/stream", {"message": request.message[:100], "language": request.language})

    async def event_generator():
        try:
            llm_service = get_llm_service()
            language = request.language or "en"

            emails_data, tasks_data, events_data = [], [], []
            if request.include_context:
                emails_data, tasks_data, events_data = await _sync_and_fetch_context(db)
                if not emails_data and not tasks_data and not events_data:
                    yield _sse_event({"type": "auth_required"})

            rag_service = get_rag_service()
            rag_has_docs = rag_service.get_status()["indexed_chunks"] > 0

            orchestrator = get_orchestrator()
            decision = await orchestrator.route(
                message=request.message,
                language=language,
                available_context={
                    "has_policy_docs": rag_has_docs,
                    "has_emails": bool(emails_data),
                    "has_tasks": bool(tasks_data),
                    "has_calendar": bool(events_data),
                },
            )
            voice_mode = bool(request.voice_mode)
            logger.info("STREAM ROUTE | intent=%s conf=%.2f voice=%s",
                        decision.intent, decision.confidence, voice_mode)
            yield _sse_event({"type": "route", "intent": decision.intent,
                              "voice_mode": voice_mode})

            full_response = ""
            _in_think = False

            def _filter_think(tok):
                """Yield (event_type, content) tuples, splitting <think> blocks."""
                nonlocal _in_think
                i = 0
                while i < len(tok):
                    if not _in_think:
                        start = tok.find("<think>", i)
                        if start == -1:
                            yield ("token", tok[i:])
                            break
                        else:
                            before = tok[i:start]
                            if before:
                                yield ("token", before)
                            _in_think = True
                            yield ("thinking_start", "")
                            i = start + len("<think>")
                    else:
                        end = tok.find("</think>", i)
                        if end == -1:
                            yield ("thinking", tok[i:])
                            break
                        else:
                            chunk = tok[i:end]
                            if chunk:
                                yield ("thinking", chunk)
                            _in_think = False
                            yield ("thinking_end", "")
                            i = end + len("</think>")

            if decision.intent == ROUTE_POLICY_QA and rag_has_docs:
                logger.info(">>> Stream: POLICY QA (voice=%s)", voice_mode)
                extracted = await llm_service.try_extract_profile_from_message(request.message)
                if extracted:
                    llm_service.update_employee_profile(extracted)

                hits, debug_info = await rag_service.search_hits(request.message)
                qa = rag_service.qa_engine

                async for evt in qa.answer_stream(request.message, hits, debug_info,
                                                   voice_mode=voice_mode):
                    if evt["type"] == "token":
                        for etype, econtent in _filter_think(evt["content"]):
                            if etype == "token":
                                full_response += econtent
                            yield _sse_event({"type": etype, "content": econtent})
                    elif evt["type"] == "meta":
                        answer_text = evt.get("answer_ar", full_response)
                        confidence = evt.get("confidence", "low")

                        is_not_found = (
                            (answer_text == "غير موجود" or
                             "لم أجد معلومات" in answer_text)
                            and confidence == "low"
                        )
                        if is_not_found:
                            full_response = ""
                            yield _sse_event({"type": "clear"})
                            async for token in llm_service.chat_stream(
                                request.message, language, voice_mode=voice_mode
                            ):
                                for etype, econtent in _filter_think(token):
                                    if etype == "token":
                                        full_response += econtent
                                    yield _sse_event({"type": etype, "content": econtent})

            elif decision.intent == ROUTE_WORKSPACE and (emails_data or tasks_data or events_data):
                logger.info(">>> Stream: WORKSPACE (voice=%s)", voice_mode)
                async for token in llm_service.contextual_chat_stream(
                    message=request.message, emails=emails_data,
                    tasks=tasks_data, events=events_data, language=language,
                    voice_mode=voice_mode,
                ):
                    for etype, econtent in _filter_think(token):
                        if etype == "token":
                            full_response += econtent
                        yield _sse_event({"type": etype, "content": econtent})
            else:
                logger.info(">>> Stream: GENERAL (voice=%s)", voice_mode)
                async for token in llm_service.chat_stream(
                    request.message, language, voice_mode=voice_mode
                ):
                    for etype, econtent in _filter_think(token):
                        if etype == "token":
                            full_response += econtent
                        yield _sse_event({"type": etype, "content": econtent})

            resp_lang = llm_service.detect_language(full_response) if full_response else language
            yield _sse_event({"type": "done", "full_response": full_response, "language": resp_lang})
            log_response(logger, "/chat/stream", "success", {"response_length": len(full_response)})

        except Exception as e:
            logger.error("Stream error: %s", e, exc_info=True)
            yield _sse_event({"type": "error", "message": str(e)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/chat", response_model=ChatResponse)
async def voice_chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    Process a chat message and return AI response.
    Uses Ollama LLM for intelligent responses with context from emails and tasks.
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    
    # Log incoming request
    log_request(logger, "/chat", {"message": request.message[:100], "language": request.language, "context": request.include_context})
    
    try:
        llm_service = get_llm_service()
        logger.debug(f"LLM Service initialized | model={llm_service.provider.model_name}")
        
        emails_data, tasks_data, events_data = [], [], []
        if request.include_context:
            emails_data, tasks_data, events_data = await _sync_and_fetch_context(db)
        
        # ---- Orchestrator: route message to the right agent ----
        language = request.language or "en"
        rag_service = get_rag_service()
        rag_has_docs = rag_service.get_status()["indexed_chunks"] > 0

        orchestrator = get_orchestrator()
        decision = await orchestrator.route(
            message=request.message,
            language=language,
            available_context={
                "has_policy_docs": rag_has_docs,
                "has_emails": bool(emails_data),
                "has_tasks": bool(tasks_data),
                "has_calendar": bool(events_data),
            },
        )

        logger.info(
            "CHAT ROUTE | intent=%s conf=%.2f | rag_has_docs=%s | reason=%s",
            decision.intent, decision.confidence, rag_has_docs, decision.reasoning,
        )

        if decision.intent == ROUTE_POLICY_QA and rag_has_docs:
            logger.info(">>> Routing to POLICY QA agent")
            response_text = await _handle_policy_qa(
                llm_service, rag_service, request.message, language
            )
        elif decision.intent == ROUTE_WORKSPACE and (emails_data or tasks_data or events_data):
            logger.info(">>> Routing to WORKSPACE agent (contextual chat)")
            response_text = await llm_service.contextual_chat(
                message=request.message,
                emails=emails_data,
                tasks=tasks_data,
                events=events_data,
                language=language,
            )
        else:
            logger.info(">>> Routing to GENERAL chat agent (no policy/workspace match)")
            response_text = await llm_service.chat(
                message=request.message, language=language
            )
        
        # Detect response language
        response_lang = llm_service.detect_language(response_text)
        
        log_response(logger, "/chat", "success", {"response_length": len(response_text)})
        logger.debug(f"Response preview: {response_text[:200]}...")
        
        return ChatResponse(
            response=response_text,
            language=response_lang
        )
        
    except Exception as e:
        error_msg = str(e)
        log_error(logger, "/chat", e, {"message": request.message[:50]})
        
        # Provide helpful error message based on the error type
        if "Cannot connect" in error_msg or "ConnectError" in error_msg:
            fallback = {
                'en': "I cannot connect to the AI service. Please check that your LLM backend is running.",
                'ar': "لا يمكنني الاتصال بخدمة الذكاء الاصطناعي. يرجى التأكد من تشغيل الخدمة."
            }
        elif "404" in error_msg:
            model = get_llm_service().provider.model_name
            fallback = {
                'en': f"The AI model '{model}' is not available. Check your configuration.",
                'ar': f"نموذج الذكاء الاصطناعي '{model}' غير متوفر. تحقق من الإعدادات."
            }
        else:
            fallback = {
                'en': f"I received your message but encountered an error: {error_msg}",
                'ar': f"استلمت رسالتك لكن حدث خطأ: {error_msg}"
            }
        
        return ChatResponse(
            response=fallback.get(request.language, fallback['en']),
            language=request.language or "en"
        )


@router.get("/voices")
async def list_available_voices(language: Optional[str] = None):
    """
    List available TTS voices, optionally filtered by language.
    """
    try:
        tts_service = get_tts_service()
        voices = await tts_service.list_voices(language)
        return {"voices": voices}
    except Exception as e:
        return {"voices": [], "error": str(e)}
