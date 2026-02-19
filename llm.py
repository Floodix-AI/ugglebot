"""
Uggly — LLM-integration
Skickar barnets text till Claude API och hanterar konversationshistorik.
"""

import logging
import re

from anthropic import Anthropic

from config import (
    ANTHROPIC_API_KEY,
    CHILD_AGE,
    CHILD_NAME,
    CLAUDE_MAX_TOKENS,
    CLAUDE_MODEL,
    CONVERSATION_HISTORY_LENGTH,
)
from cost_tracker import CostTracker
from prompts import get_system_prompt

log = logging.getLogger(__name__)


class ConversationManager:
    """
    Hanterar konversation med Claude API.

    Bevarar historik för multi-turn-samtal inom en session.
    Historiken rensas vid ny session (efter sömn/timeout).
    """

    def __init__(self, cost_tracker: CostTracker):
        self._client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self._cost = cost_tracker
        self._system_prompt = get_system_prompt(CHILD_AGE, CHILD_NAME)
        self._history: list[dict] = []
        log.info("LLM initierad — modell: %s, ålder: %d", CLAUDE_MODEL, CHILD_AGE)

    def chat(self, user_text: str) -> str:
        """
        Skicka barnets text till Claude och returnera svar.

        Args:
            user_text: Transkriberad text från barnet

        Returns:
            Claudes svar som text
        """
        # Kontrollera budget
        self._cost.check_budget()

        # Lägg till barnets meddelande i historiken
        self._history.append({"role": "user", "content": user_text})

        # Trimma historik om den är för lång (behåll senaste turn-paren)
        max_messages = CONVERSATION_HISTORY_LENGTH * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

        try:
            response = self._client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=self._system_prompt,
                messages=self._history,
            )

            assistant_text = response.content[0].text

            # Logga kostnad från response metadata
            self._cost.log_claude(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

            # Spara svaret i historiken
            self._history.append({"role": "assistant", "content": assistant_text})

            log.info("Claude-svar (%d in, %d ut tokens): '%s'",
                     response.usage.input_tokens,
                     response.usage.output_tokens,
                     assistant_text[:100])

            return assistant_text

        except Exception as e:
            log.error("Claude-fel: %s", e)
            # Ta bort det misslyckade meddelandet från historiken
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
            raise

    def chat_stream(self, user_text: str):
        """
        Streama Claude-svar som text-chunks (generator).

        Yields råa text-chunks efterhand som de genereras.
        Designad för att pipas direkt till ElevenLabs convert_realtime().
        Historik och kostnad loggas automatiskt efter sista yield.
        """
        self._cost.check_budget()
        self._history.append({"role": "user", "content": user_text})

        max_messages = CONVERSATION_HISTORY_LENGTH * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

        try:
            with self._client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=self._system_prompt,
                messages=self._history,
            ) as stream:
                for chunk in stream.text_stream:
                    yield chunk

                response = stream.get_final_message()

            self._cost.log_claude(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            self._history.append(
                {"role": "assistant", "content": response.content[0].text}
            )
            log.info(
                "Claude-svar (stream, %d in, %d ut tokens): '%s'",
                response.usage.input_tokens,
                response.usage.output_tokens,
                response.content[0].text[:100],
            )

        except Exception as e:
            log.error("Claude-fel: %s", e)
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
            raise

    def reset_conversation(self) -> None:
        """Nollställ konversationshistorik (vid ny session)."""
        self._history.clear()
        log.info("Konversationshistorik rensad")

    @property
    def turn_count(self) -> int:
        """Antal turn-par i nuvarande konversation."""
        return len([m for m in self._history if m["role"] == "user"])
