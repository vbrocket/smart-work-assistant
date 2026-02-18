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
from database import init_db, get_db, User, Email, Task, TaskStatus
from routers import voice, email, tasks
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
    
    # Generate natural language summary
    try:
        llm_service = get_llm_service()
        
        tasks_for_llm = [
            {"title": t.title, "priority": t.priority.value, "status": t.status.value}
            for t in active_tasks[:10]
        ]
        
        emails_for_llm = [
            {"sender_name": e.sender_name, "subject": e.subject}
            for e in action_emails[:5]
        ]
        
        summary_text = await llm_service.generate_daily_summary(
            tasks=tasks_for_llm,
            emails=emails_for_llm,
            language=language
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
    return {
        "app_name": settings.app_name,
        "ollama_host": settings.ollama_host,
        "ollama_model": settings.ollama_model,
        "whisper_model": settings.whisper_model,
        "tts_voice_arabic": settings.tts_voice_arabic,
        "tts_voice_english": settings.tts_voice_english,
        "azure_configured": bool(settings.azure_client_id)
    }


# Serve frontend static files (in production)
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )
