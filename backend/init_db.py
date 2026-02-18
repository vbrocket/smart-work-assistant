"""
Database initialization and seeding script
"""
import asyncio
from datetime import datetime, timedelta
from database import init_db, async_session, User, Task, TaskStatus, TaskPriority

async def seed_database():
    """Seed the database with initial data."""
    async with async_session() as db:
        # Check if we already have data
        from sqlalchemy import select
        result = await db.execute(select(User).limit(1))
        existing_user = result.scalar_one_or_none()
        
        if existing_user:
            print("[INFO] Database already has data, skipping seed")
            return
        
        print("[INFO] Seeding database with sample data...")
        
        # Create default user
        user = User(
            email="user@local",
            name="Local User",
            preferred_language="en"
        )
        db.add(user)
        await db.flush()
        
        # Create sample tasks
        sample_tasks = [
            Task(
                user_id=user.id,
                title="Review project proposal",
                description="Review and provide feedback on the Q1 project proposal",
                status=TaskStatus.PENDING_APPROVAL,
                priority=TaskPriority.HIGH,
                due_date=datetime.utcnow() + timedelta(days=2)
            ),
            Task(
                user_id=user.id,
                title="Schedule team meeting",
                description="Set up weekly sync meeting with the development team",
                status=TaskStatus.APPROVED,
                priority=TaskPriority.MEDIUM,
                due_date=datetime.utcnow() + timedelta(days=1)
            ),
            Task(
                user_id=user.id,
                title="Update documentation",
                description="Update the API documentation with new endpoints",
                status=TaskStatus.APPROVED,
                priority=TaskPriority.LOW,
                due_date=datetime.utcnow() + timedelta(days=7)
            ),
            Task(
                user_id=user.id,
                title="Prepare presentation",
                description="Create slides for the quarterly review",
                status=TaskStatus.PENDING_APPROVAL,
                priority=TaskPriority.URGENT,
                due_date=datetime.utcnow() + timedelta(days=3)
            ),
        ]
        
        for task in sample_tasks:
            db.add(task)
        
        await db.commit()
        print(f"[INFO] Created {len(sample_tasks)} sample tasks")

async def main():
    """Initialize and seed the database."""
    print("[INFO] Initializing database...")
    await init_db()
    print("[INFO] Database tables created")
    
    await seed_database()
    print("[INFO] Database initialization complete")

if __name__ == "__main__":
    asyncio.run(main())
