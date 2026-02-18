"""
Whisper Speech-to-Text Service
Supports Arabic and English with automatic language detection
"""
import os
import tempfile
import asyncio
from typing import Optional, Tuple
from pathlib import Path

# Try faster-whisper first (better performance), fallback to openai-whisper
try:
    from faster_whisper import WhisperModel
    WHISPER_BACKEND = "faster-whisper"
except ImportError:
    try:
        import whisper
        WHISPER_BACKEND = "openai-whisper"
    except ImportError:
        WHISPER_BACKEND = None

from config import get_settings

settings = get_settings()


class WhisperService:
    """Service for speech-to-text transcription using Whisper."""
    
    def __init__(self):
        self.model = None
        self.model_name = settings.whisper_model
        self._initialized = False
    
    async def initialize(self):
        """Initialize the Whisper model."""
        if self._initialized:
            return
        
        if WHISPER_BACKEND is None:
            raise RuntimeError(
                "No Whisper backend available. Install 'faster-whisper' or 'openai-whisper'"
            )
        
        # Load model in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        self._initialized = True
    
    def _load_model(self):
        """Load the Whisper model (blocking operation)."""
        print(f"Loading Whisper model '{self.model_name}' using {WHISPER_BACKEND}...")
        
        if WHISPER_BACKEND == "faster-whisper":
            # faster-whisper uses CTranslate2 for better performance
            self.model = WhisperModel(
                self.model_name,
                device="cpu",  # Use "cuda" if GPU available
                compute_type="int8"  # Use "float16" for GPU
            )
        else:
            # OpenAI whisper
            self.model = whisper.load_model(self.model_name)
        
        print(f"Whisper model loaded successfully")
    
    async def transcribe(
        self,
        audio_data: bytes,
        language: Optional[str] = None
    ) -> Tuple[str, str, float]:
        """
        Transcribe audio data to text.
        
        Args:
            audio_data: Raw audio bytes
            language: Optional language hint ('en' or 'ar'). If None, auto-detect.
        
        Returns:
            Tuple of (transcribed_text, detected_language, confidence)
        """
        if not self._initialized:
            await self.initialize()
        
        # Write audio to temporary file (Whisper requires file input)
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp_file:
            temp_file.write(audio_data)
            temp_path = temp_file.name
        
        try:
            # Run transcription in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._transcribe_file,
                temp_path,
                language
            )
            return result
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except Exception:
                pass
    
    def _transcribe_file(
        self,
        file_path: str,
        language: Optional[str] = None
    ) -> Tuple[str, str, float]:
        """
        Transcribe audio file (blocking operation).
        
        Returns:
            Tuple of (text, language, confidence)
        """
        if WHISPER_BACKEND == "faster-whisper":
            return self._transcribe_faster_whisper(file_path, language)
        else:
            return self._transcribe_openai_whisper(file_path, language)
    
    def _transcribe_faster_whisper(
        self,
        file_path: str,
        language: Optional[str] = None
    ) -> Tuple[str, str, float]:
        """Transcribe using faster-whisper."""
        # Map short language codes to full codes
        lang_map = {'en': 'en', 'ar': 'ar'}
        lang = lang_map.get(language) if language else None
        
        segments, info = self.model.transcribe(
            file_path,
            language=lang,
            beam_size=5,
            vad_filter=True,  # Filter out non-speech
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200
            )
        )
        
        # Collect all segments
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())
        
        full_text = " ".join(text_parts)
        detected_lang = info.language
        confidence = info.language_probability
        
        return full_text, detected_lang, confidence
    
    def _transcribe_openai_whisper(
        self,
        file_path: str,
        language: Optional[str] = None
    ) -> Tuple[str, str, float]:
        """Transcribe using openai-whisper."""
        import whisper
        
        # Transcribe with options
        options = {
            "fp16": False,  # Disable for CPU
            "language": language if language else None,
        }
        
        result = self.model.transcribe(file_path, **options)
        
        text = result["text"].strip()
        detected_lang = result.get("language", language or "en")
        
        # OpenAI whisper doesn't provide confidence, estimate from segments
        segments = result.get("segments", [])
        if segments:
            avg_prob = sum(s.get("avg_logprob", 0) for s in segments) / len(segments)
            # Convert log probability to confidence (rough approximation)
            confidence = min(1.0, max(0.0, 1.0 + avg_prob / 5))
        else:
            confidence = 0.5
        
        return text, detected_lang, confidence
    
    def detect_language(self, text: str) -> str:
        """
        Simple language detection based on character analysis.
        Returns 'ar' for Arabic, 'en' for English.
        """
        # Check for Arabic characters
        arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
        total_alpha = sum(1 for c in text if c.isalpha())
        
        if total_alpha == 0:
            return 'en'
        
        arabic_ratio = arabic_chars / total_alpha
        return 'ar' if arabic_ratio > 0.3 else 'en'


# Singleton instance
_whisper_service: Optional[WhisperService] = None


def get_whisper_service() -> WhisperService:
    """Get or create the Whisper service singleton."""
    global _whisper_service
    if _whisper_service is None:
        _whisper_service = WhisperService()
    return _whisper_service
