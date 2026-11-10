# Where Pepper's data goes

*Started 2026-11-10. Keep this up to date whenever a new service sees any of Pepper's data, or a test starts or ends.* While the system is being built, work is spread over several machines and services. The long-term goal is one local machine for everything. This page records what goes where in the meantime, so the recording notice (`docs/signs/recording-notice.html`) and consent wording can be checked against it.

The code enforces this table: `src/orchestra/engines.py` lists each destination and what it is cleared for, refuses anything not listed (default-deny), and a test fails if a destination there has no row here (docs/ORCHESTRATOR.md). Data cleared for solo sessions only goes out only from sessions marked `solo` (`scripts/mark_session.py`).

"Personal data" here means recordings, transcripts, camera frames and photos, voice and face data, and the memory store.

| Destination | What goes there | Kept there? | Since | Decided by |
|---|---|---|---|---|
| **tripl-desktop** (lab, encrypted disk) | Everything: session records, recordings, frames, photos, the memory store (`results/memory/pepper.sqlite`, with the tester's voice and face) | Yes. The only full copy | 2026-09 | Design |
| **Pepper** (lab Wi-Fi) | Streams audio and frames to the desktop; speaks what the host sends | Bridge logs only, no recordings | 2026-09 | Design |
| **Anthropic API** (Claude Sonnet 5.5, the mind) | For each turn Pepper answers: the words heard (text), the "Around you" state line, recent conversation, photos Pepper takes, recalled memory facts | Under Anthropic's API terms | 2026-09 | Tester |
| **Alien3** `:11435` (lab GPU machine, private Ollama) | Words heard (addressee gate, router), camera frames (judgements, scene notes). Cleared too, not in use: `:11434` as the mind (`OLLAMA_URL`), which would get what the Anthropic row lists | No: processed and dropped. **Not for storage** (tester, 2026-11-05) | 2026-10-03 | Tester |
| **Alien3** `:11436` (`scripts/stt_server.py`) | Audio of each utterance, for the second transcription pass and the voice reading | No: processed and dropped | 2026-11-02 (voice readings 2026-11-05) | Tester |
| **Backblaze B2**, `tripl-lab/system-backup`, Toronto (CA East) | Everything on the desktop, recordings and memory store included, **encrypted on the desktop first**; Backblaze cannot read it | Yes: nightly snapshots, deletions final after 30 days | 2026-11-08 | Tester |
| **SHARCNET Brine** (`brine.sharcnet.ca`, DeepSeek-V4-Flash; testing) | Replayed turns from sessions where **the tester was alone**: words heard, Pepper's recorded replies, "Around you" lines. Nothing from sessions with anyone else, no audio, no images, no voice or face data | SHARCNET logs token counts and response times only (their reply of 2026-11-02) | 2026-11-10 | Tester, against Brine's "no sensitive data" term, for their own data |
| **Anthropic** (Claude Code, the development assistant) | Whatever is opened while developing: session records, transcripts, logs; a few contact sheets of the tester's face during the face work (2026-11-05), and two that also held distant bystanders (deleted) | Under Anthropic's terms | 2026-09 | Tester |
| **GitHub** (public repo, wiki), **Basecamp** | Code, docs, aggregate results only. Never transcripts, recordings, images or names of visitors | Yes | 2026-09 | Design |
| **Hugging Face** | Nothing goes up: model downloads only | n/a | 2026-11 | n/a |

## Tests in progress

- **DeepSeek on Brine as the mind (from 2026-11-10).** `scripts/compare_minds.py` replays turns from sessions marked `solo` (it refuses any other) to Sonnet and DeepSeek on identical context; results in the git-ignored `results/system-one/minds/`. The live host still uses Sonnet. To run the host on DeepSeek: `AI_MODEL=DeepSeek-V4-Flash-0731`, `OLLAMA_URL=https://brine.sharcnet.ca`, `AI_API_KEY_FILE=~/.config/brine/key`. Then every answered turn's words would go to Brine instead of Anthropic, and so would photos and names, which Brine is not cleared for: the host refuses to start with it as the mind until that is decided and the registry changed.

## What the recording notice does not yet say (2026-11-10)

The tester is rewriting the notice. Against this table, the printed one says recordings are "kept on our lab's own computers", and does not mention: the encrypted off-site copy at Backblaze; audio and frames processed on Alien3; voices and faces compared with enrolled people as they are heard and seen; words sent to Brine while it is being tested; deletion reaching backups within 30 days.
