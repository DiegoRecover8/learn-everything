---
name: dashboard
description: Learn Everything — build and open your study dashboard (roadmap, content map, streak, accuracy, reviews ahead, achievements).
disable-model-invocation: true
allowed-tools: Bash
---

# Dashboard

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory). Speak the learner's language (`learner.language` from `le.py status`).

1. Run `le.py dashboard`. It writes a single HTML file and opens it in the browser (`opened` says whether it could). If there is no profile or exam yet, it fails: point the learner to `/learn:setup`.
2. Give the file path, so they can open it again or bookmark it. It is regenerated each time they run `/learn:dashboard`.
3. Run `le.py status` and add a three-line summary from it: days left and streak, progress (`topics.studied` of `topics.total`, `topics.mastered` mastered), and from `recap` the strongest topic and the one to work on. Do not repeat the whole page.
