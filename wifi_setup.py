"""
Uggly — WiFi-provisionering
Hanterar WiFi-konfigurering via AP-läge och captive portal.

Flöden:
  A) Första uppstart utan WiFi → AP-läge → captive portal → anslut
  B) Dashboard triggar WiFi-byte → AP-läge → captive portal → anslut
  C) Sparat WiFi försvinner → automatiskt AP-läge vid boot
"""

import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

from config import ASSETS_DIR

log = logging.getLogger("wifi_setup")

PORTAL_HTML_PATH = ASSETS_DIR / "wifi_setup.html"
PORTAL_PORT = 80
AP_GATEWAY = "192.168.4.1"

# Timeout för att vänta på existerande WiFi-anslutning vid boot
WIFI_CONNECT_TIMEOUT = 30


def has_internet(timeout: float = 5.0) -> bool:
    """Kontrollera om Pi:n har internetanslutning."""
    try:
        sock = socket.create_connection(("8.8.8.8", 53), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def get_hotspot_name() -> str:
    """Generera unikt hotspot-namn baserat på Pi:ns serienummer."""
    serial = "0000"
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        for line in cpuinfo.splitlines():
            if line.startswith("Serial"):
                serial = line.split(":")[-1].strip()[-4:].upper()
                break
    except OSError:
        pass
    return f"Uggly-{serial}"


def _scan_networks() -> list[dict]:
    """Scanna tillgängliga WiFi-nätverk. Returnerar lista av {ssid, signal, freq}."""
    try:
        # Rescan först
        subprocess.run(
            ["nmcli", "dev", "wifi", "rescan"],
            capture_output=True, timeout=10,
        )
        time.sleep(2)

        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,FREQ", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=10,
        )
        networks = []
        seen_ssids = set()

        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue

            ssid = parts[0].strip()
            if not ssid or ssid in seen_ssids:
                continue

            signal = int(parts[1]) if parts[1].isdigit() else 0
            freq = int(parts[2]) if parts[2].isdigit() else 0

            # Pi Zero 2 W stödjer bara 2.4 GHz (< 3000 MHz)
            if freq > 3000:
                continue

            seen_ssids.add(ssid)
            networks.append({
                "ssid": ssid,
                "signal": signal,
                "freq": freq,
            })

        # Sortera efter signalstyrka (starkast först)
        networks.sort(key=lambda n: n["signal"], reverse=True)
        return networks

    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("Nätverksscanning misslyckades: %s", e)
        return []


def _start_hotspot(ssid: str) -> bool:
    """Starta WiFi-hotspot (AP-läge) via NetworkManager."""
    try:
        # Stoppa eventuell existerande hotspot
        subprocess.run(
            ["nmcli", "con", "down", "Hotspot"],
            capture_output=True, timeout=10,
        )
        time.sleep(1)

        # Starta ny hotspot (öppen, ingen lösenordskrav)
        result = subprocess.run(
            ["nmcli", "dev", "wifi", "hotspot",
             "ifname", "wlan0",
             "con-name", "Hotspot",
             "ssid", ssid,
             "band", "bg",
             "password", ""],
            capture_output=True, text=True, timeout=15,
        )

        if result.returncode != 0:
            # Vissa nm-versioner kräver lösenord — använd ett enkelt
            result = subprocess.run(
                ["nmcli", "dev", "wifi", "hotspot",
                 "ifname", "wlan0",
                 "con-name", "Hotspot",
                 "ssid", ssid,
                 "band", "bg"],
                capture_output=True, text=True, timeout=15,
            )

        if result.returncode == 0:
            log.info("Hotspot '%s' startad", ssid)
            return True

        log.error("Kunde inte starta hotspot: %s", result.stderr)
        return False

    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("Hotspot-start misslyckades: %s", e)
        return False


def _stop_hotspot() -> None:
    """Stoppa hotspot och återgå till station-läge."""
    try:
        subprocess.run(
            ["nmcli", "con", "down", "Hotspot"],
            capture_output=True, timeout=10,
        )
        log.info("Hotspot stoppad")
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("Kunde inte stoppa hotspot: %s", e)


def _connect_to_wifi(ssid: str, password: str) -> bool:
    """Försök ansluta till ett WiFi-nätverk."""
    try:
        # Stoppa hotspot först
        _stop_hotspot()
        time.sleep(2)

        result = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", ssid,
             "password", password,
             "ifname", "wlan0"],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode == 0:
            log.info("Ansluten till '%s'", ssid)
            # Vänta på att anslutningen ska stabiliseras
            time.sleep(3)
            return has_internet(timeout=10)

        log.warning("Anslutning misslyckades: %s", result.stderr)
        return False

    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("WiFi-anslutning misslyckades: %s", e)
        return False


