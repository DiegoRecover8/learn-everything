---
name: add
description: Learn Everything — load study materials (PDF, Word, PowerPoint, Jupyter notebooks, photos, text) and build or extend the roadmap.
argument-hint: "[file or folder]"
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Bash, Glob, AskUserQuestion
---

# Adding materials

Each file is read **once**. You turn it into study notes saved in the data folder, and from then on everything works from those notes. The original is never needed again and never modified.

**Basics.** The script is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/le.py"` (written `le.py` below; if `CLAUDE_PLUGIN_ROOT` is empty it is `../../scripts/le.py` from this skill's base directory; type the full command each time, never through a shell variable such as `$LE`, which zsh does not split into words). It prints JSON; errors come as `{"error": ...}` on stderr. The rules in `../learn/SKILL.md` ("Rules that always apply") hold here too. Run `le.py status` first if you do not know the learner's language yet; if there is no profile, follow `../setup/SKILL.md` first. If `exams` lists several and you do not know which one the materials are for, ask, then use `--exam <slug>` on every command.

Supported: PDF, DOCX, PPTX, IPYNB (Jupyter), RTF, JPG, PNG, WEBP, GIF, TXT, MD, CSV.

## 1. Get the files

Use whatever the learner already gave you:

- **Paths** in the command or the message. A file or folder dragged into the terminal arrives as its path (spaces may be escaped with `\`).
- **Attached files**: an attached file whose path you can see is a path like any other.
- **Images or text pasted into the chat** with no file behind them: they are already in the conversation. Go to step 4 and register them with `"pasted": true`.

If there is nothing yet, ask with AskUserQuestion:

- "📎 Drag them here": tell them to drag the files or a whole folder into this window and press Enter.
- "🔍 Find recent files": run `le.py find`. It lists recent supported files in Downloads, Desktop and Documents (`le.py find <folder>` searches elsewhere). Show them as a short numbered list (file name, folder, date) and let them answer with numbers, "all" or a folder. Files with `known: true` are already loaded.
- "📋 Paste them in the chat": photos (Ctrl+V) or text.

## 2. Check what is new

```bash
le.py source check "<file or folder>" ["<another>" ...]
```

Folders are scanned recursively. Each file comes back as `new`, `known` (already loaded, skip it), `unsupported` or `not_found`. If everything is `known`, say so and stop.

**Before reading, warn the learner**, in their language and in one or two lines: how many files you are about to read, that this first read can take a few minutes (longer for long PDFs), and that it happens only once, because later sessions use the notes you save. Then start without waiting for an answer.

## 3. Read each new file

| Type | How |
|---|---|
| PDF | `le.py extract "<file>"` (see below). Do not open the PDF with the Read tool: on some computers it needs extra software, and page images cost many more tokens than text |
| DOCX, PPTX, IPYNB, RTF | `le.py extract "<file>"` prints the text (notebooks: Markdown, code and outputs, with `[cell N]` marks). If it reports `embedded_images` that matter (diagrams, plots, formulas as pictures), rerun with `--media "<home>/.cache/media/<file name>"` and Read them |
| Image | Read tool. Photos of handwritten notes or a whiteboard are fine |
| TXT, MD, CSV | Read tool |

**PDFs.** `le.py extract` needs nothing installed: it uses poppler if present, macOS's own PDF reader, or a built-in one.

- It prints the text with a `[p. N]` mark per page, then a `--- le.py:` line with the page count. When the text is too long for one output, it saves it and prints `text_file` instead: read that file with the Read tool in parts (offset and limit), never all at once.
- For a very long PDF you can also go in chunks: `--pages 1-40`, `--pages 41-80`…
- `little_text` lists pages with almost no text: scans, photos, slides that are mostly pictures. Turn the ones that matter into images with the `render` command it suggests (`le.py render "<file>" --pages 4,9-11`, at most 20 per call) and Read each image. Do the same for any page whose text mentions a figure, chart, diagram or formula that did not come through.
- A scanned PDF (every page in `little_text`) is read entirely through `render`, 20 pages at a time.

If a page is unreadable (bad scan, blurred photo), say which one and carry on. Do not guess its content. For several files, give a one-line progress note between files ("2 of 5 done").

## 4. Register the file

```bash
le.py source add <<'JSON'
{"path": "/abs/path/tema1.pdf", "title": "Tema 1: Electrostática", "summary": "One or two sentences on what it covers.", "pages": 24}
JSON
```

For pasted material, send `{"pasted": true, "title": "...", "summary": "..."}` instead of `path`. Write `title` and `summary` in the learner's language (the dashboard lists them).

It returns the source `id` (for example `s1`) and `notes_path`.

## 5. Write the notes

Write the notes with the Write tool to `notes_path`. These notes replace the original, so they must be **complete and faithful**, not a summary of a summary:

- Follow the structure of the original, with headings.
- Keep every definition, formula, law, date, name, list, classification and worked example. Write formulas in plain text or LaTeX.
- Describe diagrams and figures in words: what they show and why they matter.
- Mark where things come from with `[p. 12]`, `[slide 7]` or `[cell 4]` at the start of each page's content (keep the marks `le.py extract` printed), so that `le.py notes` can later pull out just the pages a topic needs.
- Keep the wording of anything likely to be asked literally (definitions, articles of a law).
- Do not add content that is not in the file. Do not leave out content because it looks secondary.
- Use the language of the original.

For a long file, write the notes as you go instead of holding the whole document in memory: the first part with the Write tool, then each next part appended in Bash with `cat >> "<notes_path>" <<'EOF'` … `EOF`. To change something already written (a heading, a page range), use the Edit tool. Do not use `sed -i`: it takes different arguments on macOS and Linux and fails on one of them.

Repeat steps 3 to 5 for each new file.

## 6. Build or update the roadmap

When all files are in, propose the roadmap. If one already exists, get it with `le.py roadmap show` and extend it; do not start over.

- **Units** are the big blocks (3 to 8). **Topics** are what one study sitting can cover (roughly 15 to 30 minutes each).
- Order topics so that each one builds on the previous ones.
- `weight`: how much the topic matters in the exam. 1 is normal, 2 is important, 3 is central. Use the learner's hints (past exams, what the teacher stressed) when the materials give them.
- `sources`: where the topic is covered, each starting with the source id and with page ranges whenever you know them: `"s1 p.3-8"`, `"s2 slide 4-9"`, `"s3"` (whole file).
- `summary`: one sentence on what the topic covers.
- `concepts`: 3 to 6 key terms, a few words each. They make up the content map of the dashboard.
- Write unit and topic names, summaries and concepts **in the learner's language**, whatever the language of the materials: the dashboard shows them. The notes themselves stay in the original language.
- Cover everything in the materials. Do not add topics the materials do not cover.

Show the proposal as a short outline and ask if it looks right (order, missing topics, weights). Then save it:

```bash
le.py roadmap set <<'JSON'
{"units": [
  {"name": "Electrostática", "topics": [
    {"name": "Ley de Coulomb", "weight": 2, "sources": ["s1 p.1-6"], "summary": "Fuerza entre cargas puntuales.",
     "concepts": ["carga puntual", "constante de Coulomb", "superposición"]},
    {"name": "Campo eléctrico", "weight": 2, "sources": ["s1 p.7-15"], "summary": "Campo creado por distribuciones de carga.",
     "concepts": ["líneas de campo", "dipolo", "distribuciones continuas"]}
  ]}
]}
JSON
```

Topics that keep their name (or carry their `id`) keep their progress. If the command refuses because a removed topic has questions, keep that topic, or ask the learner before using `--drop`.

If the exam still has a provisional name (the learner chose "Name it from my materials" in setup), propose two or three names drawn from the materials with AskUserQuestion, then save the choice with `le.py init` and `"exam": {"slug": "<slug>", "name": "<name>", "rename": true}`.

## 7. Close

Tell the learner in two or three lines: how many files and topics, the daily plan the command returned (`new_per_day`, `days_left`), and whether `overload` is true. If it is, say plainly that the plan is tight and suggest more daily time or trimming low-weight topics. Then offer to start the first session now (`/learn`).
