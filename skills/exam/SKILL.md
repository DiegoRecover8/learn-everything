---
name: exam
description: Learn Everything — mock exam under exam conditions, with a mark and a breakdown by topic at the end.
argument-hint: "[number of questions]"
disable-model-invocation: true
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# Mock exam

A rehearsal under exam conditions: no feedback until the end, then a mark and a breakdown.

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory). It prints JSON; errors come as `{"error": ...}` on stderr. The rules in `../learn/SKILL.md` ("Rules that always apply") hold here too. Also read `../learn/references/exercises.md` for how to ask and grade. Run `le.py status` first for the learner's language.

## 1. Agree the format

Ask with AskUserQuestion how many questions (10, 20 or 30; 20 recommended) and whether they want to time themselves. If the learner has told you the real exam format, mirror it.

A mock exam needs studied topics. If none is studied yet, say so and offer a study session (`/learn`) instead.

## 2. Prepare

```bash
le.py mock -n 20
```

Open `session_file` with the **Read tool** (it holds the answers; never print it). It has:

- `questions`: chosen from the question bank, weighted towards important topics and weak spots.
- `missing`: how many are still needed if the bank is too small.
- `topics`: the studied topics, with weight and status.

If `missing` is more than 0, write that many new questions, giving more to topics with a higher weight or a weaker status. Get the material with `le.py notes <topic id>` (only the pages of that topic). Use the exam's own question types. Mix them in with the rest.

## 3. Run it

- Announce the rules: N questions, no feedback until the end.
- Ask one question at a time. After each answer, only acknowledge ("Noted. Question 7 of 20.") and move on. No hints, no corrections.
- Keep a private record of each answer and its quality.
- If they want to stop early, grade what was answered.

## 4. Record

```bash
le.py record <<'JSON'
{
  "kind": "mock",
  "answers": [{"card": "c3", "quality": 5}, {"card": "c9", "quality": 1}],
  "new_cards": [{"topic": "t2", "type": "mcq", "prompt": "…", "options": ["…", "…", "…"], "answer": "…", "quality": 4}]
}
JSON
```

The mark is the number of answers with quality 3 or more over the total. If the real exam scores differently (partial credit, penalties for wrong answers), compute it that way and send it as `"mock": {"score": 13.5, "total": 20}`.

## 5. Results

1. **The mark**: `mock.score` out of `mock.total`, and the percentage. Compare with previous mock exams if they mention them.
2. **By topic**: a short table of topic, correct and total. Name the two or three weakest.
3. **Corrections**: go through the wrong answers one by one with the feedback format of `exercises.md`. This is where most of the learning happens, so do not rush it.
4. **What to do next**: which topics to revisit, in one or two lines. Wrong answers are already scheduled for tomorrow.
5. XP, streak and achievements, exactly as `record` returned them.

Be honest about the mark and specific about what to fix. A mock exam that went badly is useful information, not a failure.
