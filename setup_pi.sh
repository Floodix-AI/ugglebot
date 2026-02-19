#!/bin/bash
# ============================================================================
# Ugglebot — Raspberry Pi Setup Script
# Sätter upp allt som behövs för att köra Ugglebot på Pi Zero 2 W
# med ReSpeaker 2-Mics HAT.
#
# Användning:
#   chmod +x setup_pi.sh
#   ./setup_pi.sh
# ============================================================================

set -e  # Avbryt vid fel

UGGLEBOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$UGGLEBOT_DIR/.venv"

echo "============================================"
echo "  Ugglebot — Raspberry Pi Setup"
echo "============================================"
echo ""

# --- 1. Uppdatera systemet ---
echo "[1/9] Uppdaterar systemet..."
sudo apt update && sudo apt upgrade -y

# --- 2. Installera system-dependencies ---
echo ""
echo "[2/9] Installerar system-dependencies..."
sudo apt install -y \
    portaudio19-dev \
    libatlas-base-dev \
    python3-pip \
    python3-venv \
    python3-dev \
    git \
    ffmpeg \
    mpg123 \
    libasound2-dev \
    libsndfile1

# --- 3. Aktivera SPI (för ReSpeaker LEDs) ---
echo ""
echo "[3/9] Aktiverar SPI..."
if ! grep -q "^dtparam=spi=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null; then
    if [ -f /boot/firmware/config.txt ]; then
        echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt
    else
        sudo raspi-config nonint do_spi 0
    fi
    echo "SPI aktiverat (kräver omstart)"
else
    echo "SPI redan aktiverat"
fi

# --- 4. Öka swap (Pi Zero 2 W har bara 512 MB RAM) ---
echo ""
echo "[4/9] Konfigurerar swap (1024 MB)..."
if grep -q "CONF_SWAPSIZE=100" /etc/dphys-swapfile 2>/dev/null; then
    sudo sed -i 's/CONF_SWAPSIZE=100/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
    sudo systemctl restart dphys-swapfile
    echo "Swap ökad till 1024 MB"
else
    echo "Swap redan konfigurerad"
fi

# --- 5. Installera ReSpeaker-drivrutiner ---
echo ""
echo "[5/9] Installerar ReSpeaker 2-Mic drivrutiner..."
if [ ! -d /tmp/seeed-voicecard ]; then
    git clone --depth 1 https://github.com/HinTak/seeed-voicecard /tmp/seeed-voicecard || \
    echo "⚠️  Kunde inte klona seeed-voicecard — du kan behöva installera drivrutinerna manuellt"
fi
if [ -d /tmp/seeed-voicecard ]; then
    cd /tmp/seeed-voicecard
    sudo ./install.sh || echo "⚠️  ReSpeaker-installation misslyckades — kan kräva omstart och nytt försök"
    cd "$UGGLEBOT_DIR"
fi

# --- 6. Skapa Python venv och installera dependencies ---
echo ""
echo "[6/9] Skapar Python-miljö..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

pip install --upgrade pip
pip install -r "$UGGLEBOT_DIR/requirements.txt"

echo "Python-miljö skapad i $VENV_DIR"

# --- 7. Ladda ner Silero VAD ONNX-modell ---
echo ""
echo "[7/9] Laddar ner Silero VAD-modell..."
if [ ! -f "$UGGLEBOT_DIR/assets/silero_vad.onnx" ]; then
    curl -L -o "$UGGLEBOT_DIR/assets/silero_vad.onnx" \
        "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx"
    echo "VAD-modell nedladdad"
else
    echo "VAD-modell finns redan"
fi

# --- 8. Skapa .env-fil ---
echo ""
echo "[8/9] Konfigurerar miljövariabler..."
if [ ! -f "$UGGLEBOT_DIR/.env" ]; then
    cp "$UGGLEBOT_DIR/.env.example" "$UGGLEBOT_DIR/.env"
    echo "⚠️  .env skapad från .env.example"
    echo "    VIKTIGT: Redigera .env och fyll i dina API-nycklar:"
    echo "    nano $UGGLEBOT_DIR/.env"
else
    echo ".env finns redan"
fi

# --- 9. Skapa systemd-service ---
echo ""
echo "[9/9] Skapar systemd-service..."
SERVICE_FILE="/etc/systemd/system/ugglebot.service"
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Ugglebot - AI röstassistent för barn
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$UGGLEBOT_DIR
ExecStart=$VENV_DIR/bin/python $UGGLEBOT_DIR/main.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

# Begränsa resurser
MemoryMax=400M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo "Systemd-service skapad: ugglebot.service"
echo ""

# --- Sammanfattning ---
echo "============================================"
echo "  Setup klar!"
echo "============================================"
echo ""
echo "Nästa steg:"
echo "  1. Redigera .env med dina API-nycklar:"
echo "     nano $UGGLEBOT_DIR/.env"
echo ""
echo "  2. Testa ljud:"
echo "     source $VENV_DIR/bin/activate"
echo "     python test_local.py audio"
echo ""
echo "  3. Testa hela pipeline:"
echo "     python test_local.py pipeline"
echo ""
echo "  4. Starta Ugglebot:"
echo "     python main.py"
echo ""
echo "  5. Aktivera autostart vid boot:"
echo "     sudo systemctl enable ugglebot"
echo "     sudo systemctl start ugglebot"
echo ""
echo "  6. Se loggar:"
echo "     journalctl -u ugglebot -f"
echo ""

# Testa audio-enheter om vi kan
echo "=== Ljudenhetstest ==="
if command -v arecord &> /dev/null; then
    echo "Inspelningsenheter:"
    arecord -l 2>/dev/null || echo "  (inga hittade)"
    echo ""
    echo "Uppspelningsenheter:"
    aplay -l 2>/dev/null || echo "  (inga hittade)"
else
    echo "arecord/aplay ej installerat — skippar ljudtest"
fi

echo ""
echo "🦉 Ugglebot är redo! Starta med: python main.py"
