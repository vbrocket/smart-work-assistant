"""
Tasks Router - Handles task management and approval workflow
Integrates with Microsoft To-Do for task synchronization
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import get_db, User, Task, TaskStatus, TaskPriority
from services.outlook_service import get_outlook_service
from services.logger import get_task_logger

router = APIRouter()
logger = get_task_logger()


# ============ Pydantic Models ============

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "medium"  # low, medium, high, urgent
    due_date: Optional[datetime] = None


class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    status: str
    priority: str
    due_date: Optional[datetime]
    source_email_id: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[datetime] = None


class TaskStats(BaseModel):
    total: int
    pending_approval: int
    approved: int
    completed: int
    rejected: int


# ============ Helper Functions ============

async def get_current_user(db: AsyncSession) -> Optional[User]:
    """Get the first authenticated user (POC: single user)."""
    result = await db.execute(
        select(User).where(User.access_token.isnot(None))
    )
    user = result.scalar_one_or_none()
    
    # If no authenticated user, get or create a default user for POC
    if not user:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        
        if not user:
            # Create default user for POC
            user = User(
                email="user@local",
                name="Local User",
                preferred_language="en"
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
    
    return user


def task_to_response(task: Task) -> TaskResponse:
    """Convert Task model to TaskResponse."""
    return TaskResponse(
        id=task.id,
        title=task.title,
        description=task.description,
        status=task.status.value,
        priority=task.priority.value,
        due_date=task.due_date,
        source_email_id=task.source_email_id,
        created_at=task.created_at,
        completed_at=task.completed_at
    )


def parse_priority(priority_str: str) -> TaskPriority:
    """Parse priority string to enum."""
    priority_map = {
        "low": TaskPriority.LOW,
        "medium": TaskPriority.MEDIUM,
        "high": TaskPriority.HIGH,
        "urgent": TaskPriority.URGENT
    }
    return priority_map.get(priority_str.lower(), TaskPriority.MEDIUM)


def parse_status(status_str: str) -> TaskStatus:
    """Parse status string to enum."""
    status_map = {
        "pending_approval": TaskStatus.PENDING_APPROVAL,
        "approved": TaskStatus.APPROVED,
        "rejected": TaskStatus.REJECTED,
        "completed": TaskStatus.COMPLETED
    }
    return status_map.get(status_str.lower(), TaskStatus.PENDING_APPROVAL)


# ============ Endpoints ============

@router.get("/stats", response_model=TaskStats)
async def get_task_stats(db: AsyncSession = Depends(get_db)):
    """Get task statistics."""
    user = await get_current_user(db)
    
    if not user:
        return TaskStats(total=0, pending_approval=0, approved=0, completed=0, rejected=0)
    
    # Count tasks by status
    result = await db.execute(
        select(Task.status, func.count(Task.id))
        .where(Task.user_id == user.id)
        .group_by(Task.status)
    )
    
    counts = {row[0]: row[1] for row in result.all()}
    
    return TaskStats(
        total=sum(counts.values()),
        pending_approval=counts.get(TaskStatus.PENDING_APPROVAL, 0),
        approved=counts.get(TaskStatus.APPROVED, 0),
        completed=counts.get(TaskStatus.COMPLETED, 0),
        rejected=counts.get(TaskStatus.REJECTED, 0)
    )


@router.get("/", response_model=List[TaskResponse])
async def get_tasks(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    sync: bool = Query(True, description="Sync from Microsoft To-Do before fetching"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all tasks, optionally filtered by status and priority.
    Automatically syncs from Microsoft To-Do if user is authenticated.
    """
    user = await get_current_user(db)
    
    if not user:
        return []
    
    # Sync from Microsoft To-Do if user has access token and sync is enabled
    if sync and user.access_token:
        try:
            outlook_service = get_outlook_service()
            await outlook_service.sync_tasks_to_db(
                db=db,
                user=user,
                access_token=user.access_token,
                include_completed=False
            )
            logger.info(f"Synced tasks from Microsoft To-Do for user {user.id}")
        except Exception as e:
            logger.error(f"Failed to sync tasks from Microsoft To-Do: {e}")
            # Continue to return local tasks even if sync fails
    
    query = select(Task).where(Task.user_id == user.id)
    
    if status:
        query = query.where(Task.status == parse_status(status))
    
    if priority:
        query = query.where(Task.priority == parse_priority(priority))
    
    query = query.order_by(Task.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return [task_to_response(t) for t in tasks]


@router.post("/sync", response_model=List[TaskResponse])
async def sync_tasks(
    include_completed: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually sync tasks from Microsoft To-Do.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not user.access_token:
        raise HTTPException(
            status_code=400,
            detail="Not connected to Outlook. Please connect first."
        )
    
    try:
        outlook_service = get_outlook_service()
        synced_tasks = await outlook_service.sync_tasks_to_db(
            db=db,
            user=user,
            access_token=user.access_token,
            include_completed=include_completed
        )
        logger.info(f"Manually synced {len(synced_tasks)} tasks from Microsoft To-Do")
        return [task_to_response(t) for t in synced_tasks]
    except Exception as e:
        logger.error(f"Failed to sync tasks: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to sync tasks: {str(e)}"
        )


@router.get("/pending", response_model=List[TaskResponse])
async def get_pending_tasks(db: AsyncSession = Depends(get_db)):
    """
    Get tasks pending user approval.
    """
    user = await get_current_user(db)
    
    if not user:
        return []
    
    result = await db.execute(
        select(Task)
        .where(Task.user_id == user.id, Task.status == TaskStatus.PENDING_APPROVAL)
        .order_by(Task.created_at.desc())
    )
    
    tasks = result.scalars().all()
    return [task_to_response(t) for t in tasks]


@router.post("/", response_model=TaskResponse)
async def create_task(task: TaskCreate, db: AsyncSession = Depends(get_db)):
    """
    Manually create a new task.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    new_task = Task(
        user_id=user.id,
        title=task.title,
        description=task.description,
        priority=parse_priority(task.priority),
        status=TaskStatus.APPROVED,  # Manually created tasks are auto-approved
        due_date=task.due_date
    )
    
    db.add(new_task)
    await db.commit()
    await db.refresh(new_task)
    
    return task_to_response(new_task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get a specific task by ID.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return task_to_response(task)


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    update: TaskUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update a task's details.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Update fields if provided
    if update.title is not None:
        task.title = update.title
    if update.description is not None:
        task.description = update.description
    if update.priority is not None:
        task.priority = parse_priority(update.priority)
    if update.due_date is not None:
        task.due_date = update.due_date
    
    await db.commit()
    await db.refresh(task)
    
    return task_to_response(task)


@router.post("/{task_id}/approve", response_model=TaskResponse)
async def approve_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Approve a pending task, activating it.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.status != TaskStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Task is not pending approval (current status: {task.status.value})"
        )
    
    task.status = TaskStatus.APPROVED
    await db.commit()
    await db.refresh(task)
    
    return task_to_response(task)


@router.post("/{task_id}/reject", response_model=TaskResponse)
async def reject_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Reject a pending task.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.status != TaskStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Task is not pending approval (current status: {task.status.value})"
        )
    
    task.status = TaskStatus.REJECTED
    await db.commit()
    await db.refresh(task)
    
    return task_to_response(task)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Mark a task as completed.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.status not in [TaskStatus.APPROVED, TaskStatus.PENDING_APPROVAL]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete task with status: {task.status.value}"
        )
    
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(task)
    
    return task_to_response(task)


@router.post("/{task_id}/reopen", response_model=TaskResponse)
async def reopen_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Reopen a completed or rejected task.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.status = TaskStatus.APPROVED
    task.completed_at = None
    await db.commit()
    await db.refresh(task)
    
    return task_to_response(task)


@router.delete("/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Delete a task.
    """
    user = await get_current_user(db)
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    await db.delete(task)
    await db.commit()
    
    return {"status": "deleted", "task_id": task_id}
