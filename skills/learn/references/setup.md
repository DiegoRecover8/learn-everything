# Setup

Creates the learner profile and an exam, or edits them. Takes about a minute.

## 1. Check what exists

Run `le.py status`. If a profile exists, ask what they want: a new exam, or to change something (name, language, level, exam date, daily time, goal). For a change, skip to step 3 and send only the changed fields.

## 2. Ask

Use AskUserQuestion, in two short rounds. Ask in the language the learner is writing in. Free-text answers (name, exam name, date) are asked as plain questions in the conversation.

Round 1, about the learner (skip if a profile exists):

- **Name**: what to call them.
- **Language**: the language to study in. Default to the one they are writing in.
- **Education level**: secondary school, high school / Bachillerato, university, competitive public exam (oposición), professional certification, or other. This sets how deep and how technical you go.

Round 2, about the exam:

- **Exam name**: for example "Física II" or "Constitutional Law, part 1".
- **Exam date**: optional. If they do not know it, leave it empty; the plan then uses a steady pace.
- **Daily intensity**: light (15 min), normal (30 min), intense (60 min), or a custom number of minutes.
- **Goal**: `pass` (just pass), `high` (a high mark), `master` (really master it).
- **Exam type**: `test` (multiple choice), `essay` (written answers), `problems` (numeric or worked problems), `mixed`, or `oral`. This decides which exercise types you favour.

## 3. Save

```bash
le.py init <<'JSON'
{
  "name": "Ana",
  "language": "Español",
  "education_level": "Universidad",
  "exam": {
    "name": "Física II",
    "exam_date": "2027-01-20",
    "daily_minutes": 30,
    "goal": "high",
    "exam_type": "mixed"
  }
}
JSON
```

- `exam_date` is `YYYY-MM-DD` or `null`. `daily_minutes` is 5 to 480.
- A new exam name creates a new exam and makes it active. To edit an existing exam, send `"exam": {"slug": "<slug>", ...changed fields}`.
- Profile fields outside `exam` update the profile.

## 4. Next step

Confirm in one or two lines what was saved (exam, date, daily time), then ask for their materials: a file, several files, or a folder. Continue with `references/add.md`.
