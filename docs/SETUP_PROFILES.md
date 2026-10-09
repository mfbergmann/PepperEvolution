# Setup profiles: what runs where

PepperEvolution mixes a cloud model (Claude) with a few local pieces. Only one of them needs a GPU machine, and everything works without it, only slower and less selective. This page explains the two ways to run it and what each local piece adds, so you can choose.

## The pieces

| Piece | What it does | Where it runs | Needed? |
|-------|--------------|---------------|---------|
| Bridge | Exposes Pepper's hardware (speech, motion, camera, microphone, sensors) as a small web API | On Pepper (Python 2.7, NAOqi 2.5) | yes |
| Host | Everything else: the conversation loop, the world model, reflexes, the web UI | Any computer on the robot's network (Python 3.12+) | yes |
| The mind: Claude | Understands, decides, talks, uses Pepper's tools | Anthropic's API (cloud) | yes (an API key) |
| Speech to text | Turns Pepper's microphone into text | The host's CPU (NVIDIA Nemotron via sherpa-onnx, about 0.6 GB); no GPU, no cloud | for voice; typed chat works without it |
| Situation judgements | Fast yes/no and multiple-choice judgements before the mind is called: is this meant for Pepper? which action should start now? is someone waving? | A GPU machine running Ollama 0.35.1 or later with the decision models `nimble` and `clef-flash` (about 20 GB of GPU memory for both) | optional (`DECIDE_URL`) |
| Local conversation model | Claude's role played by an open model on your GPU | Ollama on a GPU machine | optional, experimental (`OLLAMA_URL`); not recommended yet |
| Long-term memory | Remembers people who agreed to it (name, facts they asked it to keep) and a short record of each session; "forget me" at any time | The host (SQLite, no GPU) | optional (`MEMORY_DIR`), off by default; personal data, see Privacy |
| Scene notes | Every 20 s while someone is in view: who is where (Clef Flash) and a short note on the place (`qwen3.5:4b`) | The GPU machine (about 3.3 GB more) | optional (`SCENE_NOTES`), off by default |
| Sound direction | Where each voice came from (NAOqi's sound localisation), for "come to me" when the camera cannot see the speaker | On Pepper (bridge 0.6 or later) | optional (`SOUND_DIRECTION`), off by default; CPU cost on the robot not measured yet |

## Profile 1: cloud mind, laptop host (no GPU)

What you need: Pepper, a computer on the same network, an Anthropic API key. Speech recognition still runs locally, but on the computer's CPU; a recent laptop is enough.

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
AI_MODEL=claude-sonnet-5-5
AI_EFFORT=medium
STT_BACKEND=sherpa
STT_MODEL=/path/to/sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8   # see GETTING_STARTED.md
VOICE_INPUT=true
DECIDE_URL=            # empty: no situation judgements
```

What it is like:
- Pepper answers about 3 s after you finish a sentence (Claude's first words plus end-of-speech detection), as measured on the robot.
- Spoken commands move Pepper only when Claude decides to, so movement starts 3 to 4 s after you stop speaking.
- Pepper answers everything its microphone hears, including people talking to each other nearby (it cannot tell side talk from requests). Push-to-talk in the web UI avoids that.
- Greetings, head turns to newcomers and face tracking all work: they use NAOqi's own detection on the robot, not the GPU.
- No camera judgements: Pepper only looks when asked (a photo per question).

If you have no way to run speech recognition locally, typed chat in the web UI works with `STT_BACKEND=none`. A cloud speech-to-text backend is not built.

## Profile 2: cloud mind plus local situation judgements (the lab setup)

Add a GPU machine reachable from the host (ours: an RTX 5090 on the Creative AI Hub, over Tailscale) running Ollama 0.35.1 or later:

```bash
ollama pull nimble        # text decisions, 9.5 GB
ollama pull clef-flash    # text and image decisions, 10 GB
ollama pull qwen3.5:4b    # only for SCENE_NOTES: notes on the place, 3.3 GB
```

```bash
# .env, in addition to profile 1
DECIDE_URL=http://your-gpu-machine:11434
ADDRESSEE_GATE=true
ROUTER=true
VISION_STREAM=true
SCENE_NOTES=false         # true: a note on the place every 20 s (needs qwen3.5:4b)
```

Also available in either profile (all off by default, `docs/MEMORY.md`):

```bash
MEMORY_DIR=results/memory # long-term memory of people who agree to it (no GPU needed)
SOUND_DIRECTION=false     # true with bridge 0.6+: the direction each voice came from
```

What it adds (measured on our data, 2026-10-03; see `docs/ARCHITECTURE.md`, "Situation judgements"):

| Feature | What changes | Cost per decision |
|---------|--------------|-------------------|
| Addressee gate | Pepper answers only speech meant for it (21 of 22 right on a real session with two visitors; a name rule got 9 of 22) | about 0.05 s |
| Command router | Head moves, gestures and turns start about 0.15 s after the transcript instead of 3-4 s; Claude still does the words (26 of 28 commands, no wrong actions) | about 0.15 s |
| Camera judgements | While someone is in view, a small frame about once a second: waving? holding something up? facing Pepper? Pepper can react to a wave without being asked | about 0.15 s per frame |

If the GPU machine is down or slow, each judgement gives up after 0.8 s and Pepper behaves as in profile 1. Keep the models warm (the host pings them every 10 minutes; the first call after Ollama unloads a model takes about 30 s).

## Privacy

In both profiles Claude receives what Pepper hears (as text) and the photos it takes when asked. With profile 2, camera frames for the judgements and the text of every utterance (including side talk that is never answered) go to your GPU machine and stay there; nothing from the judgements goes to a cloud service. Session records (`SESSION_DIR`) keep voices, photos and transcripts on the host for review; keep them out of git (see `CLAUDE.md`, "Session records"). A printable notice for people near Pepper, in Pepper's own words, is in `docs/signs/recording-notice.pdf`.

### Long-term memory and privacy

With `MEMORY_DIR` set, Pepper can remember people from one day to the next:
- **What is kept:** a name and the facts the person asked it to keep, in `MEMORY_DIR/pepper.sqlite` on the host.
- **Consent first:** a person is stored only after they said yes to Pepper's question, or asked to be remembered. The code checks this against what Pepper actually heard, and never counts side talk. Their words are kept as the consent note.
- **Deleting:**
  - "forget me" deletes the person at once;
  - so does `python scripts/memory_admin.py forget <id>`;
  - otherwise everything expires after 90 days unless the person agrees again.
- **Separate from session records:** the store holds no recordings, and forgetting someone does not touch the session records.
- **Keep it private:** keep the store out of git and off shared drives. Tell people near Pepper (the printable notice in `docs/signs/` says it).

## Not built yet

- A cloud alternative for the situation judgements. TypeSafe AI's hosted Jev uses the same `/v1/systemone` API as Ollama's decision models, so `DECIDE_URL` could point at it, but the client does not send an API key yet and it has not been tested (or checked for image support).
- A cloud speech-to-text backend.
