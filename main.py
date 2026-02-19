"""
Uggly — Huvudprogram
AI-driven röstassistent för barn (4–10 år).

State machine:
  SLEEPING → (tal detekterat via VAD) → LISTENING
  LISTENING → (tal via VAD) → RECORDING
  LISTENING → (2 min timeout) → SLEEPING
  RECORDING → (1.5s tystnad) → PROCESSING
  PROCESSING → (svar klart) → SPEAKING
  PROCESSING → (tomt/fel) → LISTENING
  SPEAKING → (klart) → LISTENING
  Alla → (budget överskriden) → SLEEPING
  Alla → (KeyboardInterrupt) → Graceful shutdown
"""

import enum
import logging
import signal
import sys
import time

from config import (
    IDLE_TIMEOUT_SECONDS,
    SESSION_MAX_MINUTES,
    VAD_CHUNK_SIZE,
    VAD_THRESHOLD,
    WAKE_SOUND_PATH,
    THINKING_SOUND_PATH,
    GOODNIGHT_SOUND_PATH,
    BUDGET_SOUND_PATH,
    ERROR_SOUND_PATH,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ugglebot")


class State(enum.Enum):
    SLEEPING = "sleeping"
    LISTENING = "listening"
    RECORDING = "recording"
    PROCESSING = "processing"
    SPEAKING = "speaking"


def run() -> None:
    """Huvudloop — startar state machine."""

    # === Synka inställningar från Supabase ===
    try:
        from config_sync import fetch_settings, apply_settings
        settings = fetch_settings()
        apply_settings(settings)
    except Exception as e:
        log.warning("Config sync misslyckades: %s — använder lokala inställningar", e)

    # === Initiera komponenter ===
    log.info("Uggly startar...")

    from audio import AudioManager
    from wake_word import WakeDetector
    from vad import SileroVAD, SpeechEndDetector
    from cost_tracker import CostTracker, BudgetExceededError, RateLimitError
    from stt import SpeechToText
    from llm import ConversationManager
    from tts import TextToSpeech
    from led import get_led_controller

    audio = AudioManager()
    vad = SileroVAD()
    wake = WakeDetector(vad)  # Återanvänder VAD-modellen
    cost = CostTracker()
    stt = SpeechToText(cost)
    llm = ConversationManager(cost)
    tts = TextToSpeech(cost, audio)
    led = get_led_controller()

    # === State ===
    state = State.SLEEPING
    recorded_audio = bytearray()
    response_text = ""
    session_start: float | None = None
    last_activity: float = time.time()

    # === Graceful shutdown ===
    def handle_shutdown(sig, frame):
        log.info("Avslutar Uggly...")
        led.set_state("off")
        led.cleanup()
        wake.cleanup()
        audio.cleanup()
        log.info("Uggly avslutad.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    log.info("Uggly redo! Väntar på tal...")

    # === Huvudloop ===
    while True:
        try:
            led.set_state(state.value)

            match state:
                # ─── SLEEPING ────────────────────────────────────
                case State.SLEEPING:
                    log.info("💤 Uggly sover... lyssnar efter tal")
                    audio.start_input_stream(VAD_CHUNK_SIZE)
                    wake.reset()

                    while state == State.SLEEPING:
                        try:
                            chunk = audio.read_chunk()
                        except Exception:
                            time.sleep(0.1)
                            continue

                        if wake.process_audio(chunk):
                            log.info("🦉 Tal detekterat — vaknar!")
                            audio.stop_input_stream()

                            # Spela uppvakningsljud
                            tts.speak_file(WAKE_SOUND_PATH)

                            # Starta ny session
                            session_start = time.time()
                            last_activity = time.time()
                            llm.reset_conversation()

                            # Börja spela in direkt (barnet pratar redan)
                            # Inkludera buffrat ljud så inget av talet går förlorat
                            recorded_audio = bytearray(wake.get_buffered_audio())
                            state = State.RECORDING

                # ─── LISTENING ───────────────────────────────────
                case State.LISTENING:
                    log.info("👂 Lyssnar efter tal...")
                    audio.start_input_stream(VAD_CHUNK_SIZE)
                    vad.reset()

                    while state == State.LISTENING:
                        # Kontrollera session-timeout (30 min)
                        if session_start and (time.time() - session_start) > SESSION_MAX_MINUTES * 60:
                            log.info("⏰ Session-timeout (%d min) — lägger sig", SESSION_MAX_MINUTES)
                            audio.stop_input_stream()
                            tts.speak_file(GOODNIGHT_SOUND_PATH)
                            state = State.SLEEPING
                            break

                        # Kontrollera inaktivitet-timeout (2 min)
                        if (time.time() - last_activity) > IDLE_TIMEOUT_SECONDS:
                            log.info("😴 Inaktivitet-timeout (%d sek) — lägger sig", IDLE_TIMEOUT_SECONDS)
                            audio.stop_input_stream()
                            tts.speak_file(GOODNIGHT_SOUND_PATH)
                            state = State.SLEEPING
                            break

                        try:
                            chunk = audio.read_chunk()
                        except Exception:
                            time.sleep(0.1)
                            continue

                        # Detektera tal med VAD
                        prob = vad.process_chunk(chunk)
                        if prob >= VAD_THRESHOLD:
                            log.info("🎙️ Tal detekterat (sannolikhet: %.2f) — spelar in", prob)
                            audio.stop_input_stream()
                            # Inkludera denna chunk i inspelningen
                            recorded_audio = bytearray(chunk)
                            state = State.RECORDING

                # ─── RECORDING ───────────────────────────────────
                case State.RECORDING:
                    log.info("⏺️ Spelar in...")
                    audio.start_input_stream(VAD_CHUNK_SIZE)
                    speech_detector = SpeechEndDetector(vad)

                    while state == State.RECORDING:
                        try:
                            chunk = audio.read_chunk()
                        except Exception:
                            time.sleep(0.1)
                            continue

                        recorded_audio.extend(chunk)
                        _, should_stop = speech_detector.process_chunk(chunk)

                        if should_stop:
                            duration = len(recorded_audio) / (16000 * 2)
                            log.info("⏹️ Inspelning klar: %.1f sek", duration)
                            audio.stop_input_stream()
                            state = State.PROCESSING

                # ─── PROCESSING ──────────────────────────────────
                case State.PROCESSING:
                    log.info("🤔 Bearbetar...")
                    led.set_state("processing")

                    # Spela tänkeljud i bakgrunden (om filen finns)
                    tts.speak_file(THINKING_SOUND_PATH)

                    try:
                        # Budget-koll sker inuti stt och llm
                        # 1. Speech-to-text
                        text = stt.transcribe(bytes(recorded_audio))
                        recorded_audio.clear()

                        if not text or len(text.strip()) < 2:
                            log.info("Tom transkription — tillbaka till lyssning")
                            state = State.LISTENING
                            last_activity = time.time()
                            continue

                        log.info("Barn sa: '%s'", text)

                        # 2. End-to-end streaming: LLM → ElevenLabs WebSocket → högtalare
                        led.set_state("speaking")
                        tts.speak_realtime(llm.chat_stream(text))

                        state = State.LISTENING
                        last_activity = time.time()

                    except BudgetExceededError as e:
                        log.warning("💰 Budget överskriden: %s", e)
                        recorded_audio.clear()
                        tts.speak_file(BUDGET_SOUND_PATH)
                        state = State.SLEEPING

                    except Exception as e:
                        log.error("Bearbetningsfel: %s", e, exc_info=True)
                        recorded_audio.clear()
                        tts.speak_file(ERROR_SOUND_PATH)
                        state = State.LISTENING
                        last_activity = time.time()

                # ─── SPEAKING (fallback, normalt ej nådd) ───────
                case State.SPEAKING:
                    log.info("🔊 Spelar upp svar...")
                    try:
                        tts.speak(response_text)
                    except BudgetExceededError as e:
                        log.warning("💰 Budget överskriden under TTS: %s", e)
                        tts.speak_file(BUDGET_SOUND_PATH)
                        state = State.SLEEPING
                        continue
                    except Exception as e:
                        log.error("TTS-fel: %s", e, exc_info=True)
                        tts.speak_file(ERROR_SOUND_PATH)

                    response_text = ""
                    state = State.LISTENING
                    last_activity = time.time()

        except (BudgetExceededError, RateLimitError) as e:
            log.warning("💰 Kostnadsspärr: %s", e)
            try:
                audio.stop_input_stream()
                tts.speak_file(BUDGET_SOUND_PATH)
            except Exception:
                pass
            state = State.SLEEPING

        except KeyboardInterrupt:
            handle_shutdown(None, None)

        except Exception as e:
            log.error("Oväntat fel i huvudloop: %s", e, exc_info=True)
            try:
                audio.stop_input_stream()
            except Exception:
                pass
            # Kort paus, sedan tillbaka till lyssning
            time.sleep(1)
            state = State.LISTENING
            last_activity = time.time()


if __name__ == "__main__":
    run()
