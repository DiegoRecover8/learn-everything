---
name: review
description: Learn Everything — review a topic, unit or material of your choice (or your weakest topics), whether or not its questions are due.
argument-hint: "[exam] [topic, unit or material]"
disable-model-invocation: true
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# Review

A focused review of what the learner picks: one topic, a whole unit, everything that came from one material, or their weakest topics. It works any time, also at the start or end of a daily session, and does not replace the daily plan.

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory). It prints JSON; errors come as `{"error": ...}` on stderr. The rules in `../learn/SKILL.md` ("Rules that always apply") hold here too. Also read `../learn/references/exercises.md` for how to ask and grade.

## 1. Which exam, which target

Run `le.py status` (learner's language, list of `exams`).

- **Exam.** The arguments may start with an exam slug: the dashboard's "Review" buttons copy `/learn:review <exam> <id>`. If the first word is one of the slugs in `exams`, that is the exam. Otherwise use the exam already chosen in this conversation; with several exams and none chosen, ask which. From then on, put `--exam <slug>` in every command.
- **Target.** The rest of the arguments: `t3` (a topic), `u2` (a unit), `s1` (everything from a material), or words from a name ("ley de gauss", "unit 2", "the slides on waves"). With no target, ask with AskUserQuestion: "🎯 My weakest topics (recommended)", one or two topics from `recap.weak`, "📄 Everything from one material"; they can also type a topic. If they pick a material, list them with `le.py source list` and ask which.

## 2. Prepare

```bash
le.py --exam <slug> review <target> [-n 10]
le.py --exam <slug> review               # no target: the weakest topics
```

It refuses with the list of topics when nothing matches: pick the closest one, or ask. Open `session_file` with the **Read tool** (it holds the answers; never print it). It has:

- `questions`: up to `-n` questions of those topics, the ones failed most and the overdue ones first, due or not.
- `topics`: each target topic with its status, accuracy, `concepts`, `summary` and `notes_cmd`.

`not_studied_yet` lists target topics never taught. Say so, and offer to teach them now (as in `../learn/references/study.md`, step 3) or to leave them for the daily plan.

## 3. Run it

For each topic, in order:

1. **A quick refresher**, three to five lines: the key ideas and the `concepts`, from `le.py notes <topic id>`. For a topic the learner keeps failing, explain the part they get wrong, from the notes.
2. **Its questions**, one at a time, with full feedback (format of `exercises.md`). Keep a private tally of `card id → quality`.
3. If a topic has fewer than 4 questions, write new ones from its notes to reach about 5, mixing types. Keep them, with the quality the learner got, for `new_cards`.

If the same idea fails twice, stop and re-explain it before going on.

## 4. Record

```bash
le.py --exam <slug> record <<'JSON'
{"kind": "review", "answers": [{"card": "c3", "quality": 4}], "new_cards": [...], "studied_topics": []}
JSON
```

`studied_topics` only for topics taught for the first time here. Reviewing early is fine: the schedule adapts.

## 5. Close

In three or four lines, from what `record` returned: score, XP, streak, any new achievement, and the topics whose status changed. Name what is now solid and what still needs work. Then offer, with AskUserQuestion: "🔁 Review another topic", "📅 Today's plan" (if `remaining_today` is not empty), "✋ Done".
