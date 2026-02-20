"""
Uggly — Ljud I/O
Hanterar mikrofon-inspelning och ljuduppspelning.
Fungerar på både Mac/Linux (utveckling) och Raspberry Pi (produktion).
"""

import io
import logging
import struct
import threading
import wave
from pathlib import Path

import numpy as np
import pyaudio

from config import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH

log = logging.getLogger(__name__)


class AudioManager:
    """Central hanterare för all ljud-I/O. Trådsäker."""

    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._lock = threading.Lock()
        self._input_stream: pyaudio.Stream | None = None
        self._input_device_index, self._input_channels = self._find_device(is_input=True)
        self._output_device_index, _ = self._find_device(is_input=False)

        log.info(
            "Audio initierat — input device: %s (%d ch), output device: %s",
            self._input_device_index,
            self._input_channels,
            self._output_device_index,
        )

    def _find_device(self, is_input: bool) -> tuple[int | None, int]:
        """Hitta ReSpeaker eller default ljudenhet. Returnerar (index, kanaler)."""
        respeaker_keywords = ["seeed", "respeaker", "2mic", "ac108"]
        device_count = self._pa.get_device_count()

        # Steg 1: Sök enheter som rapporterar rätt antal kanaler
        for i in range(device_count):
            info = self._pa.get_device_info_by_index(i)
            name = info.get("name", "").lower()
            max_ch = info.get("maxInputChannels" if is_input else "maxOutputChannels", 0)
            if max_ch > 0:
                for keyword in respeaker_keywords:
                    if keyword in name:
                        ch = min(max_ch, 2) if is_input else CHANNELS
                        log.info("Hittade ReSpeaker %s: %s (%d ch)", "mic" if is_input else "output", info["name"], ch)
                        return i, ch

        # Steg 2: PortAudio-bugg med WM8960 — rapporterar 0 input-kanaler
        # trots att ALSA capture fungerar. Tvinga device om vi hittar ReSpeaker.
        if is_input:
            for i in range(device_count):
                info = self._pa.get_device_info_by_index(i)
                name = info.get("name", "").lower()
                for keyword in respeaker_keywords:
                    if keyword in name:
                        log.info("ReSpeaker hittad med 0 input-kanaler (PortAudio-bugg) — tvingar device %d med 2 ch", i)
                        return i, 2

        # Fallback till default
        try:
            if is_input:
                default = self._pa.get_default_input_device_info()
            else:
                default = self._pa.get_default_output_device_info()
            log.info("Använder default %s: %s", "input" if is_input else "output", default["name"])
            return int(default["index"]), CHANNELS
        except IOError:
            log.warning("Ingen %s-enhet hittad", "input" if is_input else "output")
            return None, CHANNELS

    def start_input_stream(self, chunk_size: int) -> None:
        """Öppna mikrofon-inspelningsström med given chunk-storlek (i samples)."""
        with self._lock:
            self._close_input_stream_unlocked()
            self._input_stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._input_channels,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._input_device_index,
                frames_per_buffer=chunk_size,
            )
            log.debug("Input-ström startad (chunk_size=%d, channels=%d)", chunk_size, self._input_channels)

    def read_chunk(self) -> bytes:
        """Läs en chunk från mikrofon-strömmen. Returnerar mono PCM-bytes."""
        if self._input_stream is None:
            raise RuntimeError("Ingen input-ström är öppen")
        data = self._input_stream.read(
            self._input_stream._frames_per_buffer,
            exception_on_overflow=False,
        )
        # Konvertera stereo → mono (ta vänster kanal)
        if self._input_channels == 2:
            samples = np.frombuffer(data, dtype=np.int16)
            mono = samples[0::2]  # Vänster kanal (varannan sample)
            return mono.tobytes()
        return data

    def stop_input_stream(self) -> None:
        """Stäng mikrofon-strömmen."""
        with self._lock:
            self._close_input_stream_unlocked()

    def _close_input_stream_unlocked(self) -> None:
        """Intern: stäng input-ström utan lock (anropas inifrån locked kontext)."""
        if self._input_stream is not None:
            try:
                self._input_stream.stop_stream()
                self._input_stream.close()
            except Exception as e:
                log.debug("Fel vid stängning av input-ström: %s", e)
            self._input_stream = None

    def play_pcm_stream(
        self,
        audio_chunks,
        sample_rate: int = SAMPLE_RATE,
        sample_width: int = SAMPLE_WIDTH,
        channels: int = CHANNELS,
    ) -> None:
        """
        Spela upp PCM-ljud från en iterator av byte-chunks.
        Används för ElevenLabs streaming-TTS.
        Blockerar tills all audio är uppspelad.
        """
        with self._lock:
            stream = self._pa.open(
                format=self._pa.get_format_from_width(sample_width),
                channels=channels,
                rate=sample_rate,
                output=True,
                output_device_index=self._output_device_index,
            )
            try:
                for chunk in audio_chunks:
                    if chunk:
                        stream.write(chunk)
            finally:
                stream.stop_stream()
                stream.close()

    def play_wav_file(self, filepath: Path) -> None:
        """Spela upp en WAV-fil. Blockerar tills klart."""
        if not filepath.exists():
            log.warning("Ljudfil saknas: %s", filepath)
            return

        with wave.open(str(filepath), "rb") as wf:
            stream = self._pa.open(
                format=self._pa.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
                output_device_index=self._output_device_index,
            )
            try:
                chunk_size = 1024
                data = wf.readframes(chunk_size)
                while data:
                    stream.write(data)
                    data = wf.readframes(chunk_size)
            finally:
                stream.stop_stream()
                stream.close()

    def play_pcm_bytes(
        self,
        pcm_data: bytes,
        sample_rate: int = SAMPLE_RATE,
        sample_width: int = SAMPLE_WIDTH,
    ) -> None:
        """Spela upp rå PCM-data (inte WAV). Blockerar tills klart."""
        with self._lock:
            stream = self._pa.open(
                format=self._pa.get_format_from_width(sample_width),
                channels=CHANNELS,
                rate=sample_rate,
                output=True,
                output_device_index=self._output_device_index,
            )
            try:
                # Spela i chunks om 4096 bytes
                chunk_size = 4096
                for i in range(0, len(pcm_data), chunk_size):
                    stream.write(pcm_data[i : i + chunk_size])
            finally:
                stream.stop_stream()
                stream.close()

    def list_devices(self) -> list[dict]:
        """Lista alla tillgängliga ljudenheter. Användbart för felsökning."""
        devices = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            devices.append(
                {
                    "index": i,
                    "name": info["name"],
                    "input_channels": info["maxInputChannels"],
                    "output_channels": info["maxOutputChannels"],
                    "default_sample_rate": info["defaultSampleRate"],
                }
            )
        return devices

    def cleanup(self) -> None:
        """Stäng alla strömmar och frigör resurser."""
        self.stop_input_stream()
        try:
            self._pa.terminate()
        except Exception as e:
            log.debug("Fel vid PyAudio-terminering: %s", e)
        log.info("Audio-resurser frigjorda")


def pcm_to_wav_bytes(pcm_data: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Konvertera rå PCM-data till WAV-format i minnet."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()
