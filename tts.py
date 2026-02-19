"""
Uggly — Text-to-Speech
Konverterar text till tal med ElevenLabs API (streaming).
Fallback till gTTS om ElevenLabs-kvota tar slut.
"""

import io
import logging
import tempfile
from pathlib import Path

from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_MODEL,
    ELEVENLABS_SIMILARITY,
    ELEVENLABS_STABILITY,
    ELEVENLABS_VOICE_ID,
    LANGUAGE,
)
from audio import AudioManager
from cost_tracker import CostTracker

log = logging.getLogger(__name__)


class TextToSpeech:
    """
    Konverterar text till tal och spelar upp via högtalare.

    Primär: ElevenLabs streaming (låg latens, hög kvalitet).
    Fallback: gTTS (gratis, lägre kvalitet).
    """

    def __init__(self, cost_tracker: CostTracker, audio_manager: AudioManager):
        self._cost = cost_tracker
        self._audio = audio_manager
        self._client = None
        self._setup_elevenlabs()

    def _setup_elevenlabs(self) -> None:
        """Initiera ElevenLabs-klient om API-nyckel finns."""
        if not ELEVENLABS_API_KEY:
            log.warning("ELEVENLABS_API_KEY saknas — använder gTTS-fallback")
            return

        try:
            from elevenlabs.client import ElevenLabs
            self._client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            log.info(
                "ElevenLabs initierad — modell: %s, röst: %s",
                ELEVENLABS_MODEL,
                ELEVENLABS_VOICE_ID or "(ej vald)",
            )
        except Exception as e:
            log.error("Kunde inte initiera ElevenLabs: %s — använder gTTS", e)
            self._client = None

    def speak(self, text: str) -> None:
        """
        Konvertera text till tal och spela upp.
        Streamar audio direkt till högtalaren för minimal latens.

        Args:
            text: Texten som ska läsas upp
        """
        if not text:
            return

        # Kontrollera budget
        self._cost.check_budget()
        self._cost.log_elevenlabs(len(text))

        if self._client and ELEVENLABS_VOICE_ID:
            try:
                self._speak_elevenlabs(text)
                return
            except Exception as e:
                log.warning("ElevenLabs-fel: %s — provar gTTS-fallback", e)

        # Fallback till gTTS
        self._speak_gtts(text)

    def _speak_elevenlabs(self, text: str) -> None:
        """Streama TTS via ElevenLabs direkt till högtalare."""
        audio_stream = self._client.text_to_speech.convert(
            text=text,
            voice_id=ELEVENLABS_VOICE_ID,
            model_id=ELEVENLABS_MODEL,
            output_format="pcm_16000",  # Rå PCM 16kHz 16-bit mono
            voice_settings={
                "stability": ELEVENLABS_STABILITY,
                "similarity_boost": ELEVENLABS_SIMILARITY,
            },
        )

        # Streama PCM-chunks direkt till PyAudio
        self._audio.play_pcm_stream(
            audio_stream,
            sample_rate=16000,
            sample_width=2,
        )

        log.debug("ElevenLabs uppspelning klar: '%s'", text[:50])

    def _speak_gtts(self, text: str) -> None:
        """Fallback: generera tal med gTTS (gratis, lägre kvalitet)."""
        try:
            from gtts import gTTS
            from pydub import AudioSegment

            tts = gTTS(text=text, lang=LANGUAGE)

            # gTTS ger MP3 — konvertera till PCM via pydub
            mp3_buf = io.BytesIO()
            tts.write_to_fp(mp3_buf)
            mp3_buf.seek(0)

            audio_segment = AudioSegment.from_mp3(mp3_buf)
            # Konvertera till 16kHz mono 16-bit
            audio_segment = (
                audio_segment
                .set_frame_rate(16000)
                .set_channels(1)
                .set_sample_width(2)
            )

            pcm_data = audio_segment.raw_data
            self._audio.play_pcm_bytes(pcm_data, sample_rate=16000, sample_width=2)

            log.debug("gTTS uppspelning klar: '%s'", text[:50])

        except Exception as e:
            log.error("gTTS-fel: %s — kunde inte spela upp tal", e)

    def speak_realtime(self, text_iterator) -> None:
        """
        End-to-end streaming: LLM text-chunks → ElevenLabs WebSocket → högtalare.

        Tar en iterator av text-strängar (t.ex. från llm.chat_stream()) och
        streamar dem via ElevenLabs WebSocket API direkt till PyAudio.
        Mycket lägre latens jämfört med att vänta på hela svaret.

        Args:
            text_iterator: Iterator[str] som yieldar text-chunks
        """
        if not self._client or not ELEVENLABS_VOICE_ID:
            # Fallback: samla ihop text och använd gTTS
            full_text = "".join(text_iterator)
            if full_text:
                self._cost.check_budget()
                self._cost.log_elevenlabs(len(full_text))
                self._speak_gtts(full_text)
            return

        self._cost.check_budget()

        # Wrappa iteratorn för att räkna tecken (kostnadsspårning)
        char_count = 0

        def counting_iterator():
            nonlocal char_count
            for chunk in text_iterator:
                char_count += len(chunk)
                yield chunk

        try:
            from elevenlabs import VoiceSettings

            audio_stream = self._client.text_to_speech.convert_realtime(
                voice_id=ELEVENLABS_VOICE_ID,
                text=counting_iterator(),
                model_id=ELEVENLABS_MODEL,
                output_format="pcm_16000",
                voice_settings=VoiceSettings(
                    stability=ELEVENLABS_STABILITY,
                    similarity_boost=ELEVENLABS_SIMILARITY,
                ),
            )

            self._audio.play_pcm_stream(
                audio_stream,
                sample_rate=16000,
                sample_width=2,
            )

            self._cost.log_elevenlabs(char_count)
            log.info("ElevenLabs realtime klar: %d tecken", char_count)

        except Exception as e:
            log.error("ElevenLabs realtime-fel: %s", e)
            raise

    def speak_file(self, filepath: Path) -> None:
        """Spela upp en förinspelade WAV-fil."""
        self._audio.play_wav_file(filepath)

    def list_voices(self) -> list[dict]:
        """
        Lista tillgängliga ElevenLabs-röster.
        Kör detta för att hitta en passande voice_id.
        """
        if not self._client:
            log.error("ElevenLabs-klient ej initierad")
            return []

        try:
            response = self._client.voices.get_all()
            voices = []
            for voice in response.voices:
                voices.append({
                    "voice_id": voice.voice_id,
                    "name": voice.name,
                    "category": getattr(voice, "category", ""),
                    "description": getattr(voice, "description", ""),
                })
            return voices
        except Exception as e:
            log.error("Kunde inte lista röster: %s", e)
            return []
