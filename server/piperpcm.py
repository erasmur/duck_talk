# Piper neural voices for DUCK_BACKEND=local, the same protocol as saypcm: one request per stdin
# line, "<voice>\t<text>", where voice is a model name in DUCK_PIPER_DIR (e.g. de_DE-thorsten-high);
# each answer on stdout is a 4-byte little-endian length and that many bytes of 24 kHz mono Int16
# PCM (length 0 when nothing came out). Models stay loaded.
#   <venv>/bin/python piperpcm.py
import os
import struct
import sys

import numpy as np
from piper import PiperVoice, SynthesisConfig

DIR = os.environ.get("DUCK_PIPER_DIR", os.path.expanduser("~/.local/share/piper"))
SPEED = float(os.environ.get("DUCK_PIPER_SPEED", "1.0"))
RATE = 24000
voices: dict[str, PiperVoice] = {}
out = sys.stdout.buffer


def load(name: str) -> PiperVoice:
    if name not in voices:
        voices[name] = PiperVoice.load(os.path.join(DIR, f"{name}.onnx"))
    return voices[name]


def speak(name: str, text: str) -> bytes:
    voice = load(name)
    config = SynthesisConfig(length_scale=1 / SPEED)
    parts = [c.audio_int16_array for c in voice.synthesize(text, syn_config=config)]
    if not parts:
        return b""
    pcm = np.concatenate(parts).astype(np.float32)
    rate = voice.config.sample_rate
    if rate != RATE:
        n = int(len(pcm) * RATE / rate)
        pcm = np.interp(np.arange(n) * rate / RATE, np.arange(len(pcm)), pcm)
    return np.clip(pcm, -32768, 32767).astype("<i2").tobytes()


for line in sys.stdin:
    name, _, text = line.rstrip("\n").partition("\t")
    try:
        pcm = speak(name, text) if text.strip() else b""
    except Exception as e:
        print(f"piperpcm: {name}: {e}", file=sys.stderr, flush=True)
        pcm = b""
    out.write(struct.pack("<I", len(pcm)) + pcm)
    out.flush()
