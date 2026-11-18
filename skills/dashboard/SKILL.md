---
name: dashboard
description: Learn Everything — build and open your study dashboard (roadmap, content map, streak, accuracy, reviews ahead, achievements), one per exam.
argument-hint: "[exam]"
disable-model-invocation: true
allowed-tools: Bash, AskUserQuestion
---

# Dashboard

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory; type the full command each time, never through a shell variable such as `$LE`, which zsh does not split into words). Speak the learner's language (`learner.language` from `le.py status`).

1. Run `le.py status`. Each exam has its own dashboard. If the learner named an exam, or one was chosen earlier in this conversation, use it. Otherwise open the active one; there is no need to ask, because every dashboard has a switcher at the top to jump to the others.
2. Run `le.py [--exam <slug>] dashboard`. It rebuilds the dashboard of every exam and opens the one asked for in the browser (`opened` says whether it could). If there is no profile or exam yet, it fails: point the learner to `/learn:setup`.
3. Give the file path, so they can open it again or bookmark it. It is updated each time they run `/learn:dashboard`.
4. Add a three-line summary from `status`: days left and streak, progress (`topics.studied` of `topics.total`, `topics.mastered` mastered), and from `recap` the strongest topic and the one to work on. Do not repeat the whole page.
5. Mention once that each topic (and each material) has a "🔁 Review" button: it copies a command such as `/learn:review fisica-ii t3`, which they paste here to review just that.
