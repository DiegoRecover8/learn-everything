# Adding materials

Each file is read **once**. You turn it into study notes saved in the data folder, and from then on everything works from those notes. The original is never needed again and never modified.

Supported: PDF, DOCX, PPTX, JPG, PNG, WEBP, GIF, TXT, MD, CSV.

## 1. Check what is new

```bash
le.py source check "<file or folder>" ["<another>" ...]
```

Folders are scanned recursively. Each file comes back as `new`, `known` (already ingested, skip it), `unsupported` or `not_found`. If everything is `known`, say so and stop. Tell the learner how many files you will read before starting.

## 2. Read each new file

| Type | How |
|---|---|
| PDF | Read tool. More than 10 pages: read in ranges with `pages` (at most 20 per call) until the end |
| Image | Read tool. Photos of handwritten notes or a whiteboard are fine |
| DOCX, PPTX | `le.py extract "<file>"` prints the text. If it reports embedded images that matter (diagrams, formulas as pictures), rerun with `--media <folder inside the data home>` and Read them |
| TXT, MD, CSV | Read tool |

If a page is unreadable (bad scan, blurred photo), say which one and carry on. Do not guess its content.

## 3. Register the file

```bash
le.py source add <<'JSON'
{"path": "/abs/path/tema1.pdf", "title": "Tema 1: Electrostática", "summary": "One or two sentences on what it covers.", "pages": 24}
JSON
```

It returns the source `id` (for example `s1`) and `notes_path`.

## 4. Write the notes

Write the notes with the Write tool to `notes_path`. These notes replace the original, so they must be **complete and faithful**, not a summary of a summary:

- Follow the structure of the original, with headings.
- Keep every definition, formula, law, date, name, list, classification and worked example. Write formulas in plain text or LaTeX.
- Describe diagrams and figures in words: what they show and why they matter.
- Mark where things come from with `[p. 12]` or `[slide 7]` so you can cite them later.
- Keep the wording of anything likely to be asked literally (definitions, articles of a law).
- Do not add content that is not in the file. Do not leave out content because it looks secondary.
- Use the language of the original.

For a long file, write the notes as you go (one Write, then append by rewriting with more sections) instead of holding the whole document in memory.

Repeat steps 2 to 4 for each new file.

## 5. Build or update the roadmap

When all files are ingested, propose the roadmap. If a roadmap already exists, get it with `le.py roadmap show` and extend it; do not start over.

- **Units** are the big blocks (3 to 8). **Topics** are what one study sitting can cover (roughly 15 to 30 minutes each).
- Order topics so that each one builds on the previous ones.
- `weight`: how much the topic matters in the exam. 1 is normal, 2 is important, 3 is central. Use the learner's hints (past exams, what the teacher stressed) when the materials give them.
- `sources`: where the topic is covered, each starting with the source id: `"s1 p.3-8"`, `"s2"`.
- `summary`: one sentence on what the topic covers.
- Cover everything in the materials. Do not add topics the materials do not cover.

Show the proposal as a short outline and ask if it looks right (order, missing topics, weights). Then save it:

```bash
le.py roadmap set <<'JSON'
{"units": [
  {"name": "Electrostática", "topics": [
    {"name": "Ley de Coulomb", "weight": 2, "sources": ["s1 p.1-6"], "summary": "Fuerza entre cargas puntuales."},
    {"name": "Campo eléctrico", "weight": 2, "sources": ["s1 p.7-15"], "summary": "Campo creado por distribuciones de carga."}
  ]}
]}
JSON
```

Topics that keep their name (or carry their `id`) keep their progress. If the command refuses because a removed topic has questions, keep that topic, or ask the learner before using `--drop`.

## 6. Close

Tell the learner in two or three lines: how many files and topics, the daily plan the command returned (`new_per_day`, `days_left`), and whether `overload` is true. If it is, say plainly that the plan is tight and suggest more daily time or trimming low-weight topics. Then offer to start the first session now.
