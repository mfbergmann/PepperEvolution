---
name: Bug report
about: Something broke or behaved wrongly
title: ''
labels: ['bug']
assignees: ''
---

**What happened**
What you did, and what Pepper (or the host) did.

**What you expected**

**How to reproduce**
1.
2.

**Robot**
- Pepper body version (e.g. 1.8A):
- NAOqi version (`curl http://<robot>:8888/health`):
- Bridge version (same output):
- Room (office, large open room, distance to people):

**Host**
- OS and Python version:
- PepperEvolution version or commit (`git log --oneline -1`):
- AI model (`AI_MODEL`, provider if not Claude):
- Speech-to-text (`STT_BACKEND`, `STT_MODEL`), if relevant:
- Any settings changed from `env.example`:

**Logs**
The relevant part of the host log (`pepper_evolution.log`) and the bridge log (`python robot_bridge/deploy.py --logs`).
Please remove API keys, names and anything else personal first.

```
paste logs here
```
