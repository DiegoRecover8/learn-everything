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

**Every question goes through AskUserQuestion**, so the learner clicks instead of typing. AskUserQuestion takes up to 4 questions per call and **2 to 4 options per question**, never more.

**Typing.** AskUserQuestion has a free-text choice of its own, but learners do not always see it, so never point them to it ("use Other"). When an answer may need typing (an exact date, a name), add an explicit option for it, such as "✏️ Type the exact date". When they pick it, ask for that one thing as a short plain-text question and wait. That is the only setup question asked as plain text.

## 1. Check what exists

Run `le.py status`.

- **No profile** (`next_action` is `setup` and there is no `learner`): do the full setup, steps 2 to 5. `suggest` holds a likely name and language from the computer.
- **A profile exists**: ask what they want to do, with options such as "➕ New exam", "📅 Change the exam date or daily time", "👤 Change my name, language or level". Ask only the questions that matter for the answer, with the same options as below, then save in step 5 sending only the changed fields. When the exam to change is not clear and `exams` lists several, ask which one first.

## 2. Round 1: language, alone

One question, so that everything after it can be asked in the chosen language.

- Write it in the language the learner is writing in. If they typed only the command, write it in two languages, for example "🌍 Language · Idioma".
- Six languages have full translations: 🇪🇸 Español, 🇬🇧 English, 🇫🇷 Français, 🇩🇪 Deutsch, 🇮🇹 Italiano, 🇵🇹 Português. They do not fit in one question, so:
  1. First question, 4 options: the most likely language (the one they write in, or `suggest.language`), then Español and English if not already there, and last "🌍 More languages" with the flags of the rest in its description (for example "🇫🇷 Français · 🇩🇪 Deutsch · 🇮🇹 Italiano · 🇵🇹 Português").
  2. If they pick "More languages": a second question with the languages not shown yet (up to 4), each with its flag.
- Teaching works in any language they type in; the dashboard and the session-start lines use English for languages outside the six.

From here on, write every question, option and description in the chosen language.

## 3. Round 2: the learner and the exam (one call, 4 questions)

1. **Name** (header "Name"). Options: the first name from `suggest.name`, the full name if different, and "✏️ Another name". If there is no suggestion: a friendly default such as "Student" and "✏️ Type my name".
2. **Level** (header "Level"). Four options, the labels in the learner's language, using local school names in the description:
   - 🏫 Secondary school. Description with the local names: ESO / Bachillerato in Spain, high school in the US, GCSE / A-levels in the UK, lycée in France, Gymnasium in Germany.
   - 🎓 University (bachelor's degree).
   - 🔬 Postgraduate (master's, PhD).
   - 💼 Professional or competitive exam: certifications (IT, languages, finance), licences, and public-service competitive exams such as the Spanish *oposiciones*.
   Save the code: `secondary`, `university`, `postgraduate` or `professional`. If they type something else, save their words.
3. **Exam name** (header "Exam"). Up to two suggestions from what you know: the command arguments, the name of the working folder if it looks like a subject, files they mentioned. Always include "📂 Name it from my materials" and "✏️ Type the name".
4. **Exam date** (header "Date"). Four options, the real date in each description, computed from today: "📅 In about a month", "📅 In about 3 months", "✏️ Type the exact date", "🤷 No date yet". Save it as `YYYY-MM-DD`, or `null` for no date. A typed date can come in any form ("15/01", "next Friday", "20 de enero"): turn it into `YYYY-MM-DD`, choosing the next such date in the future, and repeat it back in the confirmation.

Then ask the typed answers, if any, one plain-text question each.

## 4. Round 3: how to study (one call, 4 questions)

1. **Daily time** (header "Time"): 🌱 15 min (light), 📘 30 min (normal, recommended), 🔥 60 min (intense), ✏️ Another amount (5 to 480 minutes, typed).
2. **Goal** (header "Goal"): ✅ Pass (`pass`), ⭐ A high mark (`high`), 🏆 Master it (`master`).
3. **Exam format** (header "Format"): 📝 Multiple choice (`test`), ✍️ Written answers (`essay`), 🧮 Problems (`problems`), 🔀 Mixed or oral (`mixed`; if they say oral, save `oral`). This decides which question types you favour.
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

## Changing the language later

The dashboard's buttons and labels follow the new language as soon as it is saved. The names you wrote earlier do not: units, topics, key concepts and material titles stay in the language they were written in, and they fill most of the page. So, after saving a new language, for each exam in `exams` that has a roadmap, ask (AskUserQuestion, "🌍 Translate them (recommended)" / "Keep them as they are") whether to translate those names too. If yes:

1. `le.py --exam <slug> labels show` prints them with their ids.
2. Translate every `name`, `summary`, `concepts` item and `title`. Keep technical terms that are normally left untranslated.
3. Send them back with `le.py --exam <slug> labels set`, same shape and ids (only the fields you changed). Progress, questions and notes are not touched.
4. Run `le.py dashboard --no-open` to rebuild the dashboards.

Notes and stored questions stay in the language of the materials. That is fine: you always ask questions and explain in the learner's language.

## 6. Next step

Confirm in one or two lines what was saved (exam, date, daily time). Then, by the materials answer:

- **Now** or **look for them**: follow `../add/SKILL.md` (with "look for them", start at its `le.py find` step).
- **Later**: tell them to run `/learn:add` whenever they have the files, and that they can drag a file or folder into the window.
