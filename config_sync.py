"""
Uggly — Config Sync
Hämtar inställningar från Supabase vid start, cachar lokalt.
Skickar usage-data och heartbeat.
"""

import json
import logging
import os
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

log = logging.getLogger("config_sync")

PROJECT_DIR = Path(__file__).parent
DEVICE_ID_PATH = PROJECT_DIR / ".device_id"
CACHE_PATH = PROJECT_DIR / "device_settings_cache.json"

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "")


def get_device_id() -> str:
    """Hämta eller generera enhetens UUID."""
    if DEVICE_ID_PATH.exists():
        return DEVICE_ID_PATH.read_text().strip()
    device_id = str(uuid.uuid4())
    DEVICE_ID_PATH.write_text(device_id)
    log.info("Nytt device-ID genererat: %s", device_id)
    return device_id


def fetch_settings() -> dict:
    """Hämta inställningar från Supabase. Returnerar cachade vid fel."""
    device_id = get_device_id()

    if not SUPABASE_URL or not DEVICE_API_KEY:
        log.warning("SUPABASE_URL eller DEVICE_API_KEY saknas — använder cache/defaults")
        return _load_cache()

    url = f"{SUPABASE_URL}/api/devices/{device_id}/config"
    req = Request(url, headers={
        "Authorization": f"Bearer {DEVICE_API_KEY}",
    })

    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            settings = data.get("settings", {})
            _save_cache(settings)
            log.info("Inställningar hämtade från Supabase")
            return settings
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        log.warning("Kunde inte hämta inställningar: %s — använder cache", e)
        return _load_cache()


def upload_usage(device_id: str, usage_data: dict) -> bool:
    """Skicka usage-data till Supabase."""
    if not SUPABASE_URL or not DEVICE_API_KEY:
        return False

    url = f"{SUPABASE_URL}/rest/v1/usage_logs"
    payload = json.dumps({
        "device_id": device_id,
        **usage_data,
    }).encode()

    req = Request(url, data=payload, headers={
        "Authorization": f"Bearer {DEVICE_API_KEY}",
        "Content-Type": "application/json",
        "apikey": DEVICE_API_KEY,
        "Prefer": "return=minimal",
    })

    try:
        with urlopen(req, timeout=10):
            log.info("Usage-data skickad till Supabase")
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

    log.info("Inställningar applicerade från Supabase")
