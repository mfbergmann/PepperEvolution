#!/usr/bin/env python3
"""
Look after Pepper's long-term memory (docs/MEMORY.md): list who and what is stored, forget a person, delete a fact,
run the expiry sweep.

    python scripts/memory_admin.py list                 # people (with consent and expiry), counts
    python scripts/memory_admin.py show 3               # one person: consent note, facts, sessions
    python scripts/memory_admin.py facts                # facts about the lab
    python scripts/memory_admin.py forget 3             # delete person #3, their facts and their place in sessions
    python scripts/memory_admin.py delete-fact 12
    python scripts/memory_admin.py sweep                # delete what has expired (the host also does this at start)
    python scripts/memory_admin.py episodes

The store is ``MEMORY_DIR/pepper.sqlite`` (``--dir`` or the MEMORY_DIR environment variable / .env). It holds
personal data of people who agreed to be remembered: it stays on this machine and out of git.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv  # noqa: E402

from src.memory import MemoryStore  # noqa: E402


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default=os.getenv("MEMORY_DIR"), help="the memory folder (default: MEMORY_DIR)")
    parser.add_argument("command", choices=["list", "show", "facts", "forget", "delete-fact", "sweep", "episodes"])
    parser.add_argument("id", nargs="?", type=int)
    args = parser.parse_args(argv)
    if not args.dir:
        sys.exit("No memory folder: set MEMORY_DIR or pass --dir")
    path = os.path.join(args.dir, "pepper.sqlite")
    if not os.path.exists(path):
        sys.exit(f"No memory store at {path}")
    store = MemoryStore(path)
    try:
        if args.command == "list":
            for p in store.people():
                print(
                    f"#{p.id}  {p.name}  (agreed {p.consent_at[:10]}, last seen {(p.last_seen_at or '')[:10]}, "
                    f"expires {p.expires_at[:10]})"
                )
            print(store.counts())
        elif args.command in ("show", "forget", "delete-fact") and args.id is None:
            sys.exit(f"{args.command} needs an id")
        elif args.command == "show":
            person = store.person(args.id)
            if person is None:
                sys.exit(f"No person #{args.id}")
            print(f"#{person.id}  {person.name}\n  consent: {person.consent_note} at {person.consent_at}")
            for f in store.facts_about(person.id, limit=100):
                print(f"  fact #{f.id}: {f.text}")
            for e in store.episodes(person_id=person.id, limit=20):
                print(f"  session {e.session}: {e.summary}")
        elif args.command == "facts":
            for f in store.facts_about(None, limit=200):
                print(f"#{f.id}  {f.text}  ({f.created_at[:10]})")
        elif args.command == "forget":
            print("forgotten" if store.forget(args.id) else f"No person #{args.id}")
        elif args.command == "delete-fact":
            print("deleted" if store.delete_fact(args.id) else f"No fact #{args.id}")
        elif args.command == "sweep":
            print(store.sweep())
        elif args.command == "episodes":
            for e in store.episodes(limit=50):
                print(f"{e.session}  {e.summary}  people: {store.episode_people(e.id)}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
