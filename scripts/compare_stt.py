#!/usr/bin/env python3
"""
Compare speech-to-text backends on utterances recorded from Pepper.

Recordings come from running the host with ``VOICE_RECORD_DIR=recordings``: each
utterance is ``utterance_*.wav`` plus the live recogniser's ``utterance_*.hyp.txt``.
To score accuracy, copy a ``.hyp.txt`` to ``.ref.txt`` next to it and correct it to
what was actually said; utterances without a reference are transcribed and timed
but not scored.

    python scripts/compare_stt.py recordings \\
        --sherpa ~/.local/share/pepper-models/sherpa-onnx-streaming-zipformer-en-2023-06-26 \\
        --sherpa ~/.local/share/pepper-models/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8 \\
        --whisper small

Prints one line per utterance and backend, then a summary: word error rate over the
scored utterances, and recognition time per second of audio.
"""

import argparse
import asyncio
import os
import re
import sys
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audio import make_transcriber, resample  # noqa: E402
from src.audio.pcm import duration  # noqa: E402


def words(text: str) -> List[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split()


def word_errors(ref: str, hyp: str) -> Tuple[int, int]:
    """(edit distance in words, reference length)."""
    r, h = words(ref), words(hyp)
    row = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, row[0] = row[0], i
        for j, hw in enumerate(h, 1):
            cur = min(row[j] + 1, row[j - 1] + 1, prev + (rw != hw))
            prev, row[j] = row[j], cur
    return row[len(h)], len(r)


def load(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        pcm, rate = w.readframes(w.getnframes()), w.getframerate()
    return resample(pcm, rate, 16000) if rate != 16000 else pcm


def reference(path: Path) -> Optional[str]:
    ref = path.with_suffix("").with_suffix(".ref.txt")
    return ref.read_text(encoding="utf-8").strip() if ref.exists() else None


async def run(args) -> int:
    clips = sorted(Path(args.recordings).glob("utterance_*.wav"))
    if not clips:
        print(f"no utterance_*.wav in {args.recordings} (record with VOICE_RECORD_DIR)")
        return 1
    backends: List[Tuple[str, object]] = []
    for model_dir in args.sherpa or []:
        backends.append(
            (f"sherpa:{Path(os.path.expanduser(model_dir)).name[:40]}", make_transcriber("sherpa", model=model_dir))
        )
    for model in args.whisper or []:
        backends.append((f"whisper:{model}", make_transcriber("whisper", model=model, language=args.language)))
    if not backends:
        print("give at least one --sherpa MODEL_DIR or --whisper MODEL")
        return 2
    for _, t in backends:
        await t.start()

    totals: Dict[str, Dict[str, float]] = {
        name: {"err": 0, "ref": 0, "secs": 0.0, "audio": 0.0} for name, _ in backends
    }
    for clip in clips:
        pcm, ref = load(clip), reference(clip)
        print(f"\n{clip.name} ({duration(pcm):.1f}s)" + (f"  ref: {ref}" if ref else "  (no .ref.txt)"))
        for name, t in backends:
            result = await t.transcribe(pcm)
            tot = totals[name]
            tot["secs"] += result.latency
            tot["audio"] += duration(pcm)
            score = ""
            if ref is not None:
                err, n = word_errors(ref, result.text)
                tot["err"] += err
                tot["ref"] += n
                score = f"  [{err}/{n} word errors]"
            print(f"  {name:<52} {result.latency:5.2f}s  {result.text}{score}")

    print("\nSummary")
    for name, tot in totals.items():
        wer = f"{100 * tot['err'] / tot['ref']:.1f}% WER" if tot["ref"] else "no references"
        speed = tot["secs"] / tot["audio"] if tot["audio"] else 0.0
        print(f"  {name:<52} {wer:>16}   {speed:.3f} s per audio second")
    for _, t in backends:
        await t.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", help="folder with utterance_*.wav (VOICE_RECORD_DIR)")
    parser.add_argument("--sherpa", action="append", help="sherpa-onnx streaming model directory (repeatable)")
    parser.add_argument("--whisper", action="append", help="faster-whisper model name, e.g. base, small (repeatable)")
    parser.add_argument("--language", default="en")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
