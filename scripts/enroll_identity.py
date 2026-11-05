#!/usr/bin/env python3
"""
Enrol someone's voice in Pepper's long-term memory, from recorded clips of them speaking alone (docs/MEMORY.md).

    python scripts/enroll_identity.py --person NAME --consent "their words, when and how" \\
        results/sessions/2026-10-08_* results/sessions/2026-11-05_160237@160745
    python scripts/enroll_identity.py --person NAME --consent "..." --written-consent results/sessions/...

Each source is a session folder (its audio/*.wav), a folder of clips, one .wav, or a folder followed by @HHMMSS for
only the clips from that time on. Clips must hold this person alone: leave out any session where someone else speaks.
Clips under 1.5 s are skipped, and clips far from the rest (someone else, a cough) are dropped from the voiceprint.

The person must already be stored (they agreed to be remembered) and is looked up by name, so a typo cannot create
anyone. Only one vector per person is kept: the centre of their clips' embeddings. Audio is read and not copied.
With --written-consent they stay until forgotten; otherwise the 90-day rule starts again from today. Run it again to
replace the voiceprint; ``scripts/memory_admin.py forget`` deletes it with the person.
"""

import argparse
import glob
import os
import re
import sys
import wave
from typing import List

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.identity import robust_centroid, to_blob  # noqa: E402
from src.identity.voice import (
    DEFAULT_MODEL,
    KIND,
    MIN_CLIP_SECONDS,
    SAMPLE_RATE,
    SpeakerModel,
    samples_from,
)  # noqa: E402
from src.memory import MemoryStore  # noqa: E402

CLIP_TIME = re.compile(r"_(\d{6})_\d{3}_")  # utterance_YYYYMMDD_HHMMSS_mmm_n.wav


def clips_in(source: str) -> List[str]:
    """The .wav files a source names, in recording order."""
    folder, _, since = source.partition("@")
    if folder.endswith(".wav"):
        return [folder]
    audio = os.path.join(folder, "audio")
    paths = glob.glob(os.path.join(audio if os.path.isdir(audio) else folder, "*.wav"))
    if since:
        paths = [p for p in paths if (m := CLIP_TIME.search(os.path.basename(p))) and m.group(1) >= since]
    return sorted(paths)


def read_clip(path: str):
    with wave.open(path, "rb") as fh:
        if fh.getframerate() != SAMPLE_RATE or fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise ValueError(f"{path}: not 16 kHz 16-bit mono")
        return samples_from(fh.readframes(fh.getnframes()))


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sources", nargs="+", help="session folders, clip folders or clips; folder@HHMMSS from a time")
    parser.add_argument("--person", required=True, help="their name, as stored (scripts/memory_admin.py list)")
    parser.add_argument("--consent", required=True, help="a note of their agreement to having their voice enrolled")
    parser.add_argument("--written-consent", action="store_true", help="they agreed in writing: keep until forgotten")
    parser.add_argument("--dir", default=os.getenv("MEMORY_DIR"), help="the memory folder (default: MEMORY_DIR)")
    parser.add_argument("--model", default=os.getenv("VOICE_ID_MODEL") or DEFAULT_MODEL, help="speaker model file")
    parser.add_argument("--dry-run", action="store_true", help="show what would be enrolled and store nothing")
    args = parser.parse_args(argv)
    if not args.dir:
        sys.exit("No memory folder: set MEMORY_DIR or pass --dir")
    if not args.consent.strip():
        sys.exit("Nobody is enrolled without a note of their consent")
    store = MemoryStore.open(args.dir)
    try:
        people = store.find_people(args.person)
        if len(people) != 1:
            sys.exit(f"{len(people)} people stored under that name: enrol exactly one person who agreed to be stored")
        person = people[0]
        paths = [path for source in args.sources for path in clips_in(source)]
        model = SpeakerModel(args.model, threads=4)
        names, vectors = [], []
        for path in paths:
            samples = read_clip(path)
            if len(samples) >= MIN_CLIP_SECONDS * SAMPLE_RATE:
                names.append(path)
                vectors.append(model.embed(samples))
        if not vectors:
            sys.exit("No usable clips")
        centroid, kept = robust_centroid(vectors)
        scores = [float(vectors[i] @ centroid) for i in kept]
        print(f"{len(paths)} clips, {len(paths) - len(names)} too short, {len(names) - len(kept)} dropped as outliers")
        for i in sorted(set(range(len(names))) - set(kept)):
            print(f"  dropped {os.path.basename(names[i])}: {float(vectors[i] @ centroid):.2f}")
        print(f"voiceprint from {len(kept)} clips; their scores against it {min(scores):.2f} to {max(scores):.2f}")
        if args.dry_run:
            print("dry run: nothing stored")
            return
        if args.written_consent:
            store.keep_written(person.id, args.consent)
        else:
            store.renew(person.id, args.consent)
        store.add_identity(person.id, KIND, to_blob(centroid), model.name, len(kept), args.consent)
        print(
            f"enrolled person #{person.id} in {store.path} ({'kept until forgotten' if args.written_consent else '90 days'})"
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
