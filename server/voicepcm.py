# Local neural voices for DUCK_BACKEND=local, the same protocol as saypcm: one request per stdin line,
# "<voice>\t<text>"; each answer on stdout is a 4-byte little-endian length and that many bytes of
# 24 kHz mono Int16 PCM (length 0 when nothing came out). Models stay loaded. A voice is either
#   kokoro:<name>[@<speed>]           Kokoro (kokoro-v1.0.onnx, voices-v1.0.bin), e.g. kokoro:af_heart
#   <piper model>[#<speaker>][@<speed>]   Piper, e.g. de_DE-thorsten_emotional-medium#amused@1.15
# with the models in DUCK_TTS_DIR.
#   <venv>/bin/python voicepcm.py
import os
import struct
import sys

import numpy as np

DIR = os.environ.get("DUCK_TTS_DIR", os.path.expanduser("~/.local/share/duckvoices"))
RATE = 24000
models: dict[str, object] = {}
out = sys.stdout.buffer


def piper(model: str, speaker: str, speed: float, text: str) -> tuple[np.ndarray, int]:
    from piper import PiperVoice, SynthesisConfig

    if model not in models:
        models[model] = PiperVoice.load(os.path.join(DIR, f"{model}.onnx"))
    voice = models[model]
    sid = voice.config.speaker_id_map.get(speaker) if speaker else None
    config = SynthesisConfig(speaker_id=sid, length_scale=1 / speed)
    parts = [c.audio_int16_array for c in voice.synthesize(text, syn_config=config)]
    return (np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)), voice.config.sample_rate


def kokoro(name: str, speed: float, text: str) -> tuple[np.ndarray, int]:
    from kokoro_onnx import Kokoro

    if "kokoro" not in models:
        models["kokoro"] = Kokoro(os.path.join(DIR, "kokoro-v1.0.onnx"), os.path.join(DIR, "voices-v1.0.bin"))
    lang = "en-gb" if name.startswith("b") else "en-us"
    audio, rate = models["kokoro"].create(text, voice=name, speed=speed, lang=lang)
    return np.asarray(audio, np.float32) * 32767, rate


def speak(voice: str, text: str) -> bytes:
    voice, _, speed = voice.partition("@")
    pace = float(speed) if speed else 1.0
    if voice.startswith("kokoro:"):
        pcm, rate = kokoro(voice[len("kokoro:"):], pace, text)
    else:
        model, _, speaker = voice.partition("#")
        pcm, rate = piper(model, speaker, pace, text)
    if not len(pcm):
        return b""
    if rate != RATE:
        n = int(len(pcm) * RATE / rate)
        pcm = np.interp(np.arange(n) * rate / RATE, np.arange(len(pcm)), pcm)
    return np.clip(pcm, -32768, 32767).astype("<i2").tobytes()


if __name__ == "__main__":
    for line in sys.stdin:
        name, _, text = line.rstrip("\n").partition("\t")
        try:
            pcm = speak(name, text) if text.strip() else b""
        except Exception as e:
            print(f"voicepcm: {name}: {e}", file=sys.stderr, flush=True)
            pcm = b""
        out.write(struct.pack("<I", len(pcm)) + pcm)
        out.flush()
