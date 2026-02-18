from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Enum
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
import enum

Base = declarative_base()


class TaskStatus(enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class TaskPriority(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True)
    name = Column(String(255))
    preferred_language = Column(String(10), default="en")  # 'en' or 'ar'
    
    # Microsoft Graph tokens
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    emails = relationship("Email", back_populates="user")
    tasks = relationship("Task", back_populates="user")
    conversations = relationship("Conversation", back_populates="user")


class Email(Base):
    __tablename__ = "emails"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    # Microsoft Graph email ID
    graph_id = Column(String(255), unique=True, index=True)
    
    subject = Column(String(500))
    sender_name = Column(String(255))
    sender_email = Column(String(255))
    body_preview = Column(Text)
    body_content = Column(Text)
    
    received_at = Column(DateTime)
    is_read = Column(Boolean, default=False)
    
    # AI-generated fields
    summary = Column(Text, nullable=True)
    sentiment = Column(String(50), nullable=True)  # positive, negative, neutral
    urgency = Column(String(50), nullable=True)  # low, medium, high
    
    # Draft reply
    draft_reply = Column(Text, nullable=True)
    reply_tone = Column(String(50), nullable=True)  # formal, friendly, brief
    reply_sent = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="emails")
    tasks = relationship("Task", back_populates="source_email")


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    source_email_id = Column(Integer, ForeignKey("emails.id"), nullable=True)
    
    title = Column(String(500))
    description = Column(Text, nullable=True)
    
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING_APPROVAL)
    priority = Column(Enum(TaskPriority), default=TaskPriority.MEDIUM)
    
    due_date = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="tasks")
    source_email = relationship("Email", back_populates="tasks")


class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    # Message details
    role = Column(String(20))  # 'user' or 'assistant'
    content = Column(Text)
    language = Column(String(10))  # 'en' or 'ar'
    
    # Audio reference (if voice message)
    audio_file_path = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="conversations")
