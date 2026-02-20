"""
Uggly — Konfiguration
All konfiguration samlas här. Värden laddas från .env-fil.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# === Sökvägar ===
PROJECT_DIR = Path(__file__).parent
ASSETS_DIR = PROJECT_DIR / "assets"

# === API-nycklar ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# === ElevenLabs TTS ===
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_STABILITY = float(os.getenv("ELEVENLABS_STABILITY", "0.5"))
ELEVENLABS_SIMILARITY = float(os.getenv("ELEVENLABS_SIMILARITY", "0.75"))

# === Claude LLM ===
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "200"))

# === Barninställningar ===
CHILD_AGE = int(os.getenv("CHILD_AGE", "6"))
CHILD_NAME = os.getenv("CHILD_NAME", "")
LANGUAGE = os.getenv("LANGUAGE", "sv")

# === Ljudinställningar ===
SAMPLE_RATE = 16000       # 16 kHz — standard för tal
CHANNELS = 1              # Mono
SAMPLE_WIDTH = 2          # 16-bit (2 bytes per sample)

# Chunk-storlekar i samples (inte bytes)
VAD_CHUNK_SIZE = 512      # 32 ms vid 16 kHz — Silero VAD optimal

# === VAD (Voice Activity Detection) ===
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
SILENCE_DURATION = float(os.getenv("SILENCE_DURATION", "1.5"))       # sek tystnad = slut på inspelning
MAX_RECORDING_DURATION = float(os.getenv("MAX_RECORDING_DURATION", "30"))  # max sek per inspelning

# === Wake-detektion (VAD-baserad) ===
# Högre threshold i sovläge för att undvika falska triggers (TV, syskon, etc.)
WAKE_VAD_THRESHOLD = float(os.getenv("WAKE_VAD_THRESHOLD", "0.7"))
# Längre sammanhängande tal krävs för att vakna (ms)
WAKE_SPEECH_DURATION_MS = int(os.getenv("WAKE_SPEECH_DURATION_MS", "600"))
# Max API-anrop per timme (säkerhetsnät mot run-away kostnader)
MAX_API_CALLS_PER_HOUR = int(os.getenv("MAX_API_CALLS_PER_HOUR", "30"))

# === Sessionsinställningar ===
SESSION_MAX_MINUTES = int(os.getenv("SESSION_MAX_MINUTES", "30"))
IDLE_TIMEOUT_SECONDS = int(os.getenv("IDLE_TIMEOUT_SECONDS", "120"))
CONVERSATION_HISTORY_LENGTH = int(os.getenv("CONVERSATION_HISTORY_LENGTH", "10"))

# === Kostnadsbegränsning ===
DAILY_BUDGET_SEK = float(os.getenv("DAILY_BUDGET_SEK", "5.0"))
COST_LOG_PATH = PROJECT_DIR / os.getenv("COST_LOG_FILE", "cost_log.json")
USD_TO_SEK = float(os.getenv("USD_TO_SEK", "10.5"))

# Ungefärliga kostnader per enhet (USD)
WHISPER_COST_PER_MINUTE_USD = 0.006
CLAUDE_INPUT_COST_PER_MTOK_USD = 1.0    # Haiku 4.5
CLAUDE_OUTPUT_COST_PER_MTOK_USD = 5.0   # Haiku 4.5
ELEVENLABS_COST_PER_KCHAR_USD = 0.10

# === Plattformsdetektering ===
IS_RASPBERRY_PI = (
    Path("/proc/device-tree/model").exists()
    or os.getenv("FORCE_PI_MODE", "").lower() == "true"
)

# === LED ===
LED_ENABLED = os.getenv("LED_ENABLED", "auto")  # "auto", "true", "false"

# === Ljudfiler ===
STARTUP_SOUND_PATH = ASSETS_DIR / "startup.wav"
WAKE_SOUND_PATH = ASSETS_DIR / "wake.wav"
THINKING_SOUND_PATH = ASSETS_DIR / "thinking.wav"
GOODNIGHT_SOUND_PATH = ASSETS_DIR / "goodnight.wav"
BUDGET_SOUND_PATH = ASSETS_DIR / "budget.wav"
ERROR_SOUND_PATH = ASSETS_DIR / "error.wav"

# === VAD-modell ===
VAD_MODEL_PATH = ASSETS_DIR / "silero_vad.onnx"
