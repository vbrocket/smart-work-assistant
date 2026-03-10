"""
Contacts Router - Handles Outlook contacts for attendee picking
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from database import get_db, User, Contact
from services.outlook_service import get_outlook_service
from services.logger import get_email_logger

router = APIRouter()
logger = get_email_logger()


class ContactResponse(BaseModel):
    id: int
    display_name: str
    email: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    
    class Config:
        from_attributes = True


async def get_current_user(db: AsyncSession) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    return result.scalar_one_or_none()


@router.get("/", response_model=List[ContactResponse])
async def get_contacts(
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
):
    """
    Get contacts, optionally filtered by search term.
    Auto-syncs from Outlook on first call if no local contacts exist.
    """
    user = await get_current_user(db)
    if not user:
        return []
    
    # Check if we have any contacts cached; if not, sync
    count_result = await db.execute(
        select(Contact.id).where(Contact.user_id == user.id).limit(1)
    )
    if not count_result.scalar_one_or_none() and user.access_token:
        try:
            outlook_service = get_outlook_service()
            access_token = await outlook_service.refresh_token_if_needed(db, user)
            await outlook_service.sync_contacts_to_db(db, user, access_token)
        except Exception as e:
            logger.error(f"Contact sync failed: {e}")
    
    query = select(Contact).where(Contact.user_id == user.id)
    
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Contact.display_name.ilike(search_term),
                Contact.email.ilike(search_term),
                Contact.company.ilike(search_term)
            )
        )
    
    query = query.order_by(Contact.display_name).limit(limit)
    
    result = await db.execute(query)
    contacts = result.scalars().all()
    
    return [ContactResponse.model_validate(c) for c in contacts]


@router.post("/sync")
async def sync_contacts(db: AsyncSession = Depends(get_db)):
    """Manually trigger contacts sync from Outlook."""
    user = await get_current_user(db)
    if not user or not user.access_token:
        raise HTTPException(status_code=401, detail="Not authenticated with Outlook")
    
    outlook_service = get_outlook_service()
    access_token = await outlook_service.refresh_token_if_needed(db, user)
    contacts = await outlook_service.sync_contacts_to_db(db, user, access_token)
    
    return {"synced": len(contacts)}