def _start_dns_redirect() -> subprocess.Popen | None:
    """Starta dnsmasq för att redirecta alla DNS-frågor till captive portal."""
    try:
        # Skriv minimal dnsmasq-config
        dnsmasq_conf = (
            f"interface=wlan0\n"
            f"bind-interfaces\n"
            f"address=/#/{AP_GATEWAY}\n"
            f"no-resolv\n"
            f"no-hosts\n"
        )
        conf_path = Path("/tmp/uggly_dnsmasq.conf")
        conf_path.write_text(dnsmasq_conf)

        proc = subprocess.Popen(
            ["dnsmasq", "--no-daemon", "-C", str(conf_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("DNS-redirect startad")
        return proc

    except (OSError, FileNotFoundError) as e:
        log.warning("Kunde inte starta dnsmasq: %s — captive portal kanske inte auto-öppnas", e)
        return None


class CaptivePortalHandler(BaseHTTPRequestHandler):
    """HTTP-handler för captive portal."""

    # Delas mellan alla requests
    _pairing_code = ""
    _connected = False

    def log_message(self, format, *args):
        """Tysta standard-loggar, använd vår logger."""
        log.debug("HTTP: %s", format % args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_portal(self) -> None:
        """Visa captive portal HTML-sida."""
        try:
            html = PORTAL_HTML_PATH.read_text(encoding="utf-8")
            self._send_html(html)
        except OSError:
            self._send_html("<h1>Uggly WiFi Setup</h1><p>Portal-fil saknas</p>")

    def do_GET(self) -> None:
        if self.path == "/scan":
            networks = _scan_networks()
            self._send_json({"networks": networks})

        elif self.path == "/status":
            self._send_json({
                "connected": self._connected,
                "pairing_code": self._pairing_code,
            })

        elif self.path.startswith("/generate_204") or self.path.startswith("/hotspot-detect"):
            # Android/iOS captive portal detection — redirect till portal
            self.send_response(302)
            self.send_header("Location", f"http://{AP_GATEWAY}/")
            self.end_headers()

        else:
            # Alla andra requests → visa portalen
            self._serve_portal()

    def do_POST(self) -> None:
        if self.path == "/connect":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Ogiltig data"}, 400)
                return

            ssid = data.get("ssid", "").strip()
            password = data.get("password", "")

            if not ssid:
                self._send_json({"error": "SSID saknas"}, 400)
                return

            log.info("Försöker ansluta till '%s'...", ssid)

            # Anslut i bakgrundstråd så HTTP-svaret inte blockas
            # (hotspot stängs av → HTTP-anslutning bryts)
            def connect_worker():
                success = _connect_to_wifi(ssid, password)
                CaptivePortalHandler._connected = success
                if success:
                    log.info("WiFi-anslutning lyckades!")
                else:
                    log.warning("WiFi-anslutning misslyckades — startar om hotspot")
                    hotspot_name = get_hotspot_name()
                    _start_hotspot(hotspot_name)

            # Skicka svar först, sedan anslut
            self._send_json({"status": "connecting"})
            threading.Thread(target=connect_worker, daemon=True).start()

        else:
            self._send_json({"error": "Okänd endpoint"}, 404)


def run_wifi_setup(led=None, tts=None) -> str:
    """
    Kör WiFi-setup: starta hotspot + captive portal.
    Blockerar tills WiFi är konfigurerat och internet fungerar.

    Args:
        led: LED-controller (optional) — visar setup-mönster
        tts: TextToSpeech (optional) — spelar WiFi-behövs-ljud

    Returns:
        Hotspot-namn som användes
    """
    hotspot_name = get_hotspot_name()
    log.info("Startar WiFi-setup (hotspot: %s)...", hotspot_name)

    # LED: setup-läge
    if led:
        led.set_state("setup")

    # Spela ljud: "Jag behöver WiFi!"
    if tts:
        wifi_sound = ASSETS_DIR / "wifi_needed.wav"
        if wifi_sound.exists():
            try:
                tts.speak_file(wifi_sound)
            except Exception as e:
                log.warning("Kunde inte spela WiFi-ljud: %s", e)

    # Starta hotspot
    if not _start_hotspot(hotspot_name):
        log.error("Kunde inte starta hotspot — avbryter WiFi-setup")
        return hotspot_name

    # Starta DNS-redirect för captive portal auto-detect
    dns_proc = _start_dns_redirect()

    # Starta HTTP-server
    CaptivePortalHandler._connected = False
    CaptivePortalHandler._pairing_code = ""

    try:
        server = HTTPServer(("0.0.0.0", PORTAL_PORT), CaptivePortalHandler)
        server.timeout = 1.0
        log.info("Captive portal startad på http://%s/", AP_GATEWAY)

        # Vänta tills anslutning lyckas
        while not CaptivePortalHandler._connected:
            server.handle_request()

        log.info("WiFi konfigurerat! Stänger captive portal.")

    except OSError as e:
        log.error("Kunde inte starta HTTP-server: %s", e)
        log.info("Port %d kanske redan används — försök 'sudo lsof -i :%d'", PORTAL_PORT, PORTAL_PORT)

    finally:
        # Städa upp
        if dns_proc:
            dns_proc.terminate()
            try:
                dns_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dns_proc.kill()
        _stop_hotspot()

    return hotspot_name


def wait_for_wifi_or_setup(led=None, tts=None) -> None:
    """
    Vänta på WiFi-anslutning vid boot. Om ingen anslutning
    inom timeout → starta WiFi-setup.

    Anropas från main.py innan run().
    """
    log.info("Kontrollerar WiFi-anslutning...")

    # Snabbcheck: redan ansluten?
    if has_internet():
        log.info("WiFi redan anslutet!")
        return

    # Vänta — WiFi kanske fortfarande ansluter efter boot
    log.info("Väntar %d sek på WiFi-anslutning...", WIFI_CONNECT_TIMEOUT)
    start = time.time()
    while time.time() - start < WIFI_CONNECT_TIMEOUT:
        time.sleep(3)
        if has_internet():
            log.info("WiFi anslutet!")
            return

    # Ingen anslutning — starta setup
    log.info("Ingen WiFi — startar setup-läge")
    run_wifi_setup(led=led, tts=tts)
