"""
Email Router - Handles Outlook email operations
"""
import asyncio

from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, AsyncSessionLocal, User, Email, Task, TaskStatus, TaskPriority
from services.outlook_service import get_outlook_service, OutlookService
from services.llm_service import get_llm_service, LLMService
from services.logger import get_email_logger

router = APIRouter()
logger = get_email_logger()

_device_code_bg_task: Optional[asyncio.Task] = None


# ============ Pydantic Models ============

class EmailSummary(BaseModel):
    id: int
    graph_id: str
    subject: str
    sender_name: str
    sender_email: str
    body_preview: str
    received_at: datetime
    is_read: bool
    summary: Optional[str] = None
    sentiment: Optional[str] = None
    urgency: Optional[str] = None
    
    class Config:
        from_attributes = True


class EmailDetail(EmailSummary):
    body_content: str
    draft_reply: Optional[str] = None
    reply_tone: Optional[str] = None
    extracted_tasks: List[dict] = []
    
    class Config:
        from_attributes = True


class DraftReplyRequest(BaseModel):
    tone: str = "formal"  # formal, friendly, brief
    language: Optional[str] = "en"
    additional_context: Optional[str] = None


class DraftReplyResponse(BaseModel):
    email_id: int
    draft_reply: str
    tone: str


class DeviceCodeResponse(BaseModel):
    user_code: str
    verification_uri: str
    expires_in: int
    message: str


class AuthStatusResponse(BaseModel):
    authenticated: bool
    email: Optional[str] = None
    name: Optional[str] = None
    was_connected: bool = False


class SummarizeResponse(BaseModel):
    email_id: int
    summary: str
    key_points: List[str]
    sentiment: str
    urgency: str


class ExtractTasksResponse(BaseModel):
    email_id: int
    tasks_created: int
    tasks: List[dict]


# ============ Helper Functions ============

async def get_current_user(db: AsyncSession) -> Optional[User]:
    """Get the first authenticated user (POC: single user)."""
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    return result.scalar_one_or_none()


# ============ Authentication Endpoints ============

async def _bg_acquire_token():
    """Background coroutine that waits for the user to complete device-code
    auth on Microsoft's side, then saves the resulting tokens to the DB."""
    outlook_service = get_outlook_service()
    logger.info("Background device-code polling started (timeout=900s)")
    try:
        async with AsyncSessionLocal() as db:
            success, user = await outlook_service.complete_device_code_flow(
                db=db, timeout=900,
            )
            if success and user:
                logger.info("Background device-code auth succeeded for %s", user.email)
            else:
                logger.warning("Background device-code auth failed or timed out (success=%s)", success)
    except Exception as exc:
        logger.error("Background device-code auth error: %s", exc, exc_info=True)


@router.get("/auth/device-code", response_model=DeviceCodeResponse)
async def start_device_code_flow():
    """Start Microsoft OAuth device code flow.

    Returns a code for the user to enter at microsoft.com/devicelogin.
    Automatically begins polling Microsoft for the token in the background
    so no separate ``/auth/complete`` call is needed.
    """
    global _device_code_bg_task

    outlook_service = get_outlook_service()

    try:
        flow = await outlook_service.start_device_code_flow()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start auth flow: {str(e)}")

    if _device_code_bg_task and not _device_code_bg_task.done():
        _device_code_bg_task.cancel()
    _device_code_bg_task = asyncio.create_task(_bg_acquire_token())

    return DeviceCodeResponse(**flow)


