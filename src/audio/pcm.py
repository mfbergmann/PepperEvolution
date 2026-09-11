"""
Small helpers for 16-bit little-endian mono PCM (what the bridge streams and
what the browser push-to-talk sends). No numpy needed; the speech-to-text
backends convert to float arrays themselves.
"""

import array
import io
import math
import struct
import sys
import wave
from typing import Any

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2


def samples(pcm: bytes) -> "array.array[int]":
    """PCM bytes as an array of signed 16-bit samples (odd trailing byte dropped)."""
    out = array.array("h")
    usable = len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)
    out.frombytes(pcm[:usable])
    if sys.byteorder != "little":  # pragma: no cover - big-endian hosts
        out.byteswap()
    return out


def rms(pcm: bytes) -> float:
    """Root mean square level of the samples (0 for silence, up to 32767)."""
    data = samples(pcm)
    if not data:
        return 0.0
    return math.sqrt(sum(x * x for x in data) / len(data))


def duration(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    """Length of the audio in seconds."""
    return (len(pcm) // BYTES_PER_SAMPLE) / float(sample_rate)


def resample(pcm: bytes, from_rate: int, to_rate: int) -> bytes:
    """Linear-interpolation resampler; good enough for speech going into a recogniser."""
    if from_rate == to_rate or not pcm:
        return pcm
    src = samples(pcm)
    if len(src) < 2:
        return pcm
    ratio = from_rate / float(to_rate)
    out_len = int(len(src) / ratio)
    out = array.array("h")
    last = len(src) - 1
    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        value: float = src[last] if idx >= last else src[idx] * (1.0 - frac) + src[idx + 1] * frac
        out.append(int(max(-32768, min(32767, round(value)))))
    if sys.byteorder != "little":  # pragma: no cover
        out.byteswap()
    return out.tobytes()


def to_float32(pcm: bytes) -> Any:
    """PCM bytes as a numpy float32 array in [-1, 1] (numpy is imported lazily)."""
    import numpy as np

    usable = len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)
    return np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap PCM in a WAV container (for saving utterances)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(BYTES_PER_SAMPLE)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def tone(seconds: float, freq: float = 440.0, amplitude: int = 8000, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A sine tone as PCM (test signal that any energy detector counts as speech)."""
    n = int(seconds * sample_rate)
    values = [int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(n)]
    return struct.pack("<%dh" % n, *values)


def silence(seconds: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * int(seconds * sample_rate)
