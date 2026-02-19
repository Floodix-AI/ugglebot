"""
Uggly — Voice Activity Detection (VAD)
Använder Silero VAD via ONNX Runtime direkt (ingen PyTorch).
Extremt lättviktig: ~2 MB modell + ~30 MB onnxruntime.
"""

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort

from config import (
    MAX_RECORDING_DURATION,
    SAMPLE_RATE,
    SILENCE_DURATION,
    VAD_CHUNK_SIZE,
    VAD_MODEL_PATH,
    VAD_THRESHOLD,
)

log = logging.getLogger(__name__)


class SileroVAD:
    """
    Silero VAD med ONNX Runtime — ingen PyTorch behövs.

    Modellen tar 512 samples (32 ms vid 16 kHz) åt gången och returnerar
    en sannolikhet (0.0–1.0) att chunken innehåller tal.
    """

    def __init__(self, model_path: Path | None = None):
        if model_path is None:
            model_path = VAD_MODEL_PATH

        if not model_path.exists():
            raise FileNotFoundError(
                f"Silero VAD-modell saknas: {model_path}\n"
                "Ladda ner med: curl -L -o assets/silero_vad.onnx "
                "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx"
            )

        # Konfigurera ONNX för minimal resursanvändning
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

        # Auto-detektera modellversion via input-namn
        input_names = [inp.name for inp in self._session.get_inputs()]
        output_names = [out.name for out in self._session.get_outputs()]
        log.debug("VAD modell inputs: %s, outputs: %s", input_names, output_names)

        # v5-modell: separata h/c tensorer. Äldre: kombinerad state-tensor.
        self._use_state = "state" in input_names
        if self._use_state:
            # Hämta state-shape från modellens metadata
            for inp in self._session.get_inputs():
                if inp.name == "state":
                    raw_shape = inp.shape
                    break
            # ONNX kan returnera None/str för dynamiska dimensioner — ersätt med defaults
            # Typisk state-shape: [2, 1, 128] för äldre Silero VAD-modeller
            default_shape = [2, 1, 128]
            self._state_shape = tuple(
                d if isinstance(d, int) else default_shape[i]
                for i, d in enumerate(raw_shape)
            )
            log.info("VAD modell (state-format): state shape=%s (raw=%s)", self._state_shape, raw_shape)
        else:
            log.info("VAD modell (h/c-format)")

        self._sample_rate = SAMPLE_RATE
        self._reset_state()
        log.info("Silero VAD laddad (ONNX) — chunk_size=%d samples", VAD_CHUNK_SIZE)

    def _reset_state(self) -> None:
        """Initiera/nollställ RNN:s dolda tillstånd."""
        if self._use_state:
            self._state = np.zeros(self._state_shape, dtype=np.float32)
            # v6-modellen kräver en kontextbuffert som prepend:as före varje chunk.
            # 64 samples vid 16 kHz, 32 samples vid 8 kHz.
            self._context_size = 64 if self._sample_rate == 16000 else 32
            self._context = np.zeros((1, self._context_size), dtype=np.float32)
        else:
            # Silero VAD v5 ONNX: h och c har formen [2, 1, 64]
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def process_chunk(self, pcm_chunk: bytes) -> float:
        """
        Bearbeta en audio-chunk och returnera tal-sannolikhet.

        Args:
            pcm_chunk: Rå 16-bit PCM-bytes (VAD_CHUNK_SIZE * 2 bytes)

        Returns:
            Sannolikhet (0.0–1.0) att chunken innehåller tal
        """
        # Konvertera int16 PCM till float32 [-1.0, 1.0]
        audio = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        audio = audio.reshape(1, -1)  # [1, chunk_size]

        if self._use_state:
            # v6-modellen kräver kontextbuffert prepend:ad före audio-chunken
            audio_with_context = np.concatenate([self._context, audio], axis=1)
            ort_inputs = {
                "input": audio_with_context,
                "sr": np.array(self._sample_rate, dtype=np.int64),
                "state": self._state,
            }
            output, self._state = self._session.run(None, ort_inputs)
            # Spara sista context_size samples som kontext för nästa chunk
            self._context = audio_with_context[:, -self._context_size:]
        else:
            ort_inputs = {
                "input": audio,
                "sr": np.array(self._sample_rate, dtype=np.int64),
                "h": self._h,
                "c": self._c,
            }
            output, self._h, self._c = self._session.run(None, ort_inputs)

        return float(output[0][0])

    def is_speech(self, pcm_chunk: bytes) -> bool:
        """Returnera True om chunken innehåller tal."""
        return self.process_chunk(pcm_chunk) >= VAD_THRESHOLD

    def reset(self) -> None:
        """Nollställ RNN-state (anropa mellan inspelningar)."""
        self._reset_state()


class SpeechEndDetector:
    """
    Wrapper kring SileroVAD som detekterar när talet tar slut.

    Spårar total inspelningstid och tystnadsperioder för att avgöra
    när inspelningen ska stoppas.
    """

    def __init__(self, vad: SileroVAD):
        self._vad = vad
        self._chunk_duration_ms = (VAD_CHUNK_SIZE / SAMPLE_RATE) * 1000  # 32 ms
        self.reset()

    def reset(self) -> None:
        """Nollställ detektorn för en ny inspelning."""
        self._vad.reset()
        self._silence_start_ms: float | None = None
        self._total_ms: float = 0.0
        self._has_detected_speech = False

    def process_chunk(self, pcm_chunk: bytes) -> tuple[bool, bool]:
        """
        Bearbeta en chunk och returnera (is_speech, should_stop).

        Args:
            pcm_chunk: Rå 16-bit PCM-bytes

        Returns:
            is_speech: True om chunken innehåller tal
            should_stop: True om inspelningen ska stoppas
                         (tystnad överstiger gränsen eller max tid nådd)
        """
        prob = self._vad.process_chunk(pcm_chunk)
        is_speech = prob >= VAD_THRESHOLD
        self._total_ms += self._chunk_duration_ms

        if is_speech:
            self._has_detected_speech = True
            self._silence_start_ms = None
        else:
            if self._silence_start_ms is None:
                self._silence_start_ms = self._total_ms

        # Stoppa om tystnad överstiger SILENCE_DURATION (men bara efter att tal detekterats)
        silence_exceeded = (
            self._has_detected_speech
            and self._silence_start_ms is not None
            and (self._total_ms - self._silence_start_ms) >= (SILENCE_DURATION * 1000)
        )

        # Stoppa om max inspelningstid nådd
        time_exceeded = self._total_ms >= (MAX_RECORDING_DURATION * 1000)

        should_stop = silence_exceeded or time_exceeded

        if should_stop:
            reason = "tystnad" if silence_exceeded else "max tid"
            log.debug(
                "Inspelning stoppad (%s) efter %.1f sek",
                reason,
                self._total_ms / 1000,
            )

        return is_speech, should_stop

    @property
    def total_duration_seconds(self) -> float:
        """Total inspelningstid i sekunder."""
        return self._total_ms / 1000
