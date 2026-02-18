from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite+aiosqlite:///./smart_assistant.db"
    
    # Ollama LLM
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    
    # Whisper
    whisper_model: str = "base"  # Options: tiny, base, small, medium, large
    
    # Azure AD / Microsoft Graph
    azure_client_id: str = ""
    azure_tenant_id: str = "common"
    
    # TTS Settings
    tts_voice_arabic: str = "ar-SA-HamedNeural"
    tts_voice_english: str = "en-US-GuyNeural"
    
    # App Settings
    app_name: str = "Smart Work Assistant"
    debug: bool = True
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
