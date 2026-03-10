"""
Smart Work Assistant - Main FastAPI Application
"""
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from database import init_db, get_db, User, Email, Task, TaskStatus, CalendarEvent
from routers import voice, email, tasks, calendar, contacts, policy
from routers import ws_voice
from services.llm_service import get_llm_service

settings = get_settings()


# ============ Pydantic Models ============

class DailySummaryTask(BaseModel):
    id: int
    title: str
    priority: str
    status: str
    due_date: Optional[datetime] = None


class DailySummaryEmail(BaseModel):
    id: int
    subject: str
    sender_name: str
    urgency: Optional[str] = None


class DailySummaryResponse(BaseModel):
    date: str
    tasks: List[DailySummaryTask]
    emails_requiring_action: List[DailySummaryEmail]
    summary_text: str
    stats: dict


# ============ Lifespan ============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup
    print(f"Starting {settings.app_name}...")
    await init_db()
    print("Database initialized.")

    # Auto-ingest policy documents if they exist and index is empty or backend changed
    try:
        from services.rag_service import get_rag_service
        rag = get_rag_service()
        status = rag.get_status()
        has_docs = status.get("document_count", 0) > 0
        has_index = status.get("indexed_chunks", 0) > 0
        stored_backend = status.get("embed_backend") or ""
        embed_backend = settings.effective_embed_backend
        if embed_backend == "huggingface":
            current_model = settings.hf_embed_model
        elif embed_backend == "openrouter":
            current_model = settings.or_embed_model
        else:
            current_model = settings.ollama_embed_model
        current_marker = f"{embed_backend}:{current_model}"

        needs_reingest = False
        if has_docs and not has_index:
            print("Auto-ingesting policy documents (no index found)...")
            needs_reingest = True
        elif has_docs and has_index and stored_backend and stored_backend != current_marker:
            print(
                f"Embedding config changed: '{stored_backend}' -> '{current_marker}'. "
                f"Auto-re-ingesting to rebuild vectors..."
            )
            needs_reingest = True
        elif has_docs and has_index and not stored_backend:
            print(
                f"No embedding backend marker found (legacy index). "
                f"Re-ingesting with current backend '{current_marker}'..."
            )
            needs_reingest = True
        elif has_docs and has_index:
            print(f"Policy index loaded: {status['indexed_chunks']} chunks, {status['document_count']} documents. (backend={stored_backend})")
        else:
            print("No policy documents found. Upload via /api/policy/upload.")

        if needs_reingest:
            result = await rag.ingest()
            print(f"Ingestion complete: {result.get('chunks', 0)} chunks from {result.get('documents', 0)} documents.")
    except Exception as e:
        print(f"Policy index check skipped: {e}")

    # Pre-load XTTS model at startup so the first TTS request is fast.
    # Requires reload=False (CUDA deadlocks in uvicorn's reloader subprocess).
    if settings.tts_backend.lower() == "xtts":
        try:
            from services.tts_service import get_tts_service
            tts = get_tts_service()
            if hasattr(tts, "preload"):
                print("Pre-loading XTTS-v2 model (~15-20 s)...", flush=True)
                tts.preload()
                print("XTTS-v2 model ready.", flush=True)
        except Exception as e:
            print(f"XTTS preload failed (will lazy-load on first request): {e}",
                  flush=True)

    # Warm up cloud embedding model so the first user query doesn't hit a cold start.
    if settings.effective_embed_backend in ("huggingface", "openrouter"):
        try:
            from services.rag_service import get_rag_service
            rag_svc = get_rag_service()
            embedder = rag_svc._embedder
            if hasattr(embedder, "embed_single"):
                backend_name = "HF" if settings.effective_embed_backend == "huggingface" else "OpenRouter"
                print(f"Warming up {backend_name} embedding model...", flush=True)
                await embedder.embed_single("warmup")
                print(f"{backend_name} embedding model warm.", flush=True)
        except Exception as e:
            print(f"Embed warmup skipped: {e}", flush=True)

    yield
    
    # Shutdown
    print("Shutting down...")


# ============ App Setup ============

app = FastAPI(
    title=settings.app_name,
    description="AI-powered assistant for managing meetings and emails with voice interaction in Arabic and English",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False  # Disable redirect to prevent POST->GET conversion
)