@router.post("/auth/complete")
async def complete_device_code_flow(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Legacy endpoint — kept for backwards compatibility.

    The background polling is now started automatically by ``/auth/device-code``.
    If a background task is already running, this just returns a status message.
    """
    if _device_code_bg_task and not _device_code_bg_task.done():
        return {"status": "polling", "message": "Already polling for auth completion"}

    outlook_service = get_outlook_service()
    try:
        success, user = await outlook_service.complete_device_code_flow(
            db=db, timeout=300,
        )
        if success and user:
            return {"status": "authenticated", "email": user.email, "name": user.name}
        else:
            raise HTTPException(status_code=401, detail="Authentication failed or timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/status", response_model=AuthStatusResponse)
async def get_auth_status(db: AsyncSession = Depends(get_db)):
    """Check if user is authenticated with Microsoft.

    If the stored token has expired, we attempt a silent refresh.
    On failure the stale tokens are cleared so the UI shows
    "not connected" and offers the login button.
    """
    user = await get_current_user(db)

    if user and user.access_token:
        outlook_service = get_outlook_service()
        try:
            await outlook_service.refresh_token_if_needed(db, user)
        except Exception:
            user.access_token = None
            user.refresh_token = None
            user.token_expires_at = None
            await db.commit()
            logger.warning("Stale Outlook token cleared for %s — re-auth required", user.email)
            return AuthStatusResponse(
                authenticated=False, email=None, name=None, was_connected=True,
            )

        return AuthStatusResponse(
            authenticated=True,
            email=user.email,
            name=user.name,
            was_connected=True,
        )

    has_user = user is not None
    return AuthStatusResponse(
        authenticated=False,
        email=None,
        name=None,
        was_connected=has_user,
    )


@router.delete("/auth/logout")
async def disconnect_outlook(db: AsyncSession = Depends(get_db)):
    """
    Disconnect from Microsoft/Outlook by clearing the user's access token.
    """
    user = await get_current_user(db)
    
    if not user:
        return {"status": "not_connected", "message": "No authenticated user found"}
    
    # Clear tokens
    user.access_token = None
    user.refresh_token = None
    user.token_expires_at = None
    
    await db.commit()
    logger.info(f"User {user.email} disconnected from Outlook")
    
    return {
        "status": "disconnected",
        "message": "Successfully disconnected from Outlook"
    }


# ============ Email Endpoints ============

@router.get("/", response_model=List[EmailSummary])
async def get_emails(
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    sync: bool = Query(True),  # Sync from Outlook first
    db: AsyncSession = Depends(get_db)
):
    """
    Fetch emails. If sync=True, fetches from Outlook first.
    """
    user = await get_current_user(db)
    
    if not user:
        return []
    
    outlook_service = get_outlook_service()
    
    try:
        # Refresh token if needed and sync
        if sync and user.access_token:
            logger.info(f"Syncing emails for user {user.email}")
            access_token = await outlook_service.refresh_token_if_needed(db, user)
            logger.debug(f"Token refreshed/validated, fetching emails...")
            await outlook_service.sync_emails_to_db(
                db=db,
                user=user,
                access_token=access_token,
                limit=limit + skip
            )
            logger.info(f"Email sync completed")
    except Exception as e:
        # Log error but continue with cached emails
        logger.error(f"Email sync failed | error_type={type(e).__name__} | error={str(e)}", exc_info=True)
    
    # Query from database
    query = select(Email).where(Email.user_id == user.id)
    
    if unread_only:
        query = query.where(Email.is_read == False)
    
    query = query.order_by(Email.received_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    emails = result.scalars().all()
    
    return [EmailSummary.model_validate(e) for e in emails]


@router.get("/{email_id}", response_model=EmailDetail)
async def get_email(email_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get detailed email information including AI-generated summary.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Email).where(Email.id == email_id, Email.user_id == user.id)
    )
    email = result.scalar_one_or_none()
    
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    # Get associated tasks
    task_result = await db.execute(
        select(Task).where(Task.source_email_id == email_id)
    )
    tasks = task_result.scalars().all()
    
    email_data = EmailDetail.model_validate(email)
    email_data.extracted_tasks = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status.value,
            "priority": t.priority.value
        }
        for t in tasks
    ]
    
    return email_data


@router.post("/{email_id}/summarize", response_model=SummarizeResponse)
async def summarize_email(email_id: int, db: AsyncSession = Depends(get_db)):
    """
    Generate AI summary for an email.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Email).where(Email.id == email_id, Email.user_id == user.id)
    )
    email = result.scalar_one_or_none()
    
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    llm_service = get_llm_service()
    
    # Detect language from email content
    language = llm_service.detect_language(email.body_content or email.body_preview)
    
    try:
        summary_data = await llm_service.summarize_email(
            sender=f"{email.sender_name} <{email.sender_email}>",
            subject=email.subject,
            body=email.body_content or email.body_preview,
            language=language
        )
        
        # Update email with summary
        email.summary = summary_data.get("summary", "")
        email.sentiment = summary_data.get("sentiment", "neutral")
        email.urgency = summary_data.get("urgency", "medium")
        
        await db.commit()
        
        return SummarizeResponse(
            email_id=email_id,
            summary=email.summary,
            key_points=summary_data.get("key_points", []),
            sentiment=email.sentiment,
            urgency=email.urgency
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarization failed: {str(e)}")


@router.post("/{email_id}/extract-tasks", response_model=ExtractTasksResponse)
async def extract_tasks_from_email(email_id: int, db: AsyncSession = Depends(get_db)):
    """
    Extract actionable tasks from an email using AI.
    Tasks are created with pending_approval status.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Email).where(Email.id == email_id, Email.user_id == user.id)
    )
    email = result.scalar_one_or_none()
    
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    llm_service = get_llm_service()
    language = llm_service.detect_language(email.body_content or email.body_preview)
    
    try:
        tasks_data = await llm_service.extract_tasks(
            sender=f"{email.sender_name} <{email.sender_email}>",
            subject=email.subject,
            body=email.body_content or email.body_preview,
            language=language
        )
        
        created_tasks = []
        
        for task_data in tasks_data:
            # Map priority string to enum
            priority_map = {
                "low": TaskPriority.LOW,
                "medium": TaskPriority.MEDIUM,
                "high": TaskPriority.HIGH,
                "urgent": TaskPriority.URGENT,
                "منخفض": TaskPriority.LOW,
                "متوسط": TaskPriority.MEDIUM,
                "عالي": TaskPriority.HIGH,
                "عاجل": TaskPriority.URGENT
            }
            priority = priority_map.get(
                task_data.get("priority", "medium").lower(),
                TaskPriority.MEDIUM
            )
            
            # Parse due date if provided
            due_date = None
            if task_data.get("due_date"):
                try:
                    due_date = datetime.fromisoformat(task_data["due_date"])
                except (ValueError, TypeError):
                    pass
            
            task = Task(
                user_id=user.id,
                source_email_id=email_id,
                title=task_data.get("title", "Untitled Task"),
                description=task_data.get("description"),
                status=TaskStatus.PENDING_APPROVAL,
                priority=priority,
                due_date=due_date
            )
            db.add(task)
            created_tasks.append(task)
        
        await db.commit()
        
        # Refresh to get IDs
        for task in created_tasks:
            await db.refresh(task)
        
        return ExtractTasksResponse(
            email_id=email_id,
            tasks_created=len(created_tasks),
            tasks=[
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "priority": t.priority.value,
                    "status": t.status.value
                }
                for t in created_tasks
            ]
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Task extraction failed: {str(e)}")


@router.post("/{email_id}/draft-reply", response_model=DraftReplyResponse)
async def draft_email_reply(
    email_id: int,
    request: DraftReplyRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Generate an AI-crafted reply draft with specified tone.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Email).where(Email.id == email_id, Email.user_id == user.id)
    )
    email = result.scalar_one_or_none()
    
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    llm_service = get_llm_service()
    
    try:
        draft = await llm_service.draft_reply(
            sender=f"{email.sender_name} <{email.sender_email}>",
            subject=email.subject,
            body=email.body_content or email.body_preview,
            tone=request.tone,
            additional_context=request.additional_context,
            language=request.language or "en"
        )
        
        # Save draft to database
        email.draft_reply = draft
        email.reply_tone = request.tone
        
        await db.commit()
        
        return DraftReplyResponse(
            email_id=email_id,
            draft_reply=draft,
            tone=request.tone
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {str(e)}")


@router.post("/{email_id}/send")
async def send_email_reply(email_id: int, db: AsyncSession = Depends(get_db)):
    """
    Send the approved draft reply.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Email).where(Email.id == email_id, Email.user_id == user.id)
    )
    email = result.scalar_one_or_none()
    
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    if not email.draft_reply:
        raise HTTPException(status_code=400, detail="No draft reply to send")
    
    outlook_service = get_outlook_service()
    
    try:
        access_token = await outlook_service.refresh_token_if_needed(db, user)
        
        success = await outlook_service.send_email(
            access_token=access_token,
            to=[email.sender_email],
            subject=f"Re: {email.subject}",
            body=email.draft_reply,
            reply_to_id=email.graph_id
        )
        
        if success:
            email.reply_sent = True
            await db.commit()
            return {"status": "sent", "email_id": email_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to send email")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Send failed: {str(e)}")
