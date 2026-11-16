---
name: setup
description: Learn Everything — create your profile and an exam, or change your exam date, daily time, language or level.
argument-hint: "[what to change]"
disable-model-invocation: true
allowed-tools: Read, Write, Bash, Glob, AskUserQuestion
---

# Setup

Creates the learner profile and an exam, or edits them. It takes about a minute.

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory). It prints JSON; errors come as `{"error": ...}` on stderr. The rules in `../learn/SKILL.md` ("Rules that always apply") hold here too.

**Every question goes through AskUserQuestion**, so the learner clicks instead of typing. Its automatic "Other" choice is where they type anything that is not listed (an exact date, another name). Never ask a setup question as plain text. AskUserQuestion takes up to 4 questions per call and 2 to 4 options per question.

## 1. Check what exists

Run `le.py status`.

- **No profile** (`next_action` is `setup` and there is no `learner`): do the full setup, steps 2 to 5. `suggest` holds a likely name and language from the computer.
- **A profile exists**: ask what they want to do, with options such as "➕ New exam", "📅 Change the exam date or daily time", "👤 Change my name, language or level". Ask only the questions that matter for the answer, with the same options as below, then save in step 5 sending only the changed fields.

## 2. Round 1: language, alone

One question, so that everything after it can be asked in the chosen language.

- Write it in the language the learner is writing in. If they typed only the command, write it in two languages, for example "🌍 Language · Idioma".
- Options, each with its flag, the most likely first (the language they write in, or `suggest.language`), up to 4:
  🇪🇸 Español · 🇬🇧 English · 🇫🇷 Français · 🇩🇪 Deutsch · 🇮🇹 Italiano · 🇵🇹 Português
- "Other" covers any other language. Teaching works in every language; the dashboard and the session-start lines are translated into the six above and use English for the rest.

From here on, write every question, option and description in the chosen language.

## 3. Round 2: the learner and the exam (one call, 4 questions)

1. **Name** (header "Name"). Options: the first name from `suggest.name`, then the full name if different. If there is no suggestion, offer a friendly default such as "Student" and remind them that "Other" lets them type theirs.
2. **Level** (header "Level"). Four options, the labels in the learner's language, using local school names in the description:
   - 🏫 Secondary school. Description with the local names: ESO / Bachillerato in Spain, high school in the US, GCSE / A-levels in the UK, lycée in France, Gymnasium in Germany.
   - 🎓 University (bachelor's degree).
   - 🔬 Postgraduate (master's, PhD).
   - 💼 Professional or competitive exam: certifications (IT, languages, finance), licences, and public-service competitive exams such as the Spanish *oposiciones*.
   Save the code: `secondary`, `university`, `postgraduate` or `professional`. If they type something else, save their words.
3. **Exam name** (header "Exam"). Up to three suggestions from what you know: the command arguments, the name of the working folder if it looks like a subject, files they mentioned. Always include "📂 Name it from my materials". If you have no suggestion at all, that option plus one generic example for their level is enough; "Other" lets them type it.
4. **Exam date** (header "Date"). Options computed from today's date, each with the real date in the description: "📅 In about 2 weeks", "📅 In about a month", "📅 In about 3 months", "🤷 No date yet". The question says that "Other" is for the exact date. Save it as `YYYY-MM-DD`, or `null` for no date.

## 4. Round 3: how to study (one call, 4 questions)

1. **Daily time** (header "Time"): 🌱 15 min (light), 📘 30 min (normal, recommended), 🔥 60 min (intense). "Other" for any number of minutes from 5 to 480.
2. **Goal** (header "Goal"): ✅ Pass (`pass`), ⭐ A high mark (`high`), 🏆 Master it (`master`).
3. **Exam format** (header "Format"): 📝 Multiple choice (`test`), ✍️ Written answers (`essay`), 🧮 Problems (`problems`), 🔀 Mixed (`mixed`). Oral exams go through "Other" (`oral`). This decides which question types you favour.
4. **Materials** (header "Materials"): "📎 I'll drag or attach them now", "🔍 Look for them in Downloads, Desktop and Documents", "⏰ Later".

## 5. Save

```bash
le.py init <<'JSON'
{
  "name": "Ana",
  "language": "Español",
  "education_level": "university",
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

- `language` is the language name without the flag.
- `exam_date` is `YYYY-MM-DD` or `null`. `daily_minutes` is 5 to 480.
- "Name it from my materials": use a provisional name in their language ("Mi examen", "My exam"). After the roadmap is built, the add step proposes a real name and renames it.
- A new exam name creates a new exam and makes it active. To edit an existing exam, send `"exam": {"slug": "<slug>", ...changed fields}`. To rename it, add `"name": "<new name>", "rename": true`: the folder is renamed too.
- Profile fields outside `exam` update the profile.

## 6. Next step

Confirm in one or two lines what was saved (exam, date, daily time). Then, by the materials answer:

- **Now** or **look for them**: follow `../add/SKILL.md` (with "look for them", start at its `le.py find` step).
- **Later**: tell them to run `/learn:add` whenever they have the files, and that they can drag a file or folder into the window.
