#!/usr/bin/env python3
"""
Ugglebot — Lokalt testscript
Testar varje komponent separat och hela pipeline.

Användning:
  python test_local.py audio       # Testa mikrofon och uppspelning
  python test_local.py wake        # Testa wake word-detektion
  python test_local.py vad         # Testa Voice Activity Detection
  python test_local.py stt         # Testa Speech-to-Text (kräver API-nyckel)
  python test_local.py llm         # Testa Claude LLM (kräver API-nyckel)
  python test_local.py tts         # Testa Text-to-Speech (kräver API-nyckel)
  python test_local.py voices      # Lista tillgängliga ElevenLabs-röster
  python test_local.py pipeline    # Testa hela flödet
  python test_local.py cost        # Visa kostnadssummering
"""

import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test")


def test_audio():
    """Testa mikrofon och uppspelning."""
    from audio import AudioManager
    from config import SAMPLE_RATE, VAD_CHUNK_SIZE

    audio = AudioManager()

    print("\n=== Ljudenheter ===")
    for device in audio.list_devices():
        direction = []
        if device["input_channels"] > 0:
            direction.append("IN")
        if device["output_channels"] > 0:
            direction.append("UT")
        print(f"  [{device['index']}] {device['name']} ({'/'.join(direction)})")

    print("\n=== Spelar in 3 sekunder ===")
    print("Prata nu!")

    audio.start_input_stream(VAD_CHUNK_SIZE)
    frames = []
    chunks_needed = int(SAMPLE_RATE / VAD_CHUNK_SIZE * 3)  # 3 sekunder

    for _ in range(chunks_needed):
        chunk = audio.read_chunk()
        frames.append(chunk)

    audio.stop_input_stream()
    pcm_data = b"".join(frames)
    print(f"Inspelat: {len(pcm_data)} bytes ({len(pcm_data) / (SAMPLE_RATE * 2):.1f} sek)")

    print("\n=== Spelar upp inspelningen ===")
    audio.play_pcm_bytes(pcm_data)

    audio.cleanup()
    print("✅ Audio-test klart!")
    return pcm_data


def test_wake():
    """Testa wake-detektion (VAD-baserad)."""
    from audio import AudioManager
    from vad import SileroVAD
    from wake_word import WakeDetector
    from config import VAD_CHUNK_SIZE

    audio = AudioManager()
    vad = SileroVAD()
    wake = WakeDetector(vad)

    print(f"\n=== Wake-test (VAD-baserad, ~400ms tal triggar) ===")
    print("Börja prata för att trigga wake (tryck Ctrl+C för att avbryta)...")

    audio.start_input_stream(VAD_CHUNK_SIZE)

    try:
        while True:
            chunk = audio.read_chunk()
            if wake.process_audio(chunk):
                buffered = wake.get_buffered_audio()
                duration_ms = len(buffered) / (16000 * 2) * 1000
                print(f"🦉 Wake detekterat! ({duration_ms:.0f} ms buffrat ljud)")
                break
    except KeyboardInterrupt:
        print("\nAvbrutet.")
    finally:
        audio.stop_input_stream()
        audio.cleanup()

    print("✅ Wake-test klart!")


def test_vad():
    """Testa Voice Activity Detection i realtid."""
    from audio import AudioManager
    from vad import SileroVAD
    from config import VAD_CHUNK_SIZE, VAD_THRESHOLD

    audio = AudioManager()
    vad = SileroVAD()

    print(f"\n=== VAD-test (chunk_size={VAD_CHUNK_SIZE}, threshold={VAD_THRESHOLD}) ===")
    print("Prata och se VAD-sannolikhet (tryck Ctrl+C för att avbryta)...")

    audio.start_input_stream(VAD_CHUNK_SIZE)

    try:
        while True:
            chunk = audio.read_chunk()
            prob = vad.process_chunk(chunk)
            bar = "█" * int(prob * 50) + "░" * (50 - int(prob * 50))
            marker = " 🎙️" if prob >= VAD_THRESHOLD else ""
            print(f"\r  [{bar}] {prob:.3f}{marker}", end="", flush=True)
    except KeyboardInterrupt:
        print("\n\nAvbrutet.")
    finally:
        audio.stop_input_stream()
        audio.cleanup()

    print("✅ VAD-test klart!")


