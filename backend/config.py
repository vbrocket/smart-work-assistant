from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite+aiosqlite:///./smart_assistant.db"

    # LLM provider: "ollama", "huggingface", "openrouter", or "vllm"
    llm_backend: str = "ollama"
    # Embedding provider: defaults to llm_backend when empty.
    embed_backend: str = ""
    # Reranker provider: defaults to embed_backend when empty.
    reranker_backend: str = ""

    # Ollama LLM (used when llm_backend == "ollama")
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # OpenRouter (used when llm_backend == "openrouter")
    openrouter_api_key: str = ""
    or_llm_model: str = "qwen/qwen3-30b-a3b-instruct-2507"
    or_embed_model: str = "qwen/qwen3-embedding-8b"

    # HuggingFace Inference API
    hf_api_token: str = ""
    hf_llm_model: str = "MiniMaxAI/MiniMax-M2.5"
    hf_embed_model: str = "intfloat/multilingual-e5-large-instruct"

    # vLLM local endpoints (used when *_BACKEND=vllm)
    vllm_llm_url: str = "http://localhost:8001/v1"
    vllm_llm_model: str = "Qwen/Qwen3-32B"
    vllm_embed_url: str = "http://localhost:8002/v1"
    vllm_embed_model: str = "BAAI/bge-m3"
    vllm_rerank_url: str = "http://localhost:8003"
    vllm_rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # vLLM Router (small fast model for intent classification)
    vllm_router_url: str = "http://localhost:8004/v1"
    vllm_router_model: str = "Qwen/Qwen2.5-3B-Instruct"

    @property
    def effective_embed_backend(self) -> str:
        """Resolved embedding backend: EMBED_BACKEND if set, else LLM_BACKEND."""
        return (self.embed_backend or self.llm_backend).lower()

    @property
    def effective_reranker_backend(self) -> str:
        """Resolved reranker backend: RERANKER_BACKEND -> EMBED_BACKEND -> LLM_BACKEND."""
        return (self.reranker_backend or self.embed_backend or self.llm_backend).lower()

    # STT (Speech-to-Text)
    stt_backend: str = "whisper"  # "whisper" (local) or "openrouter" (Gemini cloud)
    whisper_model: str = "medium"  # Options: tiny, base, small, medium, large, large-v3-turbo
    whisper_device: str = "cpu"  # "cpu" or "cuda"
    whisper_compute_type: str = "int8"  # "int8" for CPU, "float16" for GPU
    or_stt_model: str = "google/gemini-2.5-pro"
    
    # Azure AD / Microsoft Graph
    azure_client_id: str = ""
    azure_tenant_id: str = "common"
    
    # TTS Settings
    tts_backend: str = "edge"  # "edge", "xtts", "namaa", or "elevenlabs"
    tts_voice_arabic: str = "ar-SA-HamedNeural"
    tts_voice_english: str = "en-US-GuyNeural"

    # ElevenLabs (used when tts_backend == "elevenlabs")
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "t9akNmCDhz230CEXOYmn"
    elevenlabs_model: str = "eleven_multilingual_v2"

    # XTTS-v2 (used when tts_backend == "xtts")
    xtts_speaker_wav_ar: str = "voices/male_ar.wav"
    xtts_speaker_wav_en: str = "voices/male_en.wav"
    xtts_device: str = "cuda"

    # RAG / Policy Documents
    ollama_embed_model: str = "bge-m3"
    documents_dir: str = "documents"

    # Qdrant vector store
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "hr_policy_rag"
    qdrant_fallback: bool = True

    # BM25 lexical index
    bm25_index_path: str = "data/bm25_index.pkl"

    # Reranker
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # Retrieval tuning
    rag_fusion_method: str = "rrf"
    rag_dense_top_k: int = 20
    rag_bm25_top_k: int = 20
    rag_rerank_top_k: int = 12
    rag_final_top_k: int = 6

    # Chunking
    rag_chunk_max_tokens: int = 1100
    rag_chunk_overlap_tokens: int = 100

    # Embedding
    embedding_batch_size: int = 8

    # Legacy (kept for migration adapter)
    chroma_persist_dir: str = "data/chroma_db"
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5
    
    # App Settings
    app_name: str = "Smart Work Assistant"
    debug: bool = True
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
