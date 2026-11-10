# Orchestrator: which engine handles what, on the record

*Started 2026-11-10. Issue #37. Related: [ARCHITECTURE.md](ARCHITECTURE.md) (layers, one voice), [DATA_FLOWS.md](DATA_FLOWS.md) (where data goes).*

Each turn Pepper hears already passes through several engines:
- exact-phrase intents
- the addressee gate and command router (Nimble on Alien3)
- voice and face readings
- the mind (Claude Sonnet)

Today these choices are made by branches spread through `AIManager.process_user_input`. Their results are only partly recorded: the turn record says what the gate and router judged. It does not say:
- which mind answered, at what cost
- what the mind was actually shown
- how the turn went for the person

The DeepSeek comparison (2026-11-10) had to rebuild the mind's context from the record. The one human judgement we have (25 blind marks) is keyed by timestamp.

The orchestrator makes the routing explicit, puts every decision and its outcome on the record, and enforces in code which destination may receive which data. Its purpose is to let us improve routing from evidence, and later train a local model, without losing what makes Pepper one coherent someone in the room.

## Goals, in order

1. **No change in behaviour while it is built.** The orchestrator first records, then takes over the branching. Every step is checked against recorded sessions.
2. **Policy in code, default-deny.** A destination that is not registered, or a kind of data a destination is not cleared for, is refused before anything is sent. `DATA_FLOWS.md` and the registry must agree; a test checks it.
3. **A ledger good enough to learn from.** For every turn there is a stable id, every engine consulted, what the mind saw, cost and timing, and outcomes added afterwards. Human marks are keyed by turn id.
4. **One voice.** A conversation has one mind. Engines may judge, read and prepare; only the mind speaks and calls action tools (ARCHITECTURE.md, rule 3).
5. **Toward one local machine.** The ledger and the marks are the training and evaluation data for a local mind, trained on Alliance GPUs and run in the lab.

## What we learned that shapes this

- **Fast judgements must fail open.** Alien3 being down must never make Pepper mute (`DecisionClient`, 2026-10-08). Every engine call has a timeout and a fallback, and the record says which happened.
- **Stopping never waits.** `stop` and `be quiet` are matched before the turn lock, the judgements and the model. The orchestrator adds no await to that path. Before acting it only makes a synchronous note, guarded so it can never raise; the turn line is written after acting.
- **A cheaper mind loses on actions.** DeepSeek was 4× faster to its first word, but claimed actions without making them (18 against 0). It did nothing where the live run acted (17 against 4). Over a third of photo turns went to it without looking. Blind marks: Sonnet 12, DeepSeek 7, equal 6. Speed alone does not decide routing, and action turns are where a weaker mind costs most.
- **History is provider-specific.** Claude's thinking blocks must be replayed in the tool loop, and each provider shapes tool results differently. Switching minds inside a tool loop is impossible. Switching between turns changes the voice. So the mind is chosen per conversation, not per sentence.
- **Opt-out lists leak.** `compare_minds.py` refused two named sessions. A new session with a visitor would have been replayable by default. Presence must be declared, not assumed.
- **Marks are scarce.** 25 blind marks cannot train a router. They can only check one. Outcomes the system observes for itself have to carry most of the signal, with marks as the check.

## Shape

```
heard ─► intents (rules, this host) ──► act now ──────────────────────────────┐
     └─► judgements in parallel:                                              │
           addressee gate (Nimble, Alien3)                                    │
           command router (Nimble, Alien3)                                    ▼
           voice reading (Alien3 :11436)        ┌─► decide_turn() ─► Route ─► execute (AIManager)
           world model, faces (this host) ──────┘        pure              │
                                                                           ▼
                                        ledger: turns.jsonl (+ route, mind) │ outcomes.jsonl (later)
```

- **`src/orchestra/engines.py`: the registry.** Each engine has:
  - a name
  - the destination (a row of `DATA_FLOWS.md`)
  - where it runs: this host, the lab, or hosted in Canada or the US
  - the data classes it accepts, and its role (rule, judge, reader, mind)

  The data classes are `text`, `image`, `audio`, `identity` (names, voice and face matches), and the session's presence (`solo`, `unknown`, `others`). `allowed(engine, classes)` is the only gate. Unknown engines accept nothing.
