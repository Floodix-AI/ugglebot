"""
Uggly — Config Sync
Registrerar enheten automatiskt vid första uppstart.
Hämtar inställningar från webbplattformen, cachar lokalt.
Skickar usage-data via API.
"""

import json
import logging
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

log = logging.getLogger("config_sync")

PROJECT_DIR = Path(__file__).parent
DEVICE_ID_PATH = PROJECT_DIR / ".device_id"
API_KEY_PATH = PROJECT_DIR / ".api_key"
CACHE_PATH = PROJECT_DIR / "device_settings_cache.json"

UGGLY_API_URL = os.getenv("UGGLY_API_URL", "")


def _speak_pairing_code(code: str) -> None:
    """Läs upp parkopplingskoden via TTS (om tillgänglig)."""
    try:
        # Bokstavera koden tydligt
        spelled = " ".join(code.upper())
        text = f"Din parkopplingskod är: {spelled}. Igen: {spelled}."
        log.info("Läser upp parkopplingskod via TTS")

        from gtts import gTTS
        from pydub import AudioSegment
        import io

        tts = gTTS(text=text, lang="sv")
        mp3_buf = io.BytesIO()
        tts.write_to_fp(mp3_buf)
        mp3_buf.seek(0)

        audio_segment = AudioSegment.from_mp3(mp3_buf)
        audio_segment = (
            audio_segment
            .set_frame_rate(16000)
            .set_channels(1)
            .set_sample_width(2)
        )

        from audio import AudioManager
        audio = AudioManager()
        audio.play_pcm_bytes(audio_segment.raw_data, sample_rate=16000, sample_width=2)
        audio.cleanup()

    except Exception as e:
        log.warning("Kunde inte läsa upp parkopplingskod: %s", e)


def _get_api_key() -> str:
    """Läs API-nyckel från fil eller env."""
    if API_KEY_PATH.exists():
        return API_KEY_PATH.read_text().strip()
    return os.getenv("DEVICE_API_KEY", "")


def _get_device_id() -> str:
    """Läs device-ID från fil."""
    if DEVICE_ID_PATH.exists():
        return DEVICE_ID_PATH.read_text().strip()
    return ""


def is_registered() -> bool:
    """Kontrollera om enheten är registrerad."""
    return bool(_get_device_id() and _get_api_key())


def register_device() -> dict:
    """
    Registrera enheten på webbplattformen.
    Returnerar { device_id, api_key, pairing_code }.
    Sparar device_id och api_key lokalt.
    """
    if not UGGLY_API_URL:
        log.error("UGGLY_API_URL saknas — kan inte registrera")
        return {}

    url = f"{UGGLY_API_URL}/api/devices/register"
    req = Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

            device_id = data.get("device_id", "")
            api_key = data.get("api_key", "")
            pairing_code = data.get("pairing_code", "")

            if device_id and api_key:
                DEVICE_ID_PATH.write_text(device_id)
                API_KEY_PATH.write_text(api_key)
                log.info("Enhet registrerad!")
                log.info("═══════════════════════════════════════")
                log.info("  PARKOPPLINGSKOD:  %s", pairing_code)
                log.info("  Ange koden på din Uggly-dashboard")
                log.info("═══════════════════════════════════════")

                # Läs upp parkopplingskoden via TTS
                _speak_pairing_code(pairing_code)
            else:
                log.error("Ogiltig registreringsdata: %s", data)

            return data

    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        log.error("Registrering misslyckades: %s", e)
        return {}


def ensure_registered() -> bool:
    """Registrera om inte redan registrerad. Returnerar True om redo."""
    if is_registered():
        return True
    log.info("Enheten är inte registrerad — registrerar nu...")
    result = register_device()
    return bool(result.get("device_id"))


def fetch_settings() -> dict:
    """Hämta inställningar från webbplattformen. Returnerar cachade vid fel."""
    device_id = _get_device_id()
    api_key = _get_api_key()

    if not UGGLY_API_URL or not device_id or not api_key:
        log.warning("Credentials saknas — använder cache/defaults")
        return _load_cache()

    url = f"{UGGLY_API_URL}/api/devices/{device_id}/config"
    req = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
    })

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            settings = data.get("settings", {})
            _save_cache(settings)
            log.info("Inställningar hämtade från webbplattformen")
            return settings
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        log.warning("Kunde inte hämta inställningar: %s — använder cache", e)
        return _load_cache()


def upload_usage(usage_data: dict) -> bool:
    """Skicka usage-data till webbplattformen."""
    device_id = _get_device_id()
    api_key = _get_api_key()

    if not UGGLY_API_URL or not device_id or not api_key:
        return False

    url = f"{UGGLY_API_URL}/api/devices/{device_id}/usage"
    payload = json.dumps(usage_data).encode()

    req = Request(url, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    try:
        with urlopen(req, timeout=10):
            log.info("Usage-data skickad")
            return True
    except (URLError, TimeoutError) as e:
        log.warning("Kunde inte skicka usage-data: %s", e)
        return False


def _save_cache(settings: dict) -> None:
    """Spara inställningar lokalt."""
    try:
        CACHE_PATH.write_text(json.dumps(settings, indent=2))
    except OSError as e:
        log.warning("Kunde inte spara cache: %s", e)


def _load_cache() -> dict:
    """Ladda cachade inställningar."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def apply_settings(settings: dict) -> None:
    """Applicera hämtade inställningar på config-modulen."""
    if not settings:
        return

    import config

    mapping = {
        "child_name": ("CHILD_NAME", str),
        "child_age": ("CHILD_AGE", int),
        "language": ("LANGUAGE", str),
        "voice_id": ("ELEVENLABS_VOICE_ID", str),
        "voice_stability": ("ELEVENLABS_STABILITY", float),
        "voice_similarity": ("ELEVENLABS_SIMILARITY", float),
        "daily_budget_sek": ("DAILY_BUDGET_SEK", float),
        "session_max_minutes": ("SESSION_MAX_MINUTES", int),
        "idle_timeout_seconds": ("IDLE_TIMEOUT_SECONDS", int),
        "max_tokens": ("CLAUDE_MAX_TOKENS", int),
    }

    for key, (config_attr, type_fn) in mapping.items():
        if key in settings and settings[key] is not None:
            value = type_fn(settings[key])
            setattr(config, config_attr, value)
            log.debug("config.%s = %s", config_attr, value)

    log.info("Inställningar applicerade")


def clear_wifi_setup_flag() -> bool:
    """Meddela backend att WiFi-setup är klar (rensa wifi_setup_requested)."""
    device_id = _get_device_id()
    api_key = _get_api_key()

    if not UGGLY_API_URL or not device_id or not api_key:
        return False

    url = f"{UGGLY_API_URL}/api/devices/{device_id}/wifi-setup-done"
    payload = json.dumps({"done": True}).encode()

    req = Request(url, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    try:
        with urlopen(req, timeout=10):
            log.info("WiFi-setup-flagga rensad")
            return True
    except (URLError, TimeoutError) as e:
        log.warning("Kunde inte rensa WiFi-setup-flagga: %s", e)
        return False
