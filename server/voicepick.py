# Try the local Duck Talk voices with your own text, timed the way a call times them: the first
# sentence is what you wait for before the reply starts. Missing Piper models are downloaded on first
# use. Saving a voice writes picked.env in DUCK_TTS_DIR, which the relay reads at start.
#   <venv>/bin/python voicepick.py
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import wave

from voicepcm import DIR, RATE, models, speak

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE"
EMOTIONS = ["neutral", "amused", "surprised", "angry", "sleepy", "whisper", "disgusted", "drunk"]
VOICES = (
    [("de", f"de_DE-thorsten_emotional-medium#{e}") for e in EMOTIONS]
    + [("de", f"de_DE-{n}") for n in ["thorsten-high", "thorsten-medium", "kerstin-low", "eva_k-x_low",
                                      "ramona-low", "karlsson-low", "pavoque-low"]]
    + [("en", f"kokoro:{n}") for n in ["af_heart", "af_bella", "af_nicole", "am_michael", "am_fenrir",
                                       "am_puck", "bf_emma", "bm_george", "bm_fable"]]
)
SAMPLE = {
    "de": "Alles klar, ich schaue mir das an. Im Repository gibt es drei offene Änderungen, soll ich sie committen?",
    "en": "Sure, let me take a look. There are three open changes in the repository, shall I commit them?",
}
PICKED = os.path.join(DIR, "picked.env")


def ensure(voice: str) -> None:
    if voice.startswith("kokoro:"):
        return
    model = voice.split("@")[0].split("#")[0]
    name, quality = model[len("de_DE-"):].rsplit("-", 1)
    for ext in [".onnx", ".onnx.json"]:
        path = os.path.join(DIR, model + ext)
        if os.path.exists(path):
            continue
        print(f"  downloading {model}{ext} …", flush=True)
        urllib.request.urlretrieve(f"{HF}/{name}/{quality}/{model}{ext}", path + ".part")
        os.replace(path + ".part", path)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?:])\s+", text.strip()) if s]


def play(voice: str, text: str) -> None:
    ensure(voice)
    key = "kokoro" if voice.startswith("kokoro:") else voice.split("@")[0].split("#")[0]
    cold = "" if key in models else " (first use: includes loading the model)"
    t0 = time.monotonic()
    parts, first = [], None
    for s in sentences(text):
        parts.append(speak(voice, s))
        first = first or time.monotonic() - t0
    total = time.monotonic() - t0
    pcm = b"".join(parts)
    print(f"  first sentence {first * 1000:.0f} ms, all {total * 1000:.0f} ms, {len(pcm) / 2 / RATE:.1f} s of speech{cold}")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        with wave.open(f, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE), w.writeframes(pcm)
    subprocess.run(["afplay", f.name])
    os.unlink(f.name)


def picked() -> dict[str, str]:
    if not os.path.exists(PICKED):
        return {}
    return dict(l.strip().split("=", 1) for l in open(PICKED) if "=" in l)


def save(lang: str, voice: str) -> None:
    p = picked()
    p[f"DUCK_TTS_{lang.upper()}"] = voice
    with open(PICKED, "w") as f:
        f.writelines(f"{k}={v}\n" for k, v in p.items())
    print(f"  saved {voice} for {lang}; the next call uses it")


def menu(speed: float, texts: dict[str, str]) -> None:
    p = picked()
    print()
    for i, (lang, v) in enumerate(VOICES, 1):
        mark = " *" if p.get(f"DUCK_TTS_{lang.upper()}", "").split("@")[0] == v else ""
        print(f"  {i:2} {lang} {v}{mark}")
    print(f"\n  speed {speed:.2f}   de: {texts['de']}\n             en: {texts['en']}")
    print("  <n> play · s <n> save for calls · + / - speed · de <text> · en <text> · q quit")


def main() -> None:
    speed, texts = 1.15, dict(SAMPLE)
    menu(speed, texts)
    while True:
        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        try:
            if cmd in ("q", "quit"):
                return
            if cmd in ("+", "-"):
                speed = round(max(0.5, min(2.0, speed + (0.05 if cmd == "+" else -0.05))), 2)
                print(f"  speed {speed:.2f}")
            elif cmd[:3] in ("de ", "en "):
                texts[cmd[:2]] = cmd[3:].strip()
            elif cmd.startswith("s ") and cmd[2:].strip().isdigit():
                lang, v = VOICES[int(cmd[2:]) - 1]
                save(lang, f"{v}@{speed:g}")
            elif cmd.isdigit() and 1 <= int(cmd) <= len(VOICES):
                lang, v = VOICES[int(cmd) - 1]
                play(f"{v}@{speed:g}", texts[lang])
            else:
                menu(speed, texts)
        except Exception as e:
            print(f"  failed: {e}")


if __name__ == "__main__":
    main()