def test_stt():
    """Testa Speech-to-Text (Whisper API)."""
    from audio import AudioManager
    from vad import SileroVAD, SpeechEndDetector
    from stt import SpeechToText
    from cost_tracker import CostTracker
    from config import VAD_CHUNK_SIZE, VAD_THRESHOLD

    audio = AudioManager()
    vad = SileroVAD()
    cost = CostTracker()
    stt_engine = SpeechToText(cost)

    print("\n=== STT-test ===")
    print("Prata en mening på svenska...")

    # Vänta på tal
    audio.start_input_stream(VAD_CHUNK_SIZE)
    recorded = bytearray()
    speech_detector = SpeechEndDetector(vad)
    recording = False

    while True:
        chunk = audio.read_chunk()

        if not recording:
            prob = vad.process_chunk(chunk)
            if prob >= VAD_THRESHOLD:
                print("🎙️ Tal detekterat — spelar in...")
                recording = True
                recorded.extend(chunk)
                speech_detector = SpeechEndDetector(vad)
        else:
            recorded.extend(chunk)
            _, should_stop = speech_detector.process_chunk(chunk)
            if should_stop:
                break

    audio.stop_input_stream()
    duration = len(recorded) / (16000 * 2)
    print(f"Inspelat: {duration:.1f} sek")

    print("Transkriberar med Whisper...")
    text = stt_engine.transcribe(bytes(recorded))
    print(f"📝 Transkription: '{text}'")
    print(f"💰 Dagens kostnad: {cost.get_daily_total_sek():.4f} SEK")

    audio.cleanup()
    print("✅ STT-test klart!")
    return text


def test_llm():
    """Testa Claude LLM."""
    from llm import ConversationManager
    from cost_tracker import CostTracker

    cost = CostTracker()
    llm = ConversationManager(cost)

    print("\n=== LLM-test ===")
    test_messages = [
        "Hej Ugglebot! Vad kan du?",
        "Varför är himlen blå?",
    ]

    for msg in test_messages:
        print(f"\n👦 Barn: '{msg}'")
        response = llm.chat(msg)
        print(f"🦉 Ugglebot: '{response}'")

    print(f"\n💰 Dagens kostnad: {cost.get_daily_total_sek():.4f} SEK")
    print("✅ LLM-test klart!")


def test_tts():
    """Testa Text-to-Speech (ElevenLabs)."""
    from audio import AudioManager
    from tts import TextToSpeech
    from cost_tracker import CostTracker

    audio = AudioManager()
    cost = CostTracker()
    tts = TextToSpeech(cost, audio)

    print("\n=== TTS-test ===")
    test_text = "Hej! Jag är Ugglebot, en klok och vänlig uggla. Vad vill du prata om idag?"
    print(f"Genererar tal: '{test_text}'")

    tts.speak(test_text)

    print(f"💰 Dagens kostnad: {cost.get_daily_total_sek():.4f} SEK")
    audio.cleanup()
    print("✅ TTS-test klart!")


def test_voices():
    """Lista tillgängliga ElevenLabs-röster."""
    from audio import AudioManager
    from tts import TextToSpeech
    from cost_tracker import CostTracker

    audio = AudioManager()
    cost = CostTracker()
    tts = TextToSpeech(cost, audio)

    print("\n=== Tillgängliga ElevenLabs-röster ===")
    voices = tts.list_voices()

    if not voices:
        print("Inga röster hittades (kontrollera ELEVENLABS_API_KEY)")
        return

    for v in voices:
        print(f"  {v['voice_id']}  {v['name']:30s}  ({v['category']})")

    print(f"\nTotalt: {len(voices)} röster")
    print("Sätt ELEVENLABS_VOICE_ID i .env till önskad voice_id")
    audio.cleanup()


