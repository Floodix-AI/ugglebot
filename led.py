"""
Ugglebot — LED-kontroll för ReSpeaker 2-Mic HAT
ReSpeaker har 3 APA102 RGB LEDs styrda via SPI.
Körs bara på Raspberry Pi — no-op på Mac/Linux.

States → LED-mönster:
  sleeping:    Alla av
  listening:   Blå pulsering (sakta)
  recording:   Grön fast
  processing:  Gul pulsering (snabb)
  speaking:    Vit pulsering (medium)
  error:       Röd blink
  off:         Alla av
"""

import logging
import math
import threading
import time

from config import IS_RASPBERRY_PI, LED_ENABLED

log = logging.getLogger(__name__)

NUM_LEDS = 3  # ReSpeaker 2-Mic HAT har 3 LEDs


class LEDController:
    """Basklass för LED-kontroll."""

    def set_state(self, state: str) -> None:
        """Sätt LED-mönster baserat på state."""
        pass

    def cleanup(self) -> None:
        """Släck alla LEDs och frigör resurser."""
        pass


class NullLEDController(LEDController):
    """No-op LED-kontroll för Mac/Linux utveckling."""
    pass


class ReSpeakerLEDController(LEDController):
    """
    LED-kontroll för ReSpeaker 2-Mic HAT via SPI.
    APA102 protokoll: startram (4 bytes 0x00), per LED (0xE0|brightness, B, G, R), slutram.
    """

    def __init__(self):
        import spidev
        self._spi = spidev.SpiDev()
        self._spi.open(0, 0)  # SPI bus 0, device 0
        self._spi.max_speed_hz = 8000000  # 8 MHz

        self._state = "off"
        self._running = True
        self._lock = threading.Lock()

        # Starta animationstråd
        self._thread = threading.Thread(target=self._animation_loop, daemon=True)
        self._thread.start()
        log.info("ReSpeaker LED-kontroll startad")

    def _write_leds(self, colors: list[tuple[int, int, int]], brightness: int = 15) -> None:
        """
        Skriv färger till LEDs via SPI.

        Args:
            colors: Lista av (R, G, B) tupler, en per LED
            brightness: 0–31
        """
        # APA102 startram
        data = [0x00, 0x00, 0x00, 0x00]

        for r, g, b in colors:
            data.append(0xE0 | (brightness & 0x1F))
            data.append(b & 0xFF)
            data.append(g & 0xFF)
            data.append(r & 0xFF)

        # Slutram
        data.extend([0xFF, 0xFF, 0xFF, 0xFF])

        try:
            self._spi.xfer2(data)
        except Exception as e:
            log.debug("SPI-skrivfel: %s", e)

    def _all_off(self) -> None:
        """Släck alla LEDs."""
        self._write_leds([(0, 0, 0)] * NUM_LEDS, brightness=0)

    def set_state(self, state: str) -> None:
        """Sätt LED-state (trådsäkert)."""
        with self._lock:
            self._state = state

    def _animation_loop(self) -> None:
        """Bakgrundstråd som uppdaterar LEDs baserat på nuvarande state."""
        while self._running:
            with self._lock:
                state = self._state

            t = time.time()

            if state == "sleeping" or state == "off":
                self._all_off()
                time.sleep(0.5)  # Spara CPU i sömnläge

            elif state == "listening":
                # Blå pulsering (sakta, 2 sek cykel)
                brightness = int(2 + 13 * (0.5 + 0.5 * math.sin(t * math.pi)))
                self._write_leds([(0, 0, 200)] * NUM_LEDS, brightness=brightness)
                time.sleep(0.05)

            elif state == "recording":
                # Grön fast
                self._write_leds([(0, 200, 0)] * NUM_LEDS, brightness=10)
                time.sleep(0.1)

            elif state == "processing":
                # Gul pulsering (snabb, 0.5 sek cykel)
                brightness = int(2 + 13 * (0.5 + 0.5 * math.sin(t * 4 * math.pi)))
                self._write_leds([(200, 200, 0)] * NUM_LEDS, brightness=brightness)
                time.sleep(0.03)

            elif state == "speaking":
                # Vit pulsering (medium, 1 sek cykel)
                brightness = int(2 + 13 * (0.5 + 0.5 * math.sin(t * 2 * math.pi)))
                self._write_leds([(200, 200, 200)] * NUM_LEDS, brightness=brightness)
                time.sleep(0.05)

            elif state == "error":
                # Röd blink (0.3 sek on/off)
                on = int(t / 0.3) % 2 == 0
                if on:
                    self._write_leds([(255, 0, 0)] * NUM_LEDS, brightness=15)
                else:
                    self._all_off()
                time.sleep(0.1)

            elif state == "budget":
                # Röd fast
                self._write_leds([(255, 0, 0)] * NUM_LEDS, brightness=10)
                time.sleep(0.5)

            else:
                self._all_off()
                time.sleep(0.5)

    def cleanup(self) -> None:
        """Stäng av LEDs och frigör SPI."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._all_off()
        try:
            self._spi.close()
        except Exception:
            pass
        log.info("LED-kontroll avslutad")


def get_led_controller() -> LEDController:
    """
    Factory-funktion: returnera rätt LED-kontroller baserat på plattform.

    Returnerar ReSpeakerLEDController på Pi (om SPI finns),
    annars NullLEDController.
    """
    if LED_ENABLED == "false":
        log.info("LED-kontroll avaktiverad (LED_ENABLED=false)")
        return NullLEDController()

    if LED_ENABLED == "true" or (LED_ENABLED == "auto" and IS_RASPBERRY_PI):
        try:
            controller = ReSpeakerLEDController()
            return controller
        except Exception as e:
            log.warning("Kunde inte starta LED-kontroll: %s — kör utan LEDs", e)
            return NullLEDController()

    log.info("Kör utan LED-kontroll (ej Raspberry Pi)")
    return NullLEDController()
