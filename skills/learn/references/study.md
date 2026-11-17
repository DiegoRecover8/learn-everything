# Study session

The daily session: review what is due, learn what is new, practise it, record everything.

Every `le.py` command below carries `--exam <slug>` for the exam chosen at the start (rule 11 of `SKILL.md`); it is left out here to keep the examples short.

Also read `references/exercises.md` (how to ask and grade) and, if there is a new topic today, `references/teaching.md` (how to explain).

## 1. Recap, then get today's session

Open with a short recap built from the `le.py status` you already ran, so the learner remembers where they are. Four or five lines at most, only the lines that have data:

```
📚 Física II — 24 days to go · 🔥 8-day streak · ⭐ Level 8
📖 Last time (yesterday): Gauss's law · 8/10 correct
🗺️ 11 of 15 topics studied, 3 mastered
💪 Strong: Coulomb's law · 🎯 To improve: Electric potential
📅 Today: 12 reviews + Faraday's law
```

Take every number from `status` (`plan.days_left`, `streak`, `level`, `topics`, `recap.last_session`, `recap.strong`, `recap.weak`, `recap.next_topic`). On the very first session there is nothing to recap: just say what today holds. Then:

```bash
le.py next            # today's plan
le.py next --extra    # extra practice after the goal is met
```

The output gives counts only. Open `session_file` with the **Read tool**: it holds the review questions with their answers, so never print it or quote it. It contains:

- `learner`, `exam`: language, education level, exam type, goal.
- `plan`: `days_left`, `phase` (`learn`, `review`, `exam_day`, `past`), `overload`.
- `reviews`: questions due today.
- `new_topics`: topics to learn today, each with `concepts`, `sources` and `notes_cmd`.

## 2. Review

Ask each question in `reviews`, one at a time, following `exercises.md`. Keep a private tally of `card id → quality (0-5)`.

Keep it moving: short feedback when the answer is right, a real explanation when it is wrong. If the same idea fails twice in a row, stop and re-explain it from the notes before going on.

## 3. Learn the new topic

For each topic in `new_topics`:

1. Run its `notes_cmd` (`le.py notes <topic id>`): it prints only the pages of the notes named in `sources`. Read the whole notes file (`notes_files`) only if that excerpt is missing something you need.
2. Explain it following `teaching.md`.
3. Practise it right away with **5 to 8 new questions** you write from the notes, asked one at a time. Mix the types, favouring the ones that match `exam.exam_type` (see `exercises.md`). Go from easy to hard.
4. Keep each question you asked, with its answer and the quality the learner got, to send as `new_cards`.

If time is short, one well-learned topic beats two rushed ones. Leave the rest for tomorrow: the plan redistributes by itself.

## 4. Record

Send everything in one call:

```bash
le.py record <<'JSON'
{
  "answers": [
    {"card": "c12", "quality": 5},
    {"card": "c7", "quality": 2}
  ],
  "studied_topics": ["t4"],
  "new_cards": [
    {"topic": "t4", "type": "mcq", "prompt": "…", "options": ["…", "…", "…", "…"], "answer": "…",
     "explanation": "…", "source": "s1 p.9", "quality": 4},
    {"topic": "t4", "type": "cloze", "prompt": "The flux through a closed surface equals ___ / ε0.",
     "answer": "the enclosed charge", "accepted": ["enclosed charge", "Q enclosed"], "quality": 3}
  ]
}
JSON
```

- `answers`: reviews of existing questions.
- `studied_topics`: topics explained today. Only list a topic once it has been explained **and** practised.
- `new_cards`: the new questions, in the format of `exercises.md`. `quality` is how the learner did; leave it out for a question you want to save without having asked it.
- `minutes` is optional; the script measures the time since `next`.

If the command returns an error, nothing was saved: fix the payload and send it again.

## 5. Close

Use what `record` returned, exactly:

- Score: `correct` out of `answers`.
- `xp_gained`, and the new level if `level_up` is true.
- `streak` in days. Mention `freeze_used` (a streak freeze saved the streak) or `freeze_earned` if true.
- Every item in `new_achievements`, with its icon and name. Make this a small celebration.
- `topic_changes`, when a topic moved up (for example to mastered) or dropped.
- `goal_met_now`: today's goal is complete.
- Tomorrow: `tomorrow.reviews_due` reviews and `tomorrow.new_topics` new topics.

Finish with one honest sentence on what went well and one on what to watch. Three or four lines in total, not a report.

Then offer one more step with AskUserQuestion: "🔁 Review <the weakest topic>" (from `recap.weak`, or a topic that just dropped in `topic_changes`), "🔁 Review another topic or material", "✋ Done for today". A review follows `../review/SKILL.md` (path from the learn skill's base directory) with that topic.

## Special cases

- **Nothing due and no new topic**: the goal is already met. Offer `--extra`, a mock exam, or rest.
- **`phase` is `review`**: all topics are studied. Sessions are reviews only; suggest a mock exam every few days.
- **`phase` is `exam_day`**: a light warm-up of the weakest questions, no new content, and good luck.
- **`phase` is `past`**: ask how it went, and whether to set a new date (`/learn:setup`) or archive the exam (`/learn:data`).
- **`overload` is true**: say once that the plan is tight, and suggest more daily time or dropping low-weight topics.
- **The learner asks a question mid-session**: answer it from the notes, then return to where you were.
