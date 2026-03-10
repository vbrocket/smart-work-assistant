"""
Text-to-Speech Service.

Supports multiple backends controlled by TTS_BACKEND env variable:
  - "edge"       (default) — Microsoft Edge TTS (cloud, free, Arabic + English)
  - "elevenlabs" — ElevenLabs (cloud, high-quality multilingual)
  - "xtts"       — Coqui XTTS-v2 (local, GPU, voice cloning)
  - "namaa"      — NAMAA-Saudi-TTS (local 0.5B, Saudi Arabic dialect)
"""
import asyncio
import io
import logging
import os
import re
import tempfile
from typing import Optional, AsyncGenerator
from pathlib import Path

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

from config import get_settings

settings = get_settings()
logger = logging.getLogger("tts_service")


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
    
    _ARABIC_TO_WESTERN = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')

    _DIGIT_WORDS_AR = {
        '0': 'صفر', '1': 'واحد', '2': 'اثنان', '3': 'ثلاثة',
        '4': 'أربعة', '5': 'خمسة', '6': 'ستة', '7': 'سبعة',
        '8': 'ثمانية', '9': 'تسعة',
    }

    _NUMBER_WORDS_AR = {
        0: 'صفر', 1: 'واحد', 2: 'اثنان', 3: 'ثلاثة', 4: 'أربعة',
        5: 'خمسة', 6: 'ستة', 7: 'سبعة', 8: 'ثمانية', 9: 'تسعة',
        10: 'عشرة', 11: 'أحد عشر', 12: 'اثنا عشر', 13: 'ثلاثة عشر',
        14: 'أربعة عشر', 15: 'خمسة عشر', 16: 'ستة عشر', 17: 'سبعة عشر',
        18: 'ثمانية عشر', 19: 'تسعة عشر', 20: 'عشرون', 30: 'ثلاثون',
        40: 'أربعون', 50: 'خمسون', 60: 'ستون', 70: 'سبعون',
        80: 'ثمانون', 90: 'تسعون', 100: 'مائة', 200: 'مائتان',
        1000: 'ألف', 2000: 'ألفان',
    }

    @staticmethod
    def _int_to_arabic(n: int) -> str:
        """Convert an integer (0–99999) to spoken Arabic words."""
        w = TTSService._NUMBER_WORDS_AR
        if n in w:
            return w[n]
        if n < 0:
            return 'سالب ' + TTSService._int_to_arabic(-n)
        if n < 100:
            ones, tens = n % 10, (n // 10) * 10
            return f'{w[ones]} و{w[tens]}'
        if n < 1000:
            hundreds = n // 100
            remainder = n % 100
            h = w.get(hundreds * 100, w.get(hundreds, str(hundreds)) + ' مائة')
            if remainder == 0:
                return h
            return f'{h} و{TTSService._int_to_arabic(remainder)}'
        if n < 100000:
            thousands = n // 1000
            remainder = n % 1000
            th = w.get(thousands * 1000, TTSService._int_to_arabic(thousands) + ' آلاف')
            if remainder == 0:
                return th
            return f'{th} و{TTSService._int_to_arabic(remainder)}'
        return str(n)

    @staticmethod
    def _number_to_spoken_arabic(match: re.Match) -> str:
        """Convert dotted numbers (1.1.4) or plain numbers to spoken Arabic."""
        token = match.group(0)
        if '.' in token:
            parts = token.split('.')
            if all(p.isdigit() for p in parts):
                if len(parts) >= 3 or all(len(p) <= 2 for p in parts):
                    spoken = ' نقطة '.join(
                        TTSService._int_to_arabic(int(p)) for p in parts
                    )
                    return spoken
                try:
                    val = float(token)
                    integer_part = int(val)
                    decimal_str = token.split('.', 1)[1]
                    decimal_spoken = ' '.join(
                        TTSService._DIGIT_WORDS_AR[d] for d in decimal_str
                    )
                    return f'{TTSService._int_to_arabic(integer_part)} فاصلة {decimal_spoken}'
                except (ValueError, KeyError):
                    pass
        if token.isdigit():
            try:
                return TTSService._int_to_arabic(int(token))
            except Exception:
                pass
        return token

    @staticmethod
    def _numerals_to_arabic_words(text: str) -> str:
        """Replace all numeric patterns with spoken Arabic equivalents."""
        text = re.sub(r'(\d+%)', lambda m: TTSService._int_to_arabic(int(m.group(0)[:-1])) + ' بالمائة', text)
        text = re.sub(r'\d+(?:\.\d+)+', TTSService._number_to_spoken_arabic, text)
        text = re.sub(r'\d+\.\d+', TTSService._number_to_spoken_arabic, text)
        text = re.sub(r'\b\d+\b', TTSService._number_to_spoken_arabic, text)
        return text

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Clean text for natural TTS — strip markdown, JSON, code, citations."""
        t = text.strip()
        t = t.translate(TTSService._ARABIC_TO_WESTERN)
        t = TTSService._numerals_to_arabic_words(t)
        t = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', t)
        t = re.sub(r'[<>&]', ' ', t)
        t = t.replace('\u200f', '').replace('\u200e', '')
        t = re.sub(r'```[\s\S]*?```', '', t)
        t = re.sub(r'`([^`]*)`', r'\1', t)
        t = re.sub(r'^#{1,6}\s*', '', t, flags=re.MULTILINE)
        t = re.sub(r'\*\*([^*]+)\*\*', r'\1', t)
        t = re.sub(r'__([^_]+)__', r'\1', t)
        t = re.sub(r'\*([^*]+)\*', r'\1', t)
        t = re.sub(r'~~([^~]+)~~', r'\1', t)
        t = re.sub(r'\*+', '', t)
        t = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', t)
        t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
        t = re.sub(r'[{}\[\]"\'\\]', '', t)
        t = re.sub(
            r'\b(section_id|section_title|page|quote|answer_ar|citations|confidence)\s*[:=]',
            '', t, flags=re.IGNORECASE)
        t = re.sub(r'[^\S\n]+', ' ', t)
        t = re.sub(r'\n{3,}', '\n\n', t)
        t = t.strip()
        alpha_chars = sum(1 for c in t if c.isalpha())
        if alpha_chars < 2:
            return ""
        return t

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
        
        clean_text = self._sanitize_text(text)
        if not clean_text:
            logger.warning("TTS call skipped | text too short or no alpha chars | original_len=%d", len(text))
            return b""

        voice = self.get_voice(language, gender)
        logger.info("TTS call | provider=EdgeTTS | voice=%s | lang=%s | text_len=%d (clean=%d)",
                     voice, language, len(text), len(clean_text))
        
        communicate = edge_tts.Communicate(
            text=clean_text,
            voice=voice,
            rate=rate,
            pitch=pitch
        )
        
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            await communicate.save(tmp_path)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    
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
        
        clean_text = self._sanitize_text(text)
        voice = self.get_voice(language, gender)
        communicate = edge_tts.Communicate(text=clean_text, voice=voice)
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
        
        clean_text = self._sanitize_text(text)
        voice = self.get_voice(language, gender)
        communicate = edge_tts.Communicate(text=clean_text, voice=voice)
        
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


class XttsTTSService:
    """Local XTTS-v2 TTS with voice cloning.

    Lazily loads the model on first call.  Requires a short reference WAV per
    language/gender so XTTS can clone the voice timbre.  Falls back to Edge TTS
    on any error.
    """

    XTTS_SR = 24000
    LANG_MAP = {"ar": "ar", "en": "en"}
    MAX_XTTS_CHARS = 250

    def __init__(self):
        self._model = None
        self._gpt_cond_latents = {}
        self._speaker_embeddings = {}
        self._cuda_broken = False
        self._edge_fallback = TTSService()

    @staticmethod
    def _sanitize_for_xtts(text: str, lang: str) -> str:
        """Extra sanitisation specific to XTTS-v2 to prevent CUDA index errors.

        The XTTS GPT tokenizer has a limited vocabulary; characters that map
        to out-of-range token IDs cause ``srcIndex < srcSelectDimSize`` CUDA
        asserts that poison the entire CUDA context.
        """
        t = text
        t = re.sub(r'[\u0600-\u0605\u0610-\u061A\u064B-\u065F\u0670'
                    r'\u06D6-\u06ED\uFE70-\uFE7F]', '', t)
        t = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]', '', t)
        t = re.sub(r'[^\u0620-\u064A\u0660-\u0669a-zA-Z0-9\s.,!?؟،؛:()\-/]', ' ', t)
        t = re.sub(r'\s{2,}', ' ', t).strip()
        if len(t) > XttsTTSService.MAX_XTTS_CHARS:
            cut = t[:XttsTTSService.MAX_XTTS_CHARS]
            last_space = cut.rfind(' ')
            if last_space > XttsTTSService.MAX_XTTS_CHARS // 2:
                cut = cut[:last_space]
            t = cut.rstrip('.,!? ') + '.'
        return t

    def _load(self):
        if self._model is not None:
            return
        import gc
        import torch
        torch.backends.cudnn.enabled = False
        torch.backends.cudnn.benchmark = False

        logger.info("Loading XTTS-v2 model (this may take 15-20 s on first call)…")
        device = settings.xtts_device if torch.cuda.is_available() else "cpu"

        if device != "cpu":
            torch.cuda.empty_cache()

        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
        from TTS.utils.manage import ModelManager

        model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
        mm = ModelManager()
        model_path, config_path, _ = mm.download_model(model_name)

        config = XttsConfig()
        config.load_json(config_path)
        model = Xtts.init_from_config(config)
        model.load_checkpoint(config, checkpoint_dir=model_path, eval=True,
                              use_deepspeed=False)
        model.to(device)

        gc.collect()
        if device != "cpu":
            torch.cuda.empty_cache()

        self._model = model
        self._device = device
        vram = ""
        if device != "cpu":
            alloc = torch.cuda.memory_allocated() / 1024**2
            vram = f" | VRAM={alloc:.0f}MB"
        logger.info("XTTS-v2 loaded | device=%s%s", device, vram)

    def _get_conditioning(self, language: str, gender: str = "male"):
        lang = "ar" if language.startswith("ar") else "en"
        key = f"{lang}_{gender}"
        if key not in self._gpt_cond_latents:
            wav_path = (settings.xtts_speaker_wav_ar
                        if lang == "ar" else settings.xtts_speaker_wav_en)
            if not os.path.isabs(wav_path):
                wav_path = os.path.join(os.path.dirname(__file__), "..", wav_path)
            wav_path = os.path.abspath(wav_path)
            logger.info("Computing XTTS speaker embedding from %s", wav_path)
            gpt_cond, speaker_emb = self._model.get_conditioning_latents(
                audio_path=[wav_path],
            )
            self._gpt_cond_latents[key] = gpt_cond
            self._speaker_embeddings[key] = speaker_emb
        return self._gpt_cond_latents[key], self._speaker_embeddings[key]

    @staticmethod
    def _float_to_mp3_bytes(samples, sr: int) -> bytes:
        """Convert float32 samples [-1,1] to MP3 via ffmpeg (≈6x smaller than WAV)."""
        import subprocess
        import torch
        import numpy as np

        if isinstance(samples, torch.Tensor):
            samples = samples.squeeze().cpu().float().numpy()
        samples = np.clip(samples, -1.0, 1.0)
        int16 = (samples * 32767).astype(np.int16)
        raw_pcm = int16.tobytes()

        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
                "-codec:a", "libmp3lame", "-b:a", "64k",
                "-f", "mp3", "pipe:1",
            ],
            input=raw_pcm,
            capture_output=True,
        )
        if proc.returncode != 0:
            logger.error("ffmpeg MP3 encode failed: %s", proc.stderr.decode(errors="replace"))
            raise RuntimeError("ffmpeg MP3 encoding failed")
        return proc.stdout

    async def synthesize(
        self,
        text: str,
        language: str = "en",
        gender: str = "male",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> bytes:
        clean_text = TTSService._sanitize_text(text)
        if not clean_text:
            return b""

        if self._cuda_broken:
            return await self._edge_fallback.synthesize(
                text, language=language, gender=gender, rate=rate, pitch=pitch,
            )

        lang_code = "ar" if language.startswith("ar") else "en"
        safe_text = self._sanitize_for_xtts(clean_text, lang_code)
        if not safe_text or len(safe_text) < 3:
            return await self._edge_fallback.synthesize(
                text, language=language, gender=gender, rate=rate, pitch=pitch,
            )
        logger.info("TTS call | provider=XTTS-v2 | lang=%s | text_len=%d (safe=%d)",
                     lang_code, len(text), len(safe_text))
        try:
            def _generate():
                import torch
                self._load()
                gpt_cond, speaker_emb = self._get_conditioning(language, gender)
                with torch.no_grad():
                    out = self._model.inference(
                        text=safe_text,
                        language=lang_code,
                        gpt_cond_latent=gpt_cond,
                        speaker_embedding=speaker_emb,
                        temperature=0.65,
                        repetition_penalty=5.0,
                        top_k=50,
                        top_p=0.85,
                    )
                return self._float_to_mp3_bytes(out["wav"], self.XTTS_SR)

            return await asyncio.to_thread(_generate)
        except Exception as e:
            logger.error("XTTS synthesis failed, falling back to Edge TTS: %s", e, exc_info=True)
            if "CUDA" in str(e):
                self._reset_model()
            return await self._edge_fallback.synthesize(
                text, language=language, gender=gender, rate=rate, pitch=pitch,
            )

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str,
        language: str = "en",
        gender: str = "male",
    ) -> str:
        audio_bytes = await self.synthesize(text, language=language, gender=gender)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        return output_path

    async def stream_audio(
        self,
        text: str,
        language: str = "en",
        gender: str = "male",
    ) -> AsyncGenerator[bytes, None]:
        clean_text = TTSService._sanitize_text(text)
        if not clean_text:
            return

        if self._cuda_broken:
            async for chunk in self._edge_fallback.stream_audio(text, language, gender):
                yield chunk
            return

        lang_code = "ar" if language.startswith("ar") else "en"
        safe_text = self._sanitize_for_xtts(clean_text, lang_code)
        if not safe_text or len(safe_text) < 3:
            async for chunk in self._edge_fallback.stream_audio(text, language, gender):
                yield chunk
            return

        try:
            import queue as _queue
            import torch

            chunk_q: _queue.Queue = _queue.Queue()
            _DONE = object()

            def _stream_producer():
                try:
                    self._load()
                    gpt_cond, speaker_emb = self._get_conditioning(language, gender)
                    with torch.no_grad():
                        for chunk in self._model.inference_stream(
                            text=safe_text,
                            language=lang_code,
                            gpt_cond_latent=gpt_cond,
                            speaker_embedding=speaker_emb,
                            temperature=0.65,
                            repetition_penalty=5.0,
                            top_k=50,
                            top_p=0.85,
                            stream_chunk_size=20,
                        ):
                            mp3_bytes = self._float_to_mp3_bytes(chunk, self.XTTS_SR)
                            chunk_q.put(mp3_bytes)
                except Exception as exc:
                    chunk_q.put(exc)
                finally:
                    chunk_q.put(_DONE)

            import asyncio as _aio
            loop = _aio.get_event_loop()
            loop.run_in_executor(None, _stream_producer)

            while True:
                item = await asyncio.to_thread(chunk_q.get, timeout=120)
                if item is _DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        except Exception as e:
            logger.error("XTTS stream failed, falling back to Edge TTS: %s", e, exc_info=True)
            if "CUDA" in str(e):
                self._reset_model()
            async for chunk in self._edge_fallback.stream_audio(text, language, gender):
                yield chunk

    def _reset_model(self):
        """Release GPU model after a CUDA error.

        Once a CUDA assert fires the entire CUDA context is poisoned — no
        CUDA call will succeed until the *process* restarts.  We fall back
        to Edge TTS for the rest of this process lifetime.
        """
        import gc
        logger.warning("Resetting XTTS model after CUDA error — "
                        "falling back to Edge TTS until next restart")
        self._model = None
        self._gpt_cond_latents.clear()
        self._speaker_embeddings.clear()
        self._cuda_broken = True
        gc.collect()

    def preload(self):
        """Eagerly load model + speaker embeddings so first request is fast."""
        self._load()
        self._get_conditioning("ar")
        self._get_conditioning("en")
        logger.info("XTTS-v2 preload complete (model + both speaker embeddings cached)")

    @staticmethod
    async def list_voices(language: Optional[str] = None) -> list:
        return await TTSService.list_voices(language)


class NamaaTTSService:
    """Saudi Arabic TTS using NAMAA-Saudi-TTS (local 0.5B model).

    Lazily loads the model on first synthesis call. Falls back to Edge TTS
    for non-Arabic languages since NAMAA only supports Arabic.
    """

    def __init__(self):
        self._model = None
        self._sr: Optional[int] = None
        self._device: Optional[str] = None
        self._edge_fallback = TTSService()

    def _load(self):
        if self._model is not None:
            return
        logger.info("Loading NAMAA-Saudi-TTS model (first call, may take a moment)...")
        import torch
        from huggingface_hub import snapshot_download
        from safetensors.torch import load_file as load_safetensors
        from chatterbox import mtl_tts

        device = "cuda" if torch.cuda.is_available() else "cpu"
        ckpt_dir = snapshot_download(
            repo_id="NAMAA-Space/NAMAA-Saudi-TTS",
            repo_type="model",
        )
        self._model = mtl_tts.ChatterboxMultilingualTTS.from_pretrained(device=device)
        t3_state = load_safetensors(f"{ckpt_dir}/t3_mtl23ls_v2.safetensors", device=device)
        self._model.t3.load_state_dict(t3_state)
        self._model.t3.to(device).eval()
        self._sr = self._model.sr
        self._device = device
        logger.info("NAMAA-Saudi-TTS loaded | device=%s | sr=%d", device, self._sr)

    async def synthesize(
        self,
        text: str,
        language: str = "ar",
        gender: str = "male",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> bytes:
        """Synthesize speech. Falls back to Edge TTS for non-Arabic."""
        if not language.startswith("ar"):
            logger.info("TTS call | provider=NAMAA->EdgeTTS fallback | lang=%s | text_len=%d", language, len(text))
            return await self._edge_fallback.synthesize(
                text, language=language, gender=gender, rate=rate, pitch=pitch,
            )

        logger.info("TTS call | provider=NAMAA-Saudi-TTS | lang=%s | text_len=%d", language, len(text))

        def _generate():
            self._load()
            import torchaudio
            wav = self._model.generate(text, language_id="ar")
            buf = io.BytesIO()
            torchaudio.save(buf, wav, self._sr, format="wav")
            return buf.getvalue()

        return await asyncio.to_thread(_generate)

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str,
        language: str = "ar",
        gender: str = "male",
    ) -> str:
        """Synthesize and save to file."""
        audio_bytes = await self.synthesize(text, language=language, gender=gender)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        return output_path

    async def stream_audio(
        self,
        text: str,
        language: str = "ar",
        gender: str = "male",
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio. NAMAA doesn't truly stream, so we yield the full buffer."""
        if not language.startswith("ar"):
            async for chunk in self._edge_fallback.stream_audio(text, language, gender):
                yield chunk
            return

        audio = await self.synthesize(text, language=language, gender=gender)
        yield audio

    @staticmethod
    async def list_voices(language: Optional[str] = None) -> list:
        """NAMAA has a single voice; delegate listing to Edge TTS."""
        return await TTSService.list_voices(language)


# ---------------------------------------------------------------------------
# ElevenLabs TTS Backend
# ---------------------------------------------------------------------------

class ElevenLabsTTSService:
    """ElevenLabs cloud TTS — high-quality multilingual voice synthesis."""

    def __init__(self):
        from elevenlabs import ElevenLabs
        self._client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        self._voice_id = settings.elevenlabs_voice_id
        self._model = settings.elevenlabs_model
        logger.info("ElevenLabs TTS initialized | voice=%s model=%s",
                     self._voice_id, self._model)

    async def synthesize(
        self,
        text: str,
        language: str = "ar",
        gender: str = "male",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> bytes:
        clean = TTSService._sanitize_text(text)
        if not clean:
            return b""

        logger.info("TTS call | provider=ElevenLabs | lang=%s | text_len=%d",
                     language, len(clean))

        def _generate():
            audio_iter = self._client.text_to_speech.convert(
                voice_id=self._voice_id,
                text=clean,
                model_id=self._model,
                output_format="mp3_44100_128",
            )
            return b"".join(audio_iter)

        try:
            return await asyncio.to_thread(_generate)
        except Exception as e:
            logger.error("ElevenLabs TTS failed: %s", e, exc_info=True)
            return b""

    async def synthesize_to_file(self, text: str, output_path: str,
                                  language: str = "ar", gender: str = "male") -> str:
        audio = await self.synthesize(text, language=language, gender=gender)
        if audio:
            with open(output_path, "wb") as f:
                f.write(audio)
        return output_path

    async def synthesize_stream(self, text: str, language: str = "ar",
                                 gender: str = "male") -> AsyncGenerator[bytes, None]:
        audio = await self.synthesize(text, language=language, gender=gender)
        if audio:
            yield audio


# ---------------------------------------------------------------------------
# Singleton / Factory
# ---------------------------------------------------------------------------

_tts_service = None


def get_tts_service():
    """Get or create the TTS service singleton based on TTS_BACKEND setting."""
    global _tts_service
    if _tts_service is None:
        backend = settings.tts_backend.lower()
        if backend == "elevenlabs":
            logger.info("TTS backend: ElevenLabs (cloud)")
            _tts_service = ElevenLabsTTSService()
        elif backend == "xtts":
            logger.info("TTS backend: XTTS-v2 (local GPU) + Edge TTS fallback")
            _tts_service = XttsTTSService()
        elif backend == "namaa":
            logger.info("TTS backend: NAMAA-Saudi-TTS (Arabic) + Edge TTS (English fallback)")
            _tts_service = NamaaTTSService()
        else:
            logger.info("TTS backend: Edge TTS")
            _tts_service = TTSService()
    return _tts_service
