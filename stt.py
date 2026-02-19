"""
Uggly — Speech-to-Text
Skickar inspelat ljud till OpenAI Whisper API för transkription.
"""

import io
import logging
import wave

from openai import OpenAI

from config import LANGUAGE, OPENAI_API_KEY, SAMPLE_RATE, SAMPLE_WIDTH, CHANNELS
from cost_tracker import CostTracker

log = logging.getLogger(__name__)


class SpeechToText:
    """Transkriberar ljud till text via Whisper API."""

    def __init__(self, cost_tracker: CostTracker):
        self._client = OpenAI(api_key=OPENAI_API_KEY)
        self._cost = cost_tracker

    def transcribe(self, pcm_audio: bytes) -> str:
        """
        Transkribera rå PCM-audio till text.

        Args:
            pcm_audio: Rå 16-bit 16kHz mono PCM-bytes

        Returns:
            Transkriberad text, eller tom sträng vid fel
        """
        if not pcm_audio:
            return ""

        # Beräkna ljudlängd för kostnadsloggning
        duration_seconds = len(pcm_audio) / (SAMPLE_RATE * SAMPLE_WIDTH)

        # Kontrollera budget innan API-anrop
        self._cost.check_budget()

        try:
            # Whisper API kräver en fil — wrappa PCM i WAV-container i minnet
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm_audio)
            wav_buffer.seek(0)
            wav_buffer.name = "audio.wav"  # OpenAI SDK kräver .name-attribut

            response = self._client.audio.transcriptions.create(
                model="whisper-1",
                file=wav_buffer,
                language=LANGUAGE,
            )

            text = response.text.strip()

            # Logga kostnad
            self._cost.log_whisper(duration_seconds)

            log.info("Transkription (%.1f sek): '%s'", duration_seconds, text)
            return text

        except Exception as e:
            log.error("Whisper-fel: %s", e)
            # Logga kostnaden ändå (vi vet inte om API:et debiterade)
            self._cost.log_whisper(duration_seconds)
            return ""
