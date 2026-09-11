# Research notes: robot foundation models and Pepper (September 2026)

Question asked: is an off-board language model driving Pepper through a bridge still the right approach, given the wave of robot foundation models (NVIDIA, ByteDance, Google, Physical Intelligence and others)? Is there anything to learn from them?

Method: four parallel literature and web sweeps in September 2026 (NVIDIA stack; ByteDance and other Chinese groups; Google, Physical Intelligence and the rest; and Pepper/NAO-specific work), with sources cited below. Release details for 2026 come from vendor pages and papers where available; a few rest on secondary coverage and are marked as such.

## Verdict

1. **The bridge architecture is the state of the art for Pepper.** Every serious 2024 to 2026 Pepper or NAO language-model system uses the same two-process pattern: a thin Python 2.7 layer on the robot exposing microphone, camera, speech and actions, and a Python 3 host running the model and tools. The differences between them are latency engineering and the reactive layer, not the model class.
2. **Robot foundation models (VLAs) do not apply to Pepper.** They emit continuous joint or end-effector targets for manipulators at 10 to 200 Hz, need per-robot teleoperation data and a GPU in the loop, and assume hardware Pepper does not have. No paper or repository has put one on Pepper or NAO.
3. **The "embodied reasoning" layer of those stacks is what we already built**, and the concrete ideas worth borrowing are a richer reactive layer, point-based vision grounding, voice input, and memory. These are on the [roadmap](ROADMAP.md).

## Robot foundation models: what they are

| Family | Latest (as of Sept 2026) | Output | Access | Why it does not fit Pepper |
|---|---|---|---|---|
| NVIDIA Isaac GR00T N1.x | N1.7 generally available July 2026; N2 announced for late 2026 | 40-step chunks of end-effector or joint targets; humanoid whole-body via a lower controller | Open weights (NVIDIA Open Model License), CUDA GPU with 16 GB+ | Manipulator action space, needs a Pepper teleop dataset and a GPU in a fast loop |
| NVIDIA Cosmos Reason 2 / Cosmos 3 | Reason 2 Dec 2025; Cosmos 3 June 2026 | Reason 2: plans, points, boxes as text; Cosmos 3: text, video, actions | Open weights; Cosmos 3 Nano is workstation class | Reason 2 is the one piece that is conceptually usable (a reasoning VLM); Claude already fills that role |
| NVIDIA DreamZero | Feb 2026 paper | Video plus actions jointly | Open code and checkpoints; two GPUs for inference | World-action model for manipulation |
| ByteDance Seed GR-3 / GR-RL | July and Dec 2025 | 19-DoF arm and base action chunks | Closed | Closed, manipulation-only |
| AgiBot GO-1 / GO-2 | GO-2 April 2026 | Action-intent plan plus 30 Hz joint control | GO-1 open non-commercial; GO-2 closed | Manipulation, AgiBot hardware |
| Unitree, X Square, Xiaomi, Galbot, Tsinghua RDT-2, Shanghai AI Lab EO-1 | 2025 to 2026 | Joint or end-effector chunks; RDT-2 claims zero-shot on unseen arms with a specific gripper and camera rig | Mostly open, some non-commercial | Same reasons; some need a specific gripper rig |
| Google Gemini Robotics 2 / On-Device 2 | July 2026 | Whole-body and bi-arm motor commands | Partner or trusted-tester only | Not accessible; manipulation |
| Physical Intelligence pi0 to pi0.7 | pi0.7 April 2026 | 50-step action chunks | pi0/pi0.5 open; later closed | Manipulation, per-robot data |
| Figure Helix 02, Tesla Optimus, 1X Redwood, Skild S1 | 2025 to 2026 | Whole-body control | Closed | Not accessible |
| Hugging Face LeRobot + SmolVLA | LeRobot 0.6 July 2026 | Joint chunks | Open | The dataset and training toolchain the open ecosystem standardises on; only useful if we ever collect Pepper demonstrations |

