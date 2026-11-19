<div align="center">

<img src="docs/banner.png" alt="Learn Everything — Your notes. Your roadmap. Your exam." width="100%">

**Turn your own notes into an exam plan, a personal tutor and a daily habit.**

A plugin for [Claude Code](https://claude.com/claude-code)

[![Version](https://img.shields.io/badge/version-0.2.3-2a78d6)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-0ca30c)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776ab)](https://www.python.org/)
[![Tests](https://github.com/DiegoRecover8/learn-everything/actions/workflows/tests.yml/badge.svg)](https://github.com/DiegoRecover8/learn-everything/actions/workflows/tests.yml)

🇬🇧 **English** · 🇪🇸 [Español](README.es.md)

</div>

---

You give it your PDFs, slides, Word files and photos of your notes **once**. It builds a roadmap of everything that goes into the exam, plans every day until exam day, explains each topic from your own materials, quizzes you, brings questions back right before you would forget them, and keeps you going with streaks, XP and achievements, Duolingo style.

https://github.com/user-attachments/assets/e3641ba7-4212-4db5-afcc-65499a3eeaa2

## ✨ Features

| | |
|---|---|
| 📄 **Reads your materials once** | PDF, Word, PowerPoint, Jupyter notebooks, photos, text. Each file becomes faithful study notes and is recognised by its content, so it is never read twice. |
| 🗺️ **Roadmap and content map** | Units and topics with their key concepts, weighted by how much each one counts in the exam. |
| 📅 **A plan up to exam day** | Topics per day from your exam date and your daily minutes. The last days are kept for revision, and a missed day is re-planned automatically. The date is optional. |
| 🧑‍🏫 **Explains, then tests** | Short interactive explanations taken from your notes, then multiple choice, true or false, fill the gap, short answer, open questions, ordering and matching. |
| 🔁 **Spaced repetition** | SM-2 brings each question back at the right moment, and always before the exam. |
| 🎯 **Review what you choose** | A topic, a whole unit, everything from one PDF, or your weakest topics, whenever you want. Also from a button on each topic of the dashboard. |
| 📝 **Mock exams** | No feedback until the end, then a mark and a breakdown by topic. |
| 🔥 **Streaks, XP, levels, 16 achievements** | Plus streak freezes, so one bad day does not wipe out your streak. |
| 📊 **Dashboard** | One local HTML page: countdown, content map, roadmap, strong and weak topics, activity, accuracy and reviews ahead. Light and dark mode. |
| 👋 **A recap every time you come back** | What you covered last time, where you are strong, what to work on, and today's plan. |
| 📚 **Several exams at once** | Each subject keeps its own roadmap, questions and dashboard; `/learn` asks which one to study. Your streak and XP are shared. |
| 🌍 **Any language** | Study in your own language. The dashboard and the status lines come in English, Spanish, French, German, Italian and Portuguese. |
| 🔒 **Private** | Everything stays on your computer as plain files. |

## 🚀 Install

You need [Claude Code](https://claude.com/claude-code) and Python 3.8 or newer (already installed on macOS and most Linux systems). Nothing else.

```bash
claude plugin marketplace add DiegoRecover8/learn-everything
claude plugin install learn@learn-everything
```

Restart Claude Code and type `/learn`.

To try it without installing, clone the repository and start Claude Code with `claude --plugin-dir /path/to/learn-everything`.

## ⚡ Quick start

```
/learn:setup     answer a few clickable questions: language, name, level, exam, date, daily time
/learn:add       drag your files or a folder into the window, or let it find recent files for you
/learn           today's session
```

After that, `/learn` once a day is all you need. Every time you open Claude Code you see where you stand:

```
[Learn Everything] 📚 Physics II — 24 days to the exam
[Learn Everything] 🔥 Streak: 8 days · ⭐ Level 8 (3543 XP)
[Learn Everything] 🗺️ Progress: 11/15 topics studied · 3 mastered
[Learn Everything] 📖 Last session (yesterday): Gauss's law · 8/10 correct
[Learn Everything] 💪 Strong: Coulomb's law · 🎯 To improve: Electric potential
[Learn Everything] 📅 Today: 14 reviews + 1 new topic — run /learn
[Learn Everything] 📚 Also: History of Art (12 days)
```

## 🧭 Commands

Type `/learn` and Claude Code lists them all.

| Command | What it does |
|---|---|
| `/learn` | Asks which exam and what to do, then runs today's session (or setup, or materials, if missing) |
| `/learn:setup` | Create an exam, or change the date, daily time, language or level |
| `/learn:add` | Load materials and build or extend the roadmap |
| `/learn:review` | Review a topic, a unit, a document or your weakest topics: `/learn:review gauss` |
| `/learn:exam` | Mock exam |
| `/learn:dashboard` | Build and open the dashboards (one per exam, with a switcher) |
| `/learn:data` | Switch exams, back up, export, restore, delete |

You can also just say it: "test me on unit 2", "I have new slides", "how am I doing?".

## 📎 Adding your materials

No need to type paths:

- **Drag and drop** a file or a whole folder into the Claude Code window; its path is pasted for you.
- **Let it look**: `/learn:add` can list your recent PDFs, Word files, slides and images from Downloads, Desktop and Documents, and you pick them by number.
- **Paste** photos of your notes (Ctrl+V) or text straight into the chat.

| Type | Notes |
|---|---|
| PDF | Text read page by page with nothing to install; pages with figures and scanned pages are turned into images for Claude to look at |
| DOCX, PPTX | Text, tables, speaker notes and embedded images |
| IPYNB | Jupyter notebooks: explanations, code, outputs and plots |
| RTF | Rich text |
| JPG, PNG, WEBP, GIF | Photos of notes, whiteboards, textbook pages |
| TXT, MD, CSV | Read as they are |

Claude reads scans and photos itself, so no OCR tool is needed. The first read takes a few minutes for long documents; it happens only once.

## ⚙️ How it works

**A session** has three parts: review the questions due today, learn the next topic on the roadmap, and practise it with new questions. It starts with a short recap and ends with your score, the XP you earned, your streak and what comes tomorrow.

**The plan.** With an exam date, the topics left are spread over the days left, keeping about the last fifth (at least two days) for revision and mock exams. Without a date, it goes at a steady pace based on your daily minutes. If the plan needs more per day than your time allows, it tells you.

**Reviews.** Each answer is graded from 0 to 5. A good answer pushes the question further away (1 day, 6 days, then longer each time); a bad one brings it back tomorrow. As the exam gets close, intervals are shortened so every question is seen again in time.

**Topic status** goes from not started to learning, reviewing and mastered, according to how you answer its questions. It can go down again if you start failing them.

**Streak.** A day counts when you record at least one session. Every 7 days in a row you earn a streak freeze (up to 2), which covers one missed day automatically.

**XP.** Up to 10 per correct answer, 2 for a wrong one, 20 per topic studied, 30 for meeting the daily goal, 50 per mock exam.

**Light on tokens.** Your materials are read once and turned into notes. Each session then reads only the pages of the topic it is teaching, and the plugin's extra commands cost nothing until you use them.

**Who does what.** Claude teaches and grades; a small Python script does all the bookkeeping and arithmetic, so streaks, schedules and achievements are always computed the same way.

## 🔒 Your data

Everything is stored as plain files in `~/learn-everything/` (set `LEARN_EVERYTHING_HOME` to use another folder):

```
profile.json            you: XP, level, streak, achievements, shared by all your exams
<exam>/                 one folder per exam or subject, independent of the others
  exam.json             settings and roadmap
  cards.json            questions and their review schedule
  log.jsonl             one line per session
  sources/              study notes made from your materials
  dashboard.html        this exam's dashboard
```

- Your original files are never modified or copied; only the notes made from them are kept.
- A backup is taken before every change (the last 10 are kept in `.backups/`).
- `/learn:data` exports an exam to a zip, restores it on another computer, or deletes it. Deleted exams go to `.trash/`, so a mistake can be undone.
- To remove everything, delete the `~/learn-everything/` folder.

## ❓ FAQ

<details>
<summary><b>Where does it work?</b></summary>

In Claude Code: the terminal, the desktop app and the IDE extensions. It needs Python on the same computer and a folder to keep your progress, so it does not run in the regular claude.ai chat. Cowork supports plugins, but this one has not been tested there yet.
</details>

<details>
<summary><b>Do I need to know how to code?</b></summary>

No. You talk to Claude in plain words and answer questions by clicking options. Claude runs everything for you: it reads your files, keeps your progress and opens the dashboard. The only thing you type in a terminal is the two install commands above, once.
</details>

<details>
<summary><b>Can I prepare several exams at once?</b></summary>

Yes. Each exam has its own folder, roadmap, questions, plan and dashboard; XP, streak and achievements are shared. `/learn` asks which exam you want to study, the dashboards link to each other, and you can even study two subjects at the same time in two Claude Code windows.
</details>

<details>
<summary><b>I updated from 0.1 and the commands changed.</b></summary>

From 0.2.0 the plugin is called `learn`, so its commands are `/learn:setup`, `/learn:add` and so on. Reinstall it once:

```bash
claude plugin uninstall learn-everything@learn-everything
claude plugin marketplace update learn-everything
claude plugin install learn@learn-everything
```

Your study data is not touched.
</details>

<details>
<summary><b>How much does it cost in usage?</b></summary>

Reading long materials the first time is the expensive part, and it happens once per file. Daily sessions read only the notes of the topic being studied and the questions due today.
</details>

## 🤝 Contributing

Contributions are welcome, especially **new languages** (one JSON file in [`scripts/i18n/`](scripts/i18n)), bug reports and ideas. Read [CONTRIBUTING.md](CONTRIBUTING.md) to get started; everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go through [SECURITY.md](SECURITY.md).

## 📁 Project layout

```
.claude-plugin/        plugin and marketplace manifests
skills/learn/          /learn: the daily session, plus the guides for teaching and exercises
skills/<action>/       /learn:setup, /learn:add, /learn:review, /learn:exam, /learn:dashboard, /learn:data
scripts/le.py          the data engine (Python standard library only)
scripts/i18n/          translations of the dashboard and status lines
scripts/dashboard_template.html
hooks/hooks.json       the status lines shown when Claude Code starts
tests/test_le.py       python3 -m unittest discover -s tests
```

## 🙏 Credits

Learn Everything builds on ideas from two MIT-licensed projects:

- [learn-anything](https://github.com/ChenChenyaqi/learn-anything) by ChenChenyaqi: the knowledge map, the structure of explanations and the question types.
- [Fluent](https://github.com/m98/fluent) by m98: the Claude Code plugin shape, the session-start status, the streak and SM-2 spaced repetition.

## 📄 License

[MIT](LICENSE)
