# Changelog

All notable changes are listed here. The project follows [semantic versioning](https://semver.org).

## Unreleased

## 0.2.0 — 2026-11-16

### Changed

- **The plugin is now called `learn`**, so every action has its own command and they all show up when you type `/learn`: `/learn:setup`, `/learn:add`, `/learn:exam`, `/learn:dashboard`, `/learn:data`. `/learn` still runs the daily session. To update from 0.1: `claude plugin uninstall learn-everything@learn-everything`, `claude plugin marketplace update learn-everything`, `claude plugin install learn@learn-everything`. Study data is not affected.
- **Setup asks everything with clickable options**: name, level, exam name, exam date, daily time, goal, format and where your materials are. "Other" is always there to type your own answer.
- **The language is asked first, with flags**, and every question after it comes in that language.
- **Education levels work worldwide**: secondary school, university, postgraduate, and professional or competitive exams (certifications, licences, *oposiciones*).
- **Dashboard re-organised**: achievements take one row of badges with the next one to unlock (the full list opens on demand), and a new "Your focus" card shows your strongest and weakest topics.
- The README is ready for a public repository, in English and Spanish.

### Added

- **Content map** in the dashboard: every unit and topic with its key concepts, coloured by progress.
- **Recap when you come back**: the session-start lines and the opening of each `/learn` session show what you covered last time, your strong and weak topics, your progress and today's plan.
- **Easier ways to add materials**: drag and drop, pasting photos or text in the chat, and `le.py find`, which lists recent study files in Downloads, Desktop and Documents to pick from.
- A notice before reading materials that the first read can take a few minutes and only happens once.
- **Six languages** for the dashboard, status lines and achievements: English, Spanish, French, German, Italian and Portuguese, in `scripts/i18n/` (one JSON file per language).
- `le.py notes <topic>` prints only the pages of the notes a topic needs.
- Renaming an exam, including its folder (`"rename": true` in `le.py init`), and naming an exam after its materials.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and pull request templates, and a GitHub Actions workflow that runs the tests.

### Token use

- The five action commands are not loaded into Claude's context until you type them.
- Study sessions and mock exams read the notes of one topic (about a tenth of a 40-page file) instead of whole files.
- Command output is compact JSON (`roadmap show` is about 40% smaller).

### Removed

- The old dashboard screenshot (a new one will come with the demo video).

## 0.1.0 — 2026-11-16

First version.

- One skill, `/learn`, with `setup`, `add`, `exam`, `dashboard` and `data` actions.
- Material ingestion (PDF, DOCX, PPTX, images, text) with hash-based deduplication and saved study notes.
- Roadmap of units and topics, and a daily plan that adapts to the exam date and daily intensity.
- Eight exercise types, SM-2 spaced repetition, mock exams.
- Streak with freezes, XP, levels and 16 achievements.
- Self-contained HTML dashboard with the roadmap map, activity, accuracy and review forecast.
- Backups before every write, export, restore, and delete-to-trash.