The recurring design in all of them: a vision-language backbone (the "slow" system) plus a small action expert (the "fast" system) that turns its output into a chunk of motor commands. New robots are added by recording teleoperated demonstrations, from about a hundred episodes to hundreds of hours, and fine-tuning the action expert.

### Why they do not transfer to Pepper

- **Action space.** Pepper's arms are five-degree-of-freedom gesture arms with a one-position hand, no tactile sensing, no wrist cameras and a payload of a few hundred grams. There is no manipulation task for a learned policy to accomplish. Its base, head, speech and tablet are already exposed as good high-level primitives by NAOqi.
- **Data.** No Pepper demonstration dataset exists, and there is no teleoperation rig that records one; the two 2025 to 2026 Pepper VR teleoperation papers are Wizard-of-Oz tools and release no data.
- **Compute and latency.** Every model needs an NVIDIA GPU with 16 GB or more. Pepper's Intel Atom cannot host anything, so inference would be off-board with camera frames and joint targets crossing Wi-Fi each cycle; the fast closed loops these models assume are not available.
- **Access.** ByteDance's line, Google's motor-control models and the humanoid companies' stacks are closed.

Where a group wanted local perception models, they bolted a Jetson and a RealSense camera onto Pepper (2024) or rebuilt NAO's head (2025). That is a hardware project, not a software one.

## The layer that does transfer: embodied reasoning

- **Gemini Robotics-ER 2** (July 2026) is the only robotics model with a public API. It takes images or video plus an instruction and returns points, boxes, trajectories, plans, progress and success judgements, and calls user-defined robot functions. Structurally, that is this project with Claude in the model slot.
- **Anthropic's "Claude Plays Robotics"** (July 2026) evaluated Claude on a quadruped, a humanoid and an arm at four abstraction levels. It mostly failed at direct joint control, is about a hundred times too slow for real-time loops, and did well supervising pretrained skills through documented tools. The **Model Hardware Standard** research preview (August 2026) describes a robot as discoverable, safety-bounded tools, which is the bridge pattern.
- Open Chinese "embodied brain" models (RoboBrain 2.0 / RoboOS, Pelican-VL, Embodied-R1.5) do the same job: text plans, pixel-coordinate pointing and function calls to per-robot skill servers, with no motor output.

Design lessons applied or planned:

| Lesson | Source | Status in PepperEvolution |
|---|---|---|
| Slow planner over fast skills and reflexes | All VLA stacks; Claude Plays Robotics | Done: Claude plans; NAOqi executes; collision protection and the sonar guard live on the bridge |
| Reactive layer masks model latency | Pepper field studies; MistyPilot fast/slow framework | Roadmap M1 |
| Points and success judgements rather than motor commands | Gemini Robotics-ER; Cosmos Reason 2 | Roadmap M3 (`look_at`, verify-after-act) |
| Event-driven vision, never streamed video to the model | Measured +0.4 to +1.6 s per image in a 2026 Pepper study | Done: photos on demand as tool results |
| Gestures as discrete intents mapped to an animation library | Gesture-heads paper; RLHF gesture paper found generated motion "stiff and unnatural" | Done: animation tags and `play_animation` with the installed list |
| Action chunking: batch several primitives per model turn | Every VLA | Done: multiple tool calls per round, speech streamed by sentence |
| Explicit memory and relationship state | ARIS (2026), agent-architecture evaluation | Roadmap M4 |
| Robot described as safety-bounded tools | Model Hardware Standard | Done in spirit; publish a descriptor when the spec is open |

## Autonomous OS (autonomous.ai, June 2026)