def test_pipeline():
    """Testa hela pipeline: inspelning → STT → LLM → TTS."""
    from audio import AudioManager
    from vad import SileroVAD, SpeechEndDetector
    from stt import SpeechToText
    from llm import ConversationManager
    from tts import TextToSpeech
    from cost_tracker import CostTracker
    from config import VAD_CHUNK_SIZE, VAD_THRESHOLD

    audio = AudioManager()
    vad = SileroVAD()
    cost = CostTracker()
    stt_engine = SpeechToText(cost)
    llm = ConversationManager(cost)
    tts = TextToSpeech(cost, audio)

    print("\n=== Full pipeline-test ===")
    print("Prata en mening på svenska...")

    # 1. Spela in med VAD
    audio.start_input_stream(VAD_CHUNK_SIZE)
    recorded = bytearray()
    recording = False
    speech_detector = SpeechEndDetector(vad)

    while True:
        chunk = audio.read_chunk()

        if not recording:
            prob = vad.process_chunk(chunk)
            if prob >= VAD_THRESHOLD:
                print("🎙️ Tal detekterat — spelar in...")
                recording = True
                recorded.extend(chunk)
                speech_detector = SpeechEndDetector(vad)
        else:
            recorded.extend(chunk)
            _, should_stop = speech_detector.process_chunk(chunk)
            if should_stop:
                break

    audio.stop_input_stream()
    duration = len(recorded) / (16000 * 2)
    print(f"⏹️ Inspelat: {duration:.1f} sek")

    # 2. Transkribera
    t0 = time.time()
    text = stt_engine.transcribe(bytes(recorded))
    stt_time = time.time() - t0
    print(f"📝 Transkription ({stt_time:.1f}s): '{text}'")

    if not text:
        print("❌ Tom transkription — avbryter")
        audio.cleanup()
        return

    # 3+4. Streama LLM → TTS (end-to-end)
    t0 = time.time()
    print("🦉 Streamar svar...")
    tts.speak_realtime(llm.chat_stream(text))
    stream_time = time.time() - t0
    print(f"🔊 Streaming klar ({stream_time:.1f}s)")

    # 5. Kostnad
    summary = cost.get_summary()
    print(f"\n💰 Kostnad för detta anrop:")
    print(f"   Daglig total: {summary['daily_sek']:.4f} SEK")
    print(f"   Budget kvar:  {summary['budget_remaining_sek']:.2f} SEK")
    print(f"   Månads total: {summary['monthly_sek']:.4f} SEK")
    print(f"\n⏱️ Latens: STT={stt_time:.1f}s, LLM+TTS(stream)={stream_time:.1f}s, "
          f"Total={stt_time + stream_time:.1f}s")

    audio.cleanup()
    print("✅ Pipeline-test klart!")


def test_cost():
    """Visa kostnadssummering."""
    from cost_tracker import CostTracker

    cost = CostTracker()
    summary = cost.get_summary()

    print("\n=== Kostnadssummering ===")
    print(f"  Idag:     {summary['daily_sek']:.4f} SEK ({summary['daily_usd']:.6f} USD)")
    print(f"  Månad:    {summary['monthly_sek']:.4f} SEK")
    print(f"  Budget:   {summary['budget_sek']:.2f} SEK/dag")
    print(f"  Kvar:     {summary['budget_remaining_sek']:.2f} SEK")


TESTS = {
    "audio": test_audio,
    "wake": test_wake,
    "vad": test_vad,
    "stt": test_stt,
    "llm": test_llm,
    "tts": test_tts,
    "voices": test_voices,
    "pipeline": test_pipeline,
    "cost": test_cost,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TESTS:
        print("Ugglebot — Testscript")
        print(f"\nAnvändning: python {sys.argv[0]} <test>")
        print(f"\nTillgängliga tester:")
        for name in TESTS:
            func = TESTS[name]
            doc = func.__doc__.strip().split("\n")[0] if func.__doc__ else ""
            print(f"  {name:12s} — {doc}")
        sys.exit(1)

    test_name = sys.argv[1]
    print(f"Kör test: {test_name}")
    TESTS[test_name]()


if __name__ == "__main__":
    main()
