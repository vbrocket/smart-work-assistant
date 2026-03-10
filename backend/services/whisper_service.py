"""
Whisper Speech-to-Text Service
Supports Arabic and English with automatic language detection.

Provides both file-based transcription (for HTTP uploads) and buffer-based
transcription (for WebSocket audio chunk streaming).
"""
import os
import logging
import subprocess
import tempfile
import time
import asyncio
from typing import Optional, Tuple
from pathlib import Path

logger = logging.getLogger("whisper_service")

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
        self.device = settings.whisper_device
        self.compute_type = settings.whisper_compute_type
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
        device = self.device
        compute_type = self.compute_type
        device_index = 0

        # Parse "cuda:N" syntax
        if device.startswith("cuda"):
            if ":" in device:
                device_index = int(device.split(":")[1])
                device = "cuda"
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA not available, falling back to CPU for Whisper")
                    device = "cpu"
                    compute_type = "int8"
            except ImportError:
                device = "cpu"
                compute_type = "int8"

        logger.info("Loading Whisper model '%s' using %s on %s:%d (%s)...",
                     self.model_name, WHISPER_BACKEND, device, device_index, compute_type)

        if WHISPER_BACKEND == "faster-whisper":
            self.model = WhisperModel(
                self.model_name,
                device=device,
                device_index=device_index,
                compute_type=compute_type,
            )
        else:
            self.model = whisper.load_model(self.model_name, device=f"{device}:{device_index}" if device == "cuda" else device)

        logger.info("Whisper model loaded successfully on %s", device)
    
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
        
        options = {
            "fp16": self.device == "cuda",
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
    
    @staticmethod
    def _convert_to_wav(src_path: str) -> Optional[str]:
        """Convert audio file to 16kHz mono WAV using ffmpeg.
        Returns the WAV path on success, None on failure."""
        wav_path = src_path.rsplit(".", 1)[0] + ".wav"
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", src_path,
                 "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
                capture_output=True, timeout=15,
            )
            if result.returncode == 0 and os.path.getsize(wav_path) > 100:
                return wav_path
            print(f"[STT] ffmpeg FAILED rc={result.returncode}: {result.stderr[-300:].decode(errors='replace')}", flush=True)
        except Exception as exc:
            print(f"[STT] ffmpeg exception: {exc}", flush=True)
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        return None

    async def transcribe_buffer(
        self,
        audio_buffer: bytes,
        language: Optional[str] = None,
        suffix: str = ".webm",
    ) -> Tuple[str, str, float]:
        """Transcribe raw audio bytes (accumulated WebSocket chunks).

        Writes the buffer to a temp file, converts to WAV via ffmpeg for
        robustness (raw WebM chunks from MediaRecorder may have broken
        container headers), then transcribes.
        """
        if not self._initialized:
            await self.initialize()

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_buffer)
            tmp_path = tmp.name

        hdr = audio_buffer[:4].hex() if len(audio_buffer) >= 4 else "short"
        print(f"[STT] transcribe_buffer | {len(audio_buffer)} bytes | header={hdr} | path={tmp_path}", flush=True)

        # Save a copy for debugging if header looks wrong (valid WebM starts with 1a45dfa3)
        if not hdr.startswith("1a45dfa3"):
            debug_path = f"/tmp/debug_bad_audio_{int(time.time())}.webm"
            try:
                import shutil
                shutil.copy2(tmp_path, debug_path)
                print(f"[STT] SAVED bad audio to {debug_path}", flush=True)
            except Exception:
                pass

        wav_path = None
        try:
            wav_path = await asyncio.get_event_loop().run_in_executor(
                None, self._convert_to_wav, tmp_path,
            )
            transcribe_path = wav_path if wav_path else tmp_path
            print(f"[STT] ffmpeg result: {'OK' if wav_path else 'FAILED'} | using {transcribe_path}", flush=True)
            return await asyncio.get_event_loop().run_in_executor(
                None, self._transcribe_file, transcribe_path, language,
            )
        finally:
            for p in (tmp_path, wav_path):
                if p:
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

    async def transcribe_partial(
        self,
        audio_buffer: bytes,
        language: Optional[str] = None,
        suffix: str = ".webm",
    ) -> str:
        """Quick partial transcription for live preview.

        Returns only the text (no language/confidence) and suppresses errors so
        a failed partial never disrupts the main pipeline.
        """
        try:
            text, _lang, _conf = await self.transcribe_buffer(
                audio_buffer, language=language, suffix=suffix,
            )
            return text
        except Exception as exc:
            logger.debug("Partial transcription failed (non-fatal): %s", exc)
            return ""

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