[autonomous-ai/autonomous-os](https://github.com/autonomous-ai/autonomous-os) is the open-source stack behind the Lamp and Intern desk robots: a Go daemon plus a Python 3.12 hardware layer that run on the robot's own arm64 Linux board, a robot declared in `ROBOT.md` / `SOUL.md` / `SAFETY.md`, swappable agent runtimes (Claude Code, Codex, OpenClaw and others), skills as `SKILL.md` files, a realtime speech-to-speech voice layer and a deterministic safety gate below the model. Reachy Mini was ported in two weeks by wrapping Pollen's SDK. Assessed 10 September 2026.

**Not a platform for Pepper.** It must run on the robot (arm64, systemd, Python 3.12, 4 GB free); Pepper's head is an x86 Atom on NAOqi 2.5 with Python 2.7. Its motion contract is joint-space for a 5 or 6 degree-of-freedom articulated head, wheels are explicitly unsolved, and there is no notion of an animation library, animated speech or a tablet. Skills act by writing `[HW:/path:{json}]` markers into the model's text that the daemon regex-parses, the mechanism this project replaced with native tool calling. Voice, face and mood models default to their hosted gateway, and the hardware layer is GPL-3.0.

**Borrowed from it** (see the roadmap):

- The realtime voice design: a fast speech layer handles small talk and *delegates* turns that need tools to the main agent, with neural voice-activity detection, barge-in and echo handling documented in detail (`docs/realtime-voice.md`, `hal/drivers/voice/`). Their finding that energy-based VAD misses about half of real speech frames shaped our voice-input design.
- `lifelike` and `presence` as routeless background loops (breathing, idle micro-movements, people tracking) rather than model-driven behaviour, which is our reactive layer.
- `SAFETY.md` as machine-read numbers enforced below the model, with an explicit ledger of what is and is not gated yet.
- A local `intent` table that answers fixed commands ("stop", "be quiet") in about 50 ms with no model call.

## What others have done on Pepper and NAO

- **Low-latency multimodal Pepper (FHNW, HRI 2026)**: the most complete open Pepper system, on NAOqi 2.9 with a Kotlin tablet app streaming microphone audio to speech-to-speech models (OpenAI Realtime, Gemini Live) that drive the robot through function calling. Its ideas transfer; its code does not (2.9 hides raw audio and camera access that 2.5 gives us).
- **Nursing-home study (RO-MAN 2025)** with GPT-4o Realtime: conversations rated pleasant and personal; failures were turn-taking, dialect recognition and repetitiveness.
- **Vision-augmented dialogue (July 2026)**: one camera frame per utterance sent to a VLM; the robot could refer to what it saw; image overhead measured at 0.4 s (Pixtral) to 1.6 s (GPT-4o mini) per call.
- **Cascaded pipelines** (Scientific Reports 2025, Frontiers 2025, a 2026 hospital-receptionist project using Claude with tools): 4 to 9 seconds per turn, masked with backchannel animations; users could not interrupt; speech recognition was the dominant failure.
- **A well-engineered cascade on Furhat (2026)**: 1.35 s mean turn latency with streaming and array-microphone turn-taking. That is the target for Milestone 2.
- **Agent architectures on Pepper/NAO in simulation (Springer 2025/26)**: over 70 percent of multi-step tasks partially completed, only 14 percent end to end; explicit memory cut steps dramatically. Keep tool sets small and tasks short.
- **Learned motion**: the only examples are a 2026 diffusion co-speech gesture generator retargeted to NAO on a GPU server and movement primitives learned from teleoperation on NAO. No VLA or visuomotor policy on either robot.
- **Platform**: NAOqi 2.5 is the better research base (raw audio, cameras, all APIs, Python, ROS driver). NAOqi 2.9 exposes about twenty Kotlin APIs. Aldebaran filed for bankruptcy in February 2025; nothing new is coming from the vendor. Simulators exist (qiBullet, dormant; a ROS 2 Gazebo model from 2026) but no language-model work has used them.

## Voice input on the host (September 2026)

Surveyed for Milestone 2 (details in the roadmap). Findings that shaped the implementation:

- The Anthropic API has no audio input as of September 2026 (the Messages API accepts text, images and documents; the OpenAI-compatibility layer strips `input_audio`). Speech has to be recognised on the host and sent as text.
- **sherpa-onnx** (Apache 2.0, wheels for Python 3.10 to 3.14, bundles onnxruntime, no torch) is the one package that gives true streaming recognition with endpointing and partial results on a CPU: the 2023 English streaming zipformer (about 80 MB, real-time factor 0.06) or NVIDIA's Nemotron speech streaming model (2026, about 630 MB, word error rate around 7 %) through the same `OnlineRecognizer` API. It also wraps Silero and TEN VAD.
- **faster-whisper** now installs on Python 3.12 to 3.14 (CTranslate2 4.8 wheels); it is per-utterance, so it needs an endpointer in front and adds 0.2 to 0.7 s per sentence on a laptop CPU. Whisper invents text for silence; segments with high `no_speech_prob` must be dropped.
- Energy-based voice activity detection misses roughly half of quiet speech; Silero (torch-free `silero-vad-notorch`) or TEN VAD are the neural options. The implementation ships the energy detector for the whisper path and relies on sherpa-onnx's endpointing for the streaming path; a neural VAD is on the Milestone 2 list.
- Hosted streaming recognisers (AssemblyAI at $0.15/h, Deepgram Nova-3 at about $0.46/h with turn-taking, ElevenLabs Scribe v2 at $0.39/h) are the upgrade path if local accuracy disappoints; the `Transcriber` interface was written so one can be added without touching the rest.
- On the robot side, NAOqi 2.5 delivers microphone audio only to a qi service registered by the client (`registerService` then `ALAudioDevice.setClientPreferences(name, 16000, 3, 0)` and `subscribe(name)`); the callback runs on a libqi thread, single-threaded per service, and must return quickly. Pepper 1.8 has no echo cancellation; every project mutes capture while the robot speaks (`ALTextToSpeech/Status` events).

## Simulating Pepper off-robot (September 2026)

Asked whether Choregraphe's simulator could stand in for the robot before the live session. Summary (the roadmap's "Testing without the robot" section has the practical conclusion):

