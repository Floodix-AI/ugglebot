# Ugglebot — assets

Denna mapp innehåller ljudfiler och modeller som Ugglebot behöver.

## Filer som behövs

- `silero_vad.onnx` — Silero VAD-modell (laddas ner av setup_pi.sh)
- `wake.wav` — Kort "hu-hu!" ljud när wake word detekteras (~1 sek)
- `thinking.wav` — "Hmm"-ljud medan svar genereras (~1 sek)
- `goodnight.wav` — "Nu ska jag vila lite!" vid inaktivitet (~2 sek)
- `budget.wav` — "Jag behöver ladda!" vid budgetgräns (~2 sek)
- `error.wav` — Kort felljud (~0.5 sek)

## Generera ljudfiler

Kör `python test_local.py generate-assets` för att generera ljudfilerna
med ElevenLabs API (eller gTTS som fallback).

## Ladda ner VAD-modell

```bash
curl -L -o assets/silero_vad.onnx \
  https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx
```
