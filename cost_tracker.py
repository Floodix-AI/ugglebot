"""
Uggly — Kostnadsspårning
Loggar alla API-anrop till JSON-fil och kontrollerar daglig budget.
"""

import json
import logging
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path

from config import (
    CLAUDE_INPUT_COST_PER_MTOK_USD,
    CLAUDE_OUTPUT_COST_PER_MTOK_USD,
    COST_LOG_PATH,
    DAILY_BUDGET_SEK,
    ELEVENLABS_COST_PER_KCHAR_USD,
    MAX_API_CALLS_PER_HOUR,
    USD_TO_SEK,
    WHISPER_COST_PER_MINUTE_USD,
)

log = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Kastas när daglig kostnadsbudget överskridits."""
    pass


class RateLimitError(Exception):
    """Kastas när API-anrop per timme överskridits."""
    pass


class CostTracker:
    """
    Spårar API-kostnader per dag i en JSON-fil.

    Varje post loggar tjänst, kostnad (USD), och detaljer.
    Budget kontrolleras i SEK med konfigurerbar växelkurs.
    """

    def __init__(self, log_path: Path | None = None):
        self._log_path = log_path or COST_LOG_PATH
        self._entries: list[dict] = self._load()
        # Rate limiting: spåra API-anrop senaste timmen
        self._api_call_times: deque[float] = deque()
        log.info(
            "Kostnadsspårning startad — dagens total: %.2f SEK (budget: %.2f SEK, max %d anrop/h)",
            self.get_daily_total_sek(),
            DAILY_BUDGET_SEK,
            MAX_API_CALLS_PER_HOUR,
        )

    def _load(self) -> list[dict]:
        """Ladda befintlig kostnadslogg från JSON-fil."""
        if not self._log_path.exists():
            return []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Kunde inte läsa kostnadslogg: %s — startar ny", e)
            return []

    def _save(self) -> None:
        """Spara kostnadslogg till JSON-fil."""
        try:
            with open(self._log_path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f, indent=2, ensure_ascii=False)
        except IOError as e:
            log.error("Kunde inte spara kostnadslogg: %s", e)

    def _add_entry(self, service: str, cost_usd: float, details: str) -> None:
        """Lägg till en kostnadspost."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "service": service,
            "cost_usd": round(cost_usd, 6),
            "cost_sek": round(cost_usd * USD_TO_SEK, 4),
            "details": details,
        }
        self._entries.append(entry)
        self._save()
        log.debug(
            "Kostnad: %s — %.4f USD (%.4f SEK) — %s",
            service,
            cost_usd,
            cost_usd * USD_TO_SEK,
            details,
        )

    def check_budget(self) -> None:
        """
        Kontrollera daglig budget och rate limit.
        Kastar BudgetExceededError eller RateLimitError.
        Anropa INNAN varje API-anrop.
        """
        total = self.get_daily_total_sek()
        if total >= DAILY_BUDGET_SEK:
            raise BudgetExceededError(
                f"Daglig budget överskriden: {total:.2f} SEK (gräns: {DAILY_BUDGET_SEK:.2f} SEK)"
            )

        # Rate limiting: rensa gamla anrop (äldre än 1 timme)
        now = time.time()
        while self._api_call_times and (now - self._api_call_times[0]) > 3600:
            self._api_call_times.popleft()

        if len(self._api_call_times) >= MAX_API_CALLS_PER_HOUR:
            raise RateLimitError(
                f"Max API-anrop per timme nått: {MAX_API_CALLS_PER_HOUR}"
            )

        self._api_call_times.append(now)

    def log_whisper(self, audio_duration_seconds: float) -> None:
        """Logga Whisper API-kostnad."""
        cost = (audio_duration_seconds / 60) * WHISPER_COST_PER_MINUTE_USD
        self._add_entry(
            "whisper",
            cost,
            f"{audio_duration_seconds:.1f} sek ljud",
        )

    def log_claude(self, input_tokens: int, output_tokens: int) -> None:
        """Logga Claude API-kostnad baserat på token-användning."""
        input_cost = (input_tokens / 1_000_000) * CLAUDE_INPUT_COST_PER_MTOK_USD
        output_cost = (output_tokens / 1_000_000) * CLAUDE_OUTPUT_COST_PER_MTOK_USD
        total = input_cost + output_cost
        self._add_entry(
            "claude",
            total,
            f"{input_tokens} in + {output_tokens} ut tokens",
        )

    def log_elevenlabs(self, character_count: int) -> None:
        """Logga ElevenLabs TTS-kostnad."""
        cost = (character_count / 1000) * ELEVENLABS_COST_PER_KCHAR_USD
        self._add_entry(
            "elevenlabs",
            cost,
            f"{character_count} tecken",
        )

    def get_daily_total_sek(self) -> float:
        """Summera dagens kostnader i SEK."""
        today = date.today().isoformat()
        daily_usd = sum(
            entry["cost_usd"]
            for entry in self._entries
            if entry["timestamp"].startswith(today)
        )
        return daily_usd * USD_TO_SEK

    def get_daily_total_usd(self) -> float:
        """Summera dagens kostnader i USD."""
        today = date.today().isoformat()
        return sum(
            entry["cost_usd"]
            for entry in self._entries
            if entry["timestamp"].startswith(today)
        )

    def get_monthly_total_sek(self) -> float:
        """Summera denna månads kostnader i SEK (för statistik)."""
        month_prefix = date.today().strftime("%Y-%m")
        monthly_usd = sum(
            entry["cost_usd"]
            for entry in self._entries
            if entry["timestamp"].startswith(month_prefix)
        )
        return monthly_usd * USD_TO_SEK

    def get_summary(self) -> dict:
        """Returnera en sammanfattning av kostnader."""
        return {
            "daily_sek": round(self.get_daily_total_sek(), 2),
            "daily_usd": round(self.get_daily_total_usd(), 4),
            "monthly_sek": round(self.get_monthly_total_sek(), 2),
            "budget_sek": DAILY_BUDGET_SEK,
            "budget_remaining_sek": round(DAILY_BUDGET_SEK - self.get_daily_total_sek(), 2),
        }
