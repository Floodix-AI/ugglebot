# Ugglebot 🦉

AI-driven röstassistent för barn (4–10 år) som körs på Raspberry Pi Zero 2 W med ReSpeaker 2-Mics HAT.

Barnet pratar till en fysisk uggla-leksak och Ugglebot svarar med röst — på svenska!

## Så fungerar det

```
Barn pratar → VAD detekterar tal → Inspelning → Whisper (STT) → Claude (LLM) → ElevenLabs (TTS) → Svar via högtalare
```

**State machine:**
```
SLEEPING → (tal detekterat) → RECORDING → (tystnad) → PROCESSING → (svar) → SPEAKING → LISTENING
```

## Tech stack

| Komponent | Teknik | Körs |
|-----------|--------|------|
| Wake / VAD | Silero VAD (ONNX) | Lokalt |
| Speech-to-Text | OpenAI Whisper API | Moln |
| LLM | Claude Haiku 4.5 | Moln |
| Text-to-Speech | ElevenLabs Flash v2.5 | Moln |
| LED-kontroll | APA102 via SPI | Lokalt (Pi) |

## Snabbstart (Mac/Linux)

### 1. Installera dependencies

```bash
# Skapa och aktivera venv
python3 -m venv .venv
source .venv/bin/activate

# Installera Python-paket
pip install -r requirements.txt

# Ladda ner Silero VAD-modell
curl -L -o assets/silero_vad.onnx \
  https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx
```

### 2. Konfigurera API-nycklar

```bash
cp .env.example .env
# Redigera .env och fyll i:
#   OPENAI_API_KEY
#   ANTHROPIC_API_KEY
#   ELEVENLABS_API_KEY
#   ELEVENLABS_VOICE_ID (kör: python test_local.py voices)
```

### 3. Testa

```bash
# Lista ljudenheter
python test_local.py audio

# Testa Whisper + Claude + ElevenLabs pipeline
python test_local.py pipeline

# Se alla tester
python test_local.py
```

### 4. Kör

```bash
python main.py
```

## Raspberry Pi Setup

```bash
chmod +x setup_pi.sh
./setup_pi.sh
```

Scriptet:
1. Installerar system-dependencies
2. Aktiverar SPI (för LEDs)
3. Ökar swap till 1024 MB
4. Installerar ReSpeaker-drivrutiner
5. Skapar Python venv
6. Laddar ner VAD-modell
7. Skapar systemd-service för autostart

## Projektstruktur

```
ugglebot/
├── main.py              # Entry point — state machine
├── config.py            # All konfiguration (env-vars)
├── audio.py             # Mikrofon-inspelning & uppspelning
├── wake_word.py         # Wake-detektion (VAD-baserad)
├── vad.py               # Voice Activity Detection (Silero ONNX)
├── stt.py               # Speech-to-Text (Whisper API)
├── llm.py               # Claude API + konversationshistorik
├── tts.py               # Text-to-Speech (ElevenLabs streaming)
├── cost_tracker.py      # Kostnadsloggning & budgetgräns
├── prompts.py           # Åldersanpassade systemprompts
├── led.py               # ReSpeaker LED-kontroll (Pi only)
├── test_local.py        # Komponenttester
├── setup_pi.sh          # Pi setup-script
├── requirements.txt     # Python dependencies
├── .env.example         # Mall för API-nycklar
└── assets/
    └── silero_vad.onnx  # VAD-modell (~2 MB)
```

## Kostnadskontroll

Ugglebot spårar varje API-anrop i `cost_log.json` och stänger automatiskt av sig vid daglig budgetgräns (default: 5 SEK/dag).

```bash
# Se dagens kostnader
python test_local.py cost
```

**Ungefärlig kostnad per interaktion:**
- Whisper: ~0.001 SEK (5 sek ljud)
- Claude Haiku: ~0.005 SEK (kort svar)
- ElevenLabs: ~0.03 SEK (50 tecken)
- **Totalt: ~0.04 SEK per fråga-svar**

## Konfiguration

All konfiguration sker via `.env`-filen. Se `.env.example` för alla tillgängliga inställningar.

Viktiga inställningar:
- `CHILD_AGE` — Barnets ålder (4–10), påverkar systempromptens komplexitet
- `DAILY_BUDGET_SEK` — Max daglig kostnad i SEK
- `VAD_THRESHOLD` — Känslighet för taldetektion (0.0–1.0)
- `ELEVENLABS_VOICE_ID` — Röst-ID från ElevenLabs

## Minnesanvändning

Optimerad för Pi Zero 2 W (512 MB RAM):
- Ingen PyTorch — Silero VAD körs via ONNX Runtime (~30 MB)
- Inga tunga wake word-bibliotek — VAD:en hanterar allt
- Total: ~155 MB (gott om marginal)
