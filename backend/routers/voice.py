"""
Voice Router - Handles speech-to-text, text-to-speech, and chat endpoints
"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, List
import io

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, User, Email, Task, TaskStatus
from services.whisper_service import get_whisper_service, WhisperService
from services.tts_service import get_tts_service, TTSService
from services.llm_service import get_llm_service, LLMService
from services.logger import get_chat_logger, log_request, log_response, log_error

router = APIRouter()
logger = get_chat_logger()


async def get_current_user(db: AsyncSession) -> Optional[User]:
    """Get the first authenticated user (POC: single user)."""
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    return result.scalar_one_or_none()


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
    include_context: Optional[bool] = True  # Include emails/tasks context


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
        
        whisper_service = get_whisper_service()
        text, detected_lang, confidence = await whisper_service.transcribe(audio_data, language=lang_hint)
        
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
        audio_data = await tts_service.synthesize(
            text=request.text,
            language=request.language or "en",
            gender=request.gender or "male"
        )
        
        return Response(
            content=audio_data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=speech.mp3"
            }
        )
        
    except Exception as e:
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
        logger.debug(f"LLM Service initialized | model={llm_service.model} | base_url={llm_service.base_url}")
        
        emails_data = []
        tasks_data = []
        
        # Fetch context if requested
        if request.include_context:
            user = await get_current_user(db)
            
            if user:
                # Fetch recent emails
                email_result = await db.execute(
                    select(Email)
                    .where(Email.user_id == user.id)
                    .order_by(Email.received_at.desc())
                    .limit(10)
                )
                emails = email_result.scalars().all()
                emails_data = [
                    {
                        "id": e.id,
                        "sender_name": e.sender_name,
                        "sender_email": e.sender_email,
                        "subject": e.subject,
                        "body_preview": e.body_preview,
                        "received_at": e.received_at.isoformat() if e.received_at else None,
                        "is_read": e.is_read,
                        "urgency": e.urgency,
                        "summary": e.summary
                    }
                    for e in emails
                ]
                logger.debug(f"Loaded {len(emails_data)} emails for context")
                
                # Fetch tasks
                task_result = await db.execute(
                    select(Task)
                    .where(Task.user_id == user.id)
                    .where(Task.status.in_([TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVED]))
                    .order_by(Task.created_at.desc())
                    .limit(15)
                )
                tasks = task_result.scalars().all()
                tasks_data = [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "status": t.status.value,
                        "priority": t.priority.value,
                        "due_date": t.due_date.isoformat() if t.due_date else None
                    }
                    for t in tasks
                ]
                logger.debug(f"Loaded {len(tasks_data)} tasks for context")
        
        # Generate response with or without context
        logger.info(f"Sending message to Ollama with context...")
        
        if emails_data or tasks_data:
            # Use contextual chat when we have data
            response_text = await llm_service.contextual_chat(
                message=request.message,
                emails=emails_data,
                tasks=tasks_data,
                language=request.language or "en"
            )
        else:
            # Fall back to regular chat
            response_text = await llm_service.chat(
                message=request.message,
                language=request.language or "en"
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
        if "Cannot connect to Ollama" in error_msg:
            fallback = {
                'en': "I cannot connect to the AI service (Ollama). Please make sure Ollama is running with: ollama serve",
                'ar': "لا يمكنني الاتصال بخدمة الذكاء الاصطناعي (Ollama). يرجى التأكد من تشغيل Ollama باستخدام: ollama serve"
            }
        elif "404" in error_msg:
            fallback = {
                'en': f"The AI model is not installed. Please run: ollama pull {get_llm_service().model}",
                'ar': f"نموذج الذكاء الاصطناعي غير مثبت. يرجى تشغيل: ollama pull {get_llm_service().model}"
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
