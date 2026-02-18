"""
Text-to-Speech Service using Edge TTS
Supports Arabic and English with high-quality neural voices
"""
import asyncio
import tempfile
import os
from typing import Optional, AsyncGenerator
from pathlib import Path

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

from config import get_settings

settings = get_settings()


class TTSService:
    """Service for text-to-speech using Microsoft Edge TTS."""
    
    # Available voices for each language
    VOICES = {
        'ar': {
            'male': 'ar-SA-HamedNeural',
            'female': 'ar-SA-ZariyahNeural'
        },
        'en': {
            'male': 'en-US-GuyNeural',
            'female': 'en-US-JennyNeural'
        }
    }
    
    def __init__(self):
        self.default_voice_ar = settings.tts_voice_arabic
        self.default_voice_en = settings.tts_voice_english
    
    def get_voice(self, language: str, gender: str = 'male') -> str:
        """Get the appropriate voice for language and gender."""
        lang = 'ar' if language.startswith('ar') else 'en'
        voices = self.VOICES.get(lang, self.VOICES['en'])
        return voices.get(gender, voices['male'])
    
    async def synthesize(
        self,
        text: str,
        language: str = 'en',
        gender: str = 'male',
        rate: str = '+0%',
        pitch: str = '+0Hz'
    ) -> bytes:
        """
        Synthesize speech from text.
        
        Args:
            text: Text to synthesize
            language: Language code ('en' or 'ar')
            gender: Voice gender ('male' or 'female')
            rate: Speech rate adjustment (e.g., '+10%', '-20%')
            pitch: Pitch adjustment (e.g., '+5Hz', '-10Hz')
        
        Returns:
            Audio bytes (MP3 format)
        """
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts not installed. Install with: pip install edge-tts")
        
        voice = self.get_voice(language, gender)
        
        # Create communicate instance
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch
        )
        
        # Collect audio chunks
        audio_data = bytearray()
        
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        
        return bytes(audio_data)
    
    async def synthesize_to_file(
        self,
        text: str,
        output_path: str,
        language: str = 'en',
        gender: str = 'male'
    ) -> str:
        """
        Synthesize speech and save to file.
        
        Args:
            text: Text to synthesize
            output_path: Path to save the audio file
            language: Language code
            gender: Voice gender
        
        Returns:
            Path to the saved audio file
        """
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts not installed")
        
        voice = self.get_voice(language, gender)
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(output_path)
        return output_path
    
    async def stream_audio(
        self,
        text: str,
        language: str = 'en',
        gender: str = 'male'
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream synthesized audio chunks.
        
        Yields:
            Audio data chunks
        """
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts not installed")
        
        voice = self.get_voice(language, gender)
        communicate = edge_tts.Communicate(text=text, voice=voice)
        
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
    
    @staticmethod
    async def list_voices(language: Optional[str] = None) -> list:
        """
        List available TTS voices.
        
        Args:
            language: Optional language filter (e.g., 'ar', 'en')
        
        Returns:
            List of available voices
        """
        if not EDGE_TTS_AVAILABLE:
            return []
        
        voices = await edge_tts.list_voices()
        
        if language:
            # Filter by language prefix
            lang_prefix = f"{language}-" if len(language) == 2 else language
            voices = [v for v in voices if v['Locale'].startswith(lang_prefix)]
        
        return voices


# Singleton instance
_tts_service: Optional[TTSService] = None


def get_tts_service() -> TTSService:
    """Get or create the TTS service singleton."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
