"""
Calendar Router - Handles Outlook calendar operations
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db, User, CalendarEvent
from services.outlook_service import get_outlook_service
from services.logger import get_email_logger

router = APIRouter()
logger = get_email_logger()


# ============ Pydantic Models ============

class EventResponse(BaseModel):
    id: int
    graph_id: str
    subject: str
    organizer_name: Optional[str] = None
    organizer_email: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    is_all_day: bool = False
    location: Optional[str] = None
    is_online: bool = False
    online_meeting_url: Optional[str] = None
    body_preview: Optional[str] = None
    attendees: Optional[str] = None
    status: Optional[str] = None
    
    class Config:
        from_attributes = True


class CreateEventRequest(BaseModel):
    subject: str
    start: str  # ISO datetime string
    end: str
    location: Optional[str] = None
    body: Optional[str] = None
    is_online: bool = True
    attendees: Optional[List[dict]] = None  # [{"email": "...", "name": "..."}]


class CreateEventResponse(BaseModel):
    id: Optional[str] = None
    subject: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    online_meeting_url: Optional[str] = None
    attendees_count: int = 0


# ============ Helpers ============

async def get_current_user(db: AsyncSession) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    return result.scalar_one_or_none()


# ============ Endpoints ============

@router.get("/", response_model=List[EventResponse])
async def get_calendar_events(
    target_date: Optional[str] = Query(None, alias="date", description="YYYY-MM-DD, defaults to today"),
    sync: bool = Query(True),
    db: AsyncSession = Depends(get_db)
):
    """Fetch calendar events for a given date (default: today)."""
    user = await get_current_user(db)
    if not user:
        return []
    
    try:
        d = date.fromisoformat(target_date) if target_date else date.today()
    except ValueError:
        d = date.today()
    
    outlook_service = get_outlook_service()
    
    if sync and user.access_token:
        try:
            access_token = await outlook_service.refresh_token_if_needed(db, user)
            await outlook_service.sync_events_to_db(db, user, access_token, target_date=d, days_ahead=14)
        except Exception as e:
            logger.error(f"Calendar sync failed: {e}")
    
    start_of_day = datetime.combine(d, datetime.min.time())
    end_of_day = datetime.combine(d, datetime.max.time())
    
    result = await db.execute(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user.id,
            CalendarEvent.start_time >= start_of_day,
            CalendarEvent.start_time <= end_of_day
        )
        .order_by(CalendarEvent.start_time)
    )
    events = result.scalars().all()
    
    return [EventResponse.model_validate(e) for e in events]


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single calendar event by ID."""
    user = await get_current_user(db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.id == event_id,
            CalendarEvent.user_id == user.id
        )
    )
    event = result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    return EventResponse.model_validate(event)


@router.post("/", response_model=CreateEventResponse)
async def create_event(
    request: CreateEventRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a new calendar event with optional Teams meeting link."""
    user = await get_current_user(db)
    if not user or not user.access_token:
        raise HTTPException(status_code=401, detail="Not authenticated with Outlook")
    
    outlook_service = get_outlook_service()
    
    try:
        access_token = await outlook_service.refresh_token_if_needed(db, user)
        
        created = await outlook_service.create_calendar_event(
            access_token=access_token,
            subject=request.subject,
            start=request.start,
            end=request.end,
            attendees=request.attendees,
            location=request.location,
            body=request.body,
            is_online=request.is_online
        )
        
        if not created:
            raise HTTPException(status_code=500, detail="Failed to create event")
        
        online_meeting = created.get("onlineMeeting") or {}
        
        return CreateEventResponse(
            id=created.get("id"),
            subject=created.get("subject", request.subject),
            start_time=created.get("start", {}).get("dateTime"),
            end_time=created.get("end", {}).get("dateTime"),
            online_meeting_url=online_meeting.get("joinUrl", ""),
            attendees_count=len(request.attendees) if request.attendees else 0
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create event failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")


@router.post("/sync")
async def sync_calendar(
    target_date: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db)
):
    """Manually trigger calendar sync."""
    user = await get_current_user(db)
    if not user or not user.access_token:
        raise HTTPException(status_code=401, detail="Not authenticated with Outlook")
    
    try:
        d = date.fromisoformat(target_date) if target_date else date.today()
    except ValueError:
        d = date.today()
    
    outlook_service = get_outlook_service()
    access_token = await outlook_service.refresh_token_if_needed(db, user)
    events = await outlook_service.sync_events_to_db(db, user, access_token, target_date=d)
    
    return {"synced": len(events), "date": d.isoformat()}
