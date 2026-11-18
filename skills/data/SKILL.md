---
name: data
description: Learn Everything — list or switch exams, back up, export, restore or delete your study data.
disable-model-invocation: true
allowed-tools: Read, Bash, AskUserQuestion
---

# Managing data

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory; type the full command each time, never through a shell variable such as `$LE`, which zsh does not split into words). It prints JSON; errors come as `{"error": ...}` on stderr. Speak the learner's language (`learner.language` from `le.py status`).

Everything lives in one folder: `~/learn-everything/`, or the folder in the `LEARN_EVERYTHING_HOME` environment variable. `le.py status` prints it as `home`.

```
profile.json                 shared by all exams: learner, XP, study days, streak freezes, achievements
<exam>/                      one folder per exam or subject, independent of the others:
  exam.json                    settings and roadmap
  cards.json                   questions and their review schedule
  log.jsonl                    one line per session
  sources/index.json           list of ingested materials
  sources/<id>.md              study notes made from each material
  dashboard.html               this exam's dashboard
.backups/                    automatic copies, last 10 writes
.trash/                      deleted exams and notes
.cache/                      page images and long extracted texts, cleared after two weeks
exports/                     zip exports
```

What is shared and what is not: the learner, XP, level, streak and achievements are one for everyone (studying any exam keeps the streak). Everything about an exam (roadmap, questions, notes, plan, sessions, dashboard) lives in its folder and never mixes with another. Several Claude Code windows can study different exams at the same time: each one passes `--exam <slug>`, and writes take turns so no XP is lost.

Statistics (streak, level, accuracy, topic status, daily plan) are computed from these files each time. They are not stored, so they cannot go out of sync.

## Commands

Ask the learner what they want (AskUserQuestion with the most likely options), then run the matching command. Add `--exam <slug>` before the command to act on an exam that is not the active one.

| Need | Command |
|---|---|
| See all exams | `le.py list` |
| Change the active exam (the one the session-start lines and `/learn` offer first) | `le.py switch <slug>` |
| Translate unit, topic and material names (after a language change) | `le.py labels show`, then `le.py labels set` (see `../setup/SKILL.md`) |
| Edit exam settings or profile | `/learn:setup` (see `../setup/SKILL.md`) |
| Rename an exam (and its folder) | `le.py init` with `{"exam": {"slug": "<slug>", "name": "<new name>", "rename": true}}` |
| See the roadmap with progress | `le.py roadmap show` |
| See the materials | `le.py source list` |
| Remove a material | `le.py source remove <id>` (notes go to `.trash`; questions and roadmap stay) |
| See stored questions | `le.py cards list [--topic <id>]` (answers are not shown) |
| Make a backup now | `le.py backup` |
| Export an exam to a zip | `le.py export [--out <file.zip>]` |
| Restore an export | `le.py restore <file.zip>` (add `--force` to replace an existing exam) |
| Delete an exam | `le.py delete <slug> --confirm <slug>` |

## Safety rules

- **Ask before anything destructive**: `delete`, `restore --force`, `source remove`, `roadmap set --drop`. Say exactly what will be removed and wait for a clear yes.
- `delete` moves the exam to `.trash/`; it does not erase it. XP, streak and achievements are kept. To undo, move the folder back from `.trash/`. To free the space, the learner empties `.trash/` themselves.
- `restore` never overwrites the current profile, so the live streak and XP are safe.
- Before a risky change, offer `le.py export` first.
- A backup is taken automatically before every write. To roll back, copy the files from the newest folder in `.backups/` over the current ones, after confirming with the learner.
- Do not edit the JSON files by hand, and do not touch anything outside the data folder.

## Moving to another computer

`le.py export` on the old one, copy the zip, `le.py restore <zip>` on the new one. The zip contains the exam, its notes and the profile; the profile is restored only if the new computer has none.