- Choregraphe 2.5.10.7 for Linux 64 still downloads from Aldebaran's static bucket (`choregraphe-suite-2.5.10.7-linux64.tar.gz`, 347 MB) after the company's 2025 receivership; Maxvision now owns the assets and publishes the free licence key. Unpacked copies of the 2.5.5.5 / 2.5.7.1 SDKs (`naoqi-sdk`, `pynaoqi-python2.7`, `choregraphe-suite`) exist on GitHub under Michdo93.
- The interesting part is not the GUI but `bin/naoqi-bin`: the desktop build of NAOqi itself, which runs headless and loads ALMotion, ALRobotPosture, ALTextToSpeech (events only, no audio), ALAnimatedSpeech, ALBasicAwareness, ALAutonomousLife, ALLeds (no-op), ALMemory, ALBehaviorManager and ALAnimationPlayer (without the animation package). Pepper is chosen in `etc/naoqi/ALRobotModel.xml` (`JULIETTEY20MP.xml`). Absent on the desktop: ALAudioDevice, ALAudioRecorder, ALSpeechRecognition, ALTabletService, the hardware layer (so no sonar, touch, bumper or battery keys) and camera frames.
- The Python 2.7 SDK needs a shared, UCS4 Python 2.7 (`libpython2.7.so`, `PyUnicodeUCS4_*`), which a default self-built 2.7.18 is not; Arch's AUR `python2` qualifies.
- Known breakage on modern Linux: the bundled `libz.so.1` shadows the system one (`ZLIB_1.2.9 not found`); delete it. `libpng12` is bundled. Qt 5.4 has no Wayland plugin (XWayland for the GUI; the headless binary does not care).
- qiBullet (PyBullet Pepper, own API, last release 2022), Webots (no Pepper; `naoqisim` is NAO-only and dead) and the ROS 2 Gazebo Pepper packages do not speak NAOqi and would not exercise the bridge.