- **`src/orchestra/manifest.py`: session presence.** Each session folder has a `session.json` with `presence`. It is `unknown` at start, and the tester marks it `solo` or `others` after review (`scripts/mark_session.py`). Anything that sends session data to a destination cleared for solo sessions only, such as Brine, refuses unless the manifest says `solo`.
- **`src/orchestra/ledger.py`: ids, mind calls, outcomes.**
  - Turn ids are `<session>#<n>`.
  - Per model call the ledger records: the engine, model, effort, round, time to the first text, duration, usage, stop reason, error and phantom retries.
  - Per turn it records:
    - the state block as sent
    - the user text as sent (when it differs, e.g. `[Already started: …]`)
    - hashes of the system prompt and the tool list
    - the turn ids in the history
    - the number of photos in context
- **Outcomes** go to `outcomes.jsonl`, keyed by turn id and appended when they become known:
  - `stopped_after`: stop or quiet within seconds of Pepper's answer
  - `repeated`: the same words again soon after an answer
  - `repeated_after_ignored`: the same words again after the gate ignored them, a likely missed address
  - `error`: the mind call failed
  - `tool_failed`
- **`src/orchestra/route.py`: the decision as pure functions.**
  - `addressee_verdict()` takes the gate's probability and the situation flags and returns the verdict, plus what decided it.
  - `accept_route()` applies the router's threshold and the body-free check.
  - `decide_turn()` returns the path: `not_addressed`, `routed_intent`, `look_then_mind`, `routed_then_mind` or `mind`.

  `AIManager` gathers the judgements, calls these, and executes the route. The functions take plain values, so tests and `scripts/replay_decisions.py` can run them on recorded turns.

## Phases

**1a. The ledger (additive).** *Built 2026-11-10 (44c44bd); to be checked on the robot.*
- Turn ids, mind-call records, outcomes, the registry and manifests.
- `compare_minds.py` uses manifests instead of its exclusion list.
- Blind keys carry turn ids, and `scripts/collect_marks.py` keeps marks in `results/marks/marks.jsonl`.
- The live host refuses a mind whose destination is not cleared for a session of unknown presence. With today's settings that changes nothing: Anthropic is cleared, and Brine is not.

**1b. The branching as pure functions.** *Built 2026-11-10. On all 21 recorded sessions: 276 of 276 gate verdicts and 275 of 277 paths agree. The other two are records made before later fixes: a routed "look at me" that still ended the turn, and a phrase the intent list no longer matches. No recorded session had SECOND_VOICE on, so the second-voice rule and the stop override rest on unit tests until the two-person session.*
- Move the decision out of `process_user_input` into `route.py`, with no change in behaviour.
- `scripts/replay_decisions.py` recomputes every recorded gate verdict and path and must agree with what was recorded.
- This is a separate commit from 1a, so it can be reverted alone if the next robot session finds anything.

**2. Evaluation, then routing that learns.**
- An evaluation set drawn from the ledger, fixed and held out:
  - turns with outcomes
  - turns with marks
  - action turns, weighted
- A recurring blind sheet from new solo sessions, so marks accumulate.
- Candidate engines are replayed offline from the ledger, never run live in parallel, because a live turn cannot know that the room is solo.

**Promotion bar** (written before any learner):

A route moves from replay to live only when, on the held-out set, all four hold:
- it does no worse than the current route on actions not made, actions claimed without the tool, and unasked body moves
- it is not worse in blind marks (sign test over at least 40 marked turns)
- it improves the thing it was chosen for (time to the first word, or cost)
- its destination is cleared for the data

The first learned piece is likely the addressee gate's thresholds, which have the most outcomes: replies and repeats after being ignored.

**3. A local mind (Alliance).**
- Distil Sonnet's replies and the marked preferences into an open model. The candidate is Qwen3.8-27B, which with thinking off was fast on Brine and reads images.
- Train on Alliance H100s; run it on this desktop or Alien3.

Two decisions come before any data moves; neither blocks phases 1 and 2:
- **Anthropic's terms.** Check what they allow when Claude's outputs are used to train another model.
- **Consent.** Alliance becomes a new row in `DATA_FLOWS.md`, decided by the tester, with consent wording to match. Only solo-session text, never voice, face, audio or images of anyone.

## What stays out

- Nothing in the orchestrator speaks or moves the robot. Execution stays in `AIManager` and the bridge's bounds stay in force.
- No mind switching within a conversation in phases 1–2. A per-conversation choice comes only with a promoted route.
- No live shadow runs to hosted services.
- The public repo, this document and issues hold aggregates only. Ledgers, outcomes, marks and replays stay in git-ignored `results/` on this machine (and its encrypted backup).