# CORS middleware for PWA
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(voice.router, prefix="/api/voice", tags=["Voice"])
app.include_router(email.router, prefix="/api/emails", tags=["Email"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(calendar.router, prefix="/api/calendar", tags=["Calendar"])
app.include_router(contacts.router, prefix="/api/contacts", tags=["Contacts"])
app.include_router(policy.router, prefix="/api/policy", tags=["Policy"])
app.include_router(ws_voice.router, tags=["WebSocket Voice"])


# ============ Helper Functions ============

async def get_current_user(db: AsyncSession) -> Optional[User]:
    """Get the first authenticated user (POC: single user)."""
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    user = result.scalar_one_or_none()
    
    if not user:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
    
    return user


# ============ Endpoints ============

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": "0.1.0",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/summary/daily", response_model=DailySummaryResponse)
async def get_daily_summary(
    language: str = "en",
    db: AsyncSession = Depends(get_db)
):
    """
    Get daily summary of tasks and emails.
    Includes AI-generated natural language summary.
    """
    user = await get_current_user(db)
    
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    if not user:
        return DailySummaryResponse(
            date=today,
            tasks=[],
            emails_requiring_action=[],
            summary_text="Welcome! Connect your Outlook account to get started.",
            stats={"pending": 0, "active": 0, "completed": 0, "emails_action": 0}
        )
    
    # Get pending and active tasks
    task_result = await db.execute(
        select(Task)
        .where(Task.user_id == user.id)
        .where(Task.status.in_([TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVED]))
        .order_by(Task.created_at.desc())
        .limit(20)
    )
    active_tasks = task_result.scalars().all()
    
    # Get completed tasks (today)
    completed_result = await db.execute(
        select(Task)
        .where(Task.user_id == user.id)
        .where(Task.status == TaskStatus.COMPLETED)
        .limit(10)
    )
    completed_tasks = completed_result.scalars().all()
    
    # Get emails requiring action (unread or high urgency)
    email_result = await db.execute(
        select(Email)
        .where(Email.user_id == user.id)
        .where(
            (Email.is_read == False) | 
            (Email.urgency == "high") |
            (Email.draft_reply.isnot(None) & (Email.reply_sent == False))
        )
        .order_by(Email.received_at.desc())
        .limit(10)
    )
    action_emails = email_result.scalars().all()
    
    # Calculate stats
    pending_count = sum(1 for t in active_tasks if t.status == TaskStatus.PENDING_APPROVAL)
    active_count = sum(1 for t in active_tasks if t.status == TaskStatus.APPROVED)
    completed_count = len(completed_tasks)
    
    # Convert to response models
    task_summaries = [
        DailySummaryTask(
            id=t.id,
            title=t.title,
            priority=t.priority.value,
            status=t.status.value,
            due_date=t.due_date
        )
        for t in active_tasks
    ]
    
    email_summaries = [
        DailySummaryEmail(
            id=e.id,
            subject=e.subject,
            sender_name=e.sender_name,
            urgency=e.urgency
        )
        for e in action_emails
    ]
    
    # Get calendar events for the next 14 days
    from datetime import timedelta as _td
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    range_end = datetime.combine(datetime.utcnow().date() + _td(days=14), datetime.max.time())
    event_result = await db.execute(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user.id,
            CalendarEvent.start_time >= today_start,
            CalendarEvent.start_time <= range_end,
        )
        .order_by(CalendarEvent.start_time)
        .limit(50)
    )
    today_events = event_result.scalars().all()

    # Generate natural language summary
    try:
        llm_service = get_llm_service()

        tasks_for_llm = [
            {"title": t.title, "priority": t.priority.value, "status": t.status.value,
             "due_date": t.due_date.isoformat() if t.due_date else None}
            for t in active_tasks[:15]
        ]

        emails_for_llm = [
            {"sender_name": e.sender_name, "sender_email": e.sender_email,
             "subject": e.subject, "body_preview": e.body_preview,
             "received_at": e.received_at.isoformat() if e.received_at else None,
             "is_read": e.is_read, "urgency": e.urgency}
            for e in action_emails[:10]
        ]

        events_for_llm = [
            {"subject": ev.subject,
             "start_time": ev.start_time.isoformat() if ev.start_time else None,
             "end_time": ev.end_time.isoformat() if ev.end_time else None,
             "location": ev.location, "is_online": ev.is_online,
             "organizer": ev.organizer_name}
            for ev in today_events
        ]

        summary_text = await llm_service.generate_daily_summary(
            tasks=tasks_for_llm,
            emails=emails_for_llm,
            events=events_for_llm,
            language=language,
        )
    except Exception as e:
        # Fallback summary if LLM fails
        if language == "ar":
            summary_text = f"لديك {pending_count} مهام تنتظر الموافقة و{active_count} مهام نشطة و{len(action_emails)} رسائل تتطلب اهتمامك."
        else:
            summary_text = f"You have {pending_count} tasks pending approval, {active_count} active tasks, and {len(action_emails)} emails requiring attention."
    
    return DailySummaryResponse(
        date=today,
        tasks=task_summaries,
        emails_requiring_action=email_summaries,
        summary_text=summary_text,
        stats={
            "pending": pending_count,
            "active": active_count,
            "completed": completed_count,
            "emails_action": len(action_emails)
        }
    )


@app.get("/api/config")
async def get_app_config():
    """Get app configuration (non-sensitive)."""
    backend = settings.llm_backend.lower()
    llm_model = {
        "vllm": settings.vllm_llm_model,
        "openrouter": settings.or_llm_model,
        "huggingface": settings.hf_llm_model,
    }.get(backend, settings.ollama_model)

    return {
        "app_name": settings.app_name,
        "llm_backend": settings.llm_backend,
        "llm_model": llm_model,
        "tts_backend": settings.tts_backend,
        "stt_backend": settings.stt_backend,
        "stt_model": settings.or_stt_model if settings.stt_backend == "openrouter" else settings.whisper_model,
        "tts_voice_arabic": settings.tts_voice_arabic,
        "tts_voice_english": settings.tts_voice_english,
        "azure_configured": bool(settings.azure_client_id),
    }


# Serve frontend static files (in production)
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")


def _kill_port(port: int = 8000):
    """Kill any process currently listening on the given port (Windows)."""
    import subprocess, sys
    if sys.platform != "win32":
        return
    try:
        out = subprocess.check_output(
            f'netstat -ano | findstr ":{port}" | findstr "LISTENING"',
            shell=True, text=True,
        )
        pids = {line.split()[-1] for line in out.strip().splitlines() if line.strip()}
        for pid in pids:
            if pid and pid.isdigit() and int(pid) != os.getpid():
                print(f"[INFO] Killing existing process on port {port} (PID {pid})")
                subprocess.call(f"taskkill /PID {pid} /F", shell=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pass


if __name__ == "__main__":
    import uvicorn

    _kill_port(8000)

    # Disable reload when using XTTS: CUDA model loading deadlocks inside
    # uvicorn's reloader subprocess on Windows.
    use_reload = settings.debug and settings.tts_backend.lower() not in ("xtts", "namaa")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=use_reload,
    )
