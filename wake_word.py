"""
Ugglebot — Wake-detektion
Använder Silero VAD för att detektera att någon börjar prata.
Ingen separat wake word — barnet pratar bara och Ugglebot vaknar.

Kräver sammanhängande tal i ~600 ms för att trigga (konfigurerbart),
med högre VAD-threshold än i inspelningsläge för att undvika
falska triggers från TV-ljud, syskon, etc.
Buffrar talet så att inget av barnets röst går förlorad.
"""

import logging
from collections import deque

from config import (
    SAMPLE_RATE,
    VAD_CHUNK_SIZE,
    WAKE_SPEECH_DURATION_MS,
    WAKE_VAD_THRESHOLD,
)

log = logging.getLogger(__name__)

# Beräkna antal chunks baserat på konfiguration
_CHUNK_DURATION_MS = (VAD_CHUNK_SIZE / SAMPLE_RATE) * 1000  # 32 ms
_CHUNKS_NEEDED = max(1, int(WAKE_SPEECH_DURATION_MS / _CHUNK_DURATION_MS))


class WakeDetector:
    """
    Detekterar att någon börjar prata med hjälp av Silero VAD.

    Kräver ~600 ms sammanhängande tal med högt VAD-tröskelvärde (0.7)
    för att trigga. Buffrar alla tal-chunks så att inget ljud
    går förlorat när vi övergår till inspelning.
    """

    def __init__(self, vad):
        """
        Args:
            vad: En SileroVAD-instans (återanvänds från huvudprogrammet)
        """
        self._vad = vad
        self._consecutive_speech = 0
        self._speech_buffer: deque[bytes] = deque(maxlen=_CHUNKS_NEEDED + 10)
        self.frame_length: int = VAD_CHUNK_SIZE

        log.info(
            "WakeDetector: threshold=%.2f, duration=%dms (%d chunks)",
            WAKE_VAD_THRESHOLD,
            WAKE_SPEECH_DURATION_MS,
            _CHUNKS_NEEDED,
        )

    def process_audio(self, pcm_chunk: bytes) -> bool:
        """
        Mata in en audio-chunk och returnera True om wake detekteras.

        Använder WAKE_VAD_THRESHOLD (högre än VAD_THRESHOLD) och kräver
        längre sammanhängande tal för att undvika falska triggers.

        Args:
            pcm_chunk: Rå 16-bit PCM-bytes (VAD_CHUNK_SIZE * 2 bytes)

        Returns:
            True om wake detekterades (sammanhängande tal)
        """
        prob = self._vad.process_chunk(pcm_chunk)

        if prob >= WAKE_VAD_THRESHOLD:
            self._consecutive_speech += 1
            self._speech_buffer.append(pcm_chunk)

            if self._consecutive_speech >= _CHUNKS_NEEDED:
                log.info(
                    "Wake: %.0f ms sammanhängande tal detekterat",
                    self._consecutive_speech * _CHUNK_DURATION_MS,
                )
                return True
        else:
            self._consecutive_speech = 0
            self._speech_buffer.clear()

        return False

    def get_buffered_audio(self) -> bytes:
        """
        Hämta allt buffrat tal-ljud sedan wake-detektion startade.
        Anropa efter att process_audio() returnerat True.
        """
        audio = b"".join(self._speech_buffer)
        self._speech_buffer.clear()
        return audio

    def reset(self) -> None:
        """Nollställ räknare och buffer."""
        self._consecutive_speech = 0
        self._speech_buffer.clear()
        self._vad.reset()

    def cleanup(self) -> None:
        """Inga resurser att frigöra (VAD ägs av huvudprogrammet)."""
        pass