Conclusion: a headless `naoqi-bin` is worth an hour before the live session to catch wrong NAOqi method names and signatures in the bridge; it cannot test sensors, audio, camera or tablet, which stay on the Milestone 0 checklist.

## Sources

Robot foundation models:

- NVIDIA GR00T N1/N1.5: https://research.nvidia.com/labs/gear/gr00t-n1_5/ ; N1.6: https://developer.nvidia.com/blog/building-generalist-humanoid-capabilities-with-nvidia-isaac-gr00t-n1-6-using-a-sim-to-real-workflow/ ; N1.7: https://github.com/NVIDIA/Isaac-GR00T and https://huggingface.co/nvidia/GR00T-N1.7-3B
- NVIDIA DreamZero: https://arxiv.org/abs/2602.15922 ; Cosmos Reason 2: https://github.com/nvidia-cosmos/cosmos-reason2 ; Cosmos 3: https://arxiv.org/abs/2606.02800
- ByteDance GR-3: https://arxiv.org/abs/2507.15493 ; GR-RL: https://seed.bytedance.com/en/blog/seed-research-gr-rl-released-a-breakthrough-in-high-precision-manipulation-for-vla-models-applying-real-world-reinforcement-learning-to-shoe-lacing-for-the-first-time
- AgiBot GO-1: https://huggingface.co/agibot-world/GO-1 ; GO-2 (secondary coverage): https://www.therobotreport.com/agibot-releases-go-2-foundation-model-embodied-ai/
- Unitree UnifoLM: https://github.com/unitreerobotics/unifolm-vla ; X Square WALL-OSS: https://github.com/X-Square-Robot/wall-x ; Xiaomi-Robotics-0: https://github.com/XiaomiRobotics/Xiaomi-Robotics-0 ; RDT-2: https://huggingface.co/robotics-diffusion-transformer/RDT2-VQ ; EO-1: https://huggingface.co/IPEC-COMMUNITY/EO-1-3B
- RoboBrain 2.0: https://huggingface.co/BAAI/RoboBrain2.0-7B ; Pelican-VL: https://github.com/Open-X-Humanoid/pelican-vl ; Embodied-R1.5: https://arxiv.org/abs/2606.11324
- Gemini Robotics-ER: https://ai.google.dev/gemini-api/docs/robotics-overview and https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-robotics-er-2/ ; Gemini Robotics SDK: https://github.com/google-deepmind/gemini-robotics-sdk ; On-Device 2 model card: https://deepmind.google/models/model-cards/gemini-robotics-on-device-2/
- Physical Intelligence openpi: https://github.com/Physical-Intelligence/openpi ; pi0.7: https://www.pi.website/blog/pi07
- Figure Helix 02: https://www.figure.ai/news/helix-02 ; 1X Redwood: https://www.1x.tech/discover/redwood-ai-world-model ; Skild S1: https://skild.ai/blogs/s1
- LeRobot 0.6: https://huggingface.co/blog/lerobot-release-v060 ; OpenVLA: https://github.com/openvla/openvla
- Anthropic, Claude Plays Robotics: https://www.anthropic.com/research/claude-plays-robotics ; Model Hardware Standard: https://www.anthropic.com/news/model-hardware-standard-research-preview

Pepper and NAO:

- Low-latency LLM-driven multimodal interaction on Pepper (HRI 2026): https://arxiv.org/abs/2603.21013 and https://github.com/studerus/pepper-android-realtime-chat
- LLM in a socially assistive robot, nursing-home study (RO-MAN 2025): https://ieeexplore.ieee.org/document/11217629/
- Augmenting human-robot dialogue with VLMs (2026): https://arxiv.org/html/2607.16318
- First impressions of a humanoid social robot with natural language (Scientific Reports 2025): https://pmc.ncbi.nlm.nih.gov/articles/PMC12137922/
- Multimodal collaborative storytelling with Pepper (Frontiers 2025): https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2025.1662819/full
- Simultaneous text and gesture generation with small models (Frontiers 2025): https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2025.1581024/full
- Expressive robot gestures through iterative RLHF (2026): https://arxiv.org/abs/2606.18747
- Pepper medical-assistance robot with Claude tool calling (2026): https://github.com/AlyLotfy/Pepper-Medical-Assistance-Robot
- ARIS agentic social-robot framework (2026): https://arxiv.org/abs/2605.00943
- LLM agent architectures for social robots (Springer): https://link.springer.com/chapter/10.1007/978-3-032-07175-0_26
- MistyPilot fast/slow framework (2026): https://arxiv.org/abs/2603.03640
- Interruption handling for conversational robots (RSS 2025): https://arxiv.org/abs/2501.01568
- Multi-party conversation on Furhat (2026): https://pmc.ncbi.nlm.nih.gov/articles/PMC13124475/
- Upgrading Pepper with a Jetson and RealSense (2024): https://arxiv.org/abs/2409.01036 ; Enhanced NAO (2025): https://arxiv.org/abs/2509.17760
- Pepper audio and bridge utilities: https://github.com/leolani/cltl-backend-naoqi , https://github.com/JBramauer/pepperspeechrecognition , https://github.com/incognite-lab/Pepper-Controller
- qiBullet: https://github.com/softbankrobotics-research/qibullet ; ROS 2 Gazebo Pepper (2026): https://github.com/tuncismail/pepper-robot-ros2-gazebo-simulation
- NAOqi 2.5 vs 2.9: https://d9d4fpk5grhfv.cloudfront.net/developer-center/articles/OSComparison/index.html ; Aldebaran bankruptcy (2025): https://uk.finance.yahoo.com/news/universities-face-getting-stuck-thousands-131320950.html

Voice input and simulation:

- sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx ; streaming zipformer models: https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html ; Nemotron streaming: https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b ; VAD wrappers: https://k2-fsa.github.io/sherpa/onnx/vad/index.html
- faster-whisper: https://github.com/SYSTRAN/faster-whisper ; TEN VAD: https://github.com/TEN-framework/ten-vad ; Silero VAD: https://github.com/snakers4/silero-vad
- Claude Messages API content types: https://platform.claude.com/docs/en/api/messages/create ; OpenAI-SDK compatibility (audio stripped): https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk
- NAOqi 2.5 ALAudioDevice: http://doc.aldebaran.com/2-5/naoqi/audio/alaudiodevice-api.html ; sound processing example: http://doc.aldebaran.com/2-5/dev/python/examples/audio/audio_soundprocessing.html ; ALBasicAwareness: http://doc.aldebaran.com/2-5/naoqi/interaction/autonomousabilities/albasicawareness-api.html ; qi Python services: http://doc.aldebaran.com/2-5/dev/libqi/guide/py-service.html
- Choregraphe 2.5 downloads (still live): https://community-static.aldebaran.com/resources/2.5.10/Choregraphe/ ; successor support pages: https://maxtronics.com/en/support/kb/softwares/downloads-softwares/pepper-2-5-downloads/ ; SDK mirrors: https://github.com/Michdo93/naoqi-sdk-2.5.7.1-linux64 , https://github.com/Michdo93/pynaoqi-python2.7-2.5.7.1-linux64 , https://github.com/Michdo93/choregraphe-suite-2.5.10.7-linux64
- Virtual robot docs: http://doc.aldebaran.com/2-5/dev/tools/robot-simulation.html ; Python SDK install: http://doc.aldebaran.com/2-5/dev/python/install_guide.html ; Aldebaran receivership: https://www.therobotreport.com/aldebaran-pepper-nao-robots-receivership/
