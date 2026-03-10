"""
Logging Service - Centralized logging for the Smart Work Assistant
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Create logs directory
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Log file path
LOG_FILE = LOG_DIR / f"assistant_{datetime.now().strftime('%Y%m%d')}.log"

# Get debug mode from environment (avoid circular import)
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"


def setup_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Set up a logger with both console and file handlers.
    
    Args:
        name: Logger name (usually __name__)
        level: Logging level
    
    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    
    # Console handler - colorful output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO if not DEBUG_MODE else logging.DEBUG)
    console_format = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    
    # File handler - detailed output
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger


# Pre-configured loggers for different modules
def get_chat_logger() -> logging.Logger:
    """Get logger for chat/voice operations."""
    return setup_logger("chat")


def get_llm_logger() -> logging.Logger:
    """Get logger for LLM service."""
    return setup_logger("llm")


def get_email_logger() -> logging.Logger:
    """Get logger for email operations."""
    return setup_logger("email")


def get_task_logger() -> logging.Logger:
    """Get logger for task operations."""
    return setup_logger("task")


def get_rag_logger() -> logging.Logger:
    """Get logger for RAG pipeline operations."""
    return setup_logger("rag")


# Convenience function to log with context
def log_request(logger: logging.Logger, endpoint: str, data: dict):
    """Log an incoming request."""
    logger.info(f"REQUEST {endpoint} | data={data}")


def log_response(logger: logging.Logger, endpoint: str, status: str, data: Optional[dict] = None):
    """Log an outgoing response."""
    logger.info(f"RESPONSE {endpoint} | status={status} | data={data}")


def log_error(logger: logging.Logger, endpoint: str, error: Exception, context: Optional[dict] = None):
    """Log an error with full details."""
    logger.error(f"ERROR {endpoint} | type={type(error).__name__} | message={str(error)} | context={context}")
    logger.debug(f"ERROR TRACEBACK:", exc_info=True)
