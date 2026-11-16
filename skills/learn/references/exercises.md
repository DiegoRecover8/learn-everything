# Exercises: types, asking, grading

## Question types

Every question is stored as a card. `topic`, `type`, `prompt` and `answer` are required; `explanation` (why the answer is right) and `source` (`"s1 p.9"`) are strongly recommended.

| `type` | What it is | `answer` | Extra fields |
|---|---|---|---|
| `mcq` | Multiple choice, one correct | the correct option, copied exactly | `options`: 3 or 4 strings |
| `multi` | Multiple choice, several correct | list of correct options | `options`: 4 to 6 strings |
| `tf` | True or false | `true` or `false` | — |
| `cloze` | Fill the gap. The prompt marks it with `___` | the missing text | `accepted`: other valid wordings |
| `short` | Short answer: a word, a number, one sentence | the expected answer | `accepted`: other valid wordings |
| `essay` | Open question: explain, compare, solve a problem | a model answer | `rubric`: the key points a full answer must contain |
| `order` | Put steps or items in order | list in the correct order | — |
| `match` | Pair two columns | list of `[left, right]` pairs | — |

Favour the types that match the exam:

| `exam_type` | Mostly | Also |
|---|---|---|
| `test` | `mcq`, `multi`, `tf` | `cloze` |
| `essay` | `essay`, `short` | `cloze`, `order` |
| `problems` | `essay` (worked problems), `short` (numeric result) | `mcq` |
| `oral` | `short`, `essay` | `tf` |
| `mixed` | a balance of all | — |

## Writing good questions

- Take them from the notes. One idea per question.
- Ask for understanding and application, not only recall: "what happens if…", "why…", "which case applies…".
- Wrong options must be plausible: the mistakes a real student makes.
- Do not give the answer away in the prompt, and do not always put the right option in the same position.
- For `cloze`, hide the key term, not a filler word.
- For problems, use the data and notation of the materials, with new numbers.

## Asking

One question at a time, then wait.

- `mcq`, `tf`: use AskUserQuestion with the options, so the learner can click. Shuffle the options. Do not mark any option as recommended and do not put hints in the descriptions.
- `multi`: AskUserQuestion with `multiSelect`.
- `cloze`, `short`, `essay`, `order`, `match`: ask in the conversation and let them type. For `order` and `match`, show the items shuffled.

Show a light progress cue ("Question 3 of 8"). If the learner says "I don't know", do not push: count it as quality 0 and explain.

## Grading: quality 0 to 5

The script schedules the next review from this number, so grade honestly.

| Quality | Meaning |
|---|---|
| 5 | Correct, sure and immediate |
| 4 | Correct, with some hesitation or a slip in wording |
| 3 | Correct with effort, or right after a small hint, or mostly complete |
| 2 | Wrong or incomplete, but the right idea was close |
| 1 | Wrong; recognised the answer once shown |
| 0 | Blank or no idea |

3 or more counts as correct. Below 3 the question comes back tomorrow.

- `mcq`, `tf`, `multi`, `order`, `match`: correct is 5 (or 4 if they said they guessed); wrong is 1. A `multi`, `order` or `match` that is mostly right is 2.
- `cloze`, `short`: accept `answer`, anything in `accepted`, and any wording that means the same. Ignore accents, capitals and small typos unless spelling is the point. For numbers, accept sensible rounding and require the unit if the exam does.
- `essay`: compare against `rubric`. All key points and well reasoned: 5. One minor point missing: 4. About two thirds: 3. About half: 2. Less: 1.

## Feedback

Right after each answer, briefly:

```
✅ Correct!  (or ❌ Not quite.)
Why: one or two sentences with the reason, not just the fact.
Correct answer: … (only if they were wrong or incomplete)
📄 Source: Tema 1, p. 9
```

- When right: confirm and add one sentence that reinforces the reason. Do not over-explain.
- When wrong: say what part was right, name the mistake, give the correct answer and the reason. If it is a classic confusion, name it ("this mixes up field and potential").
- For `essay`: say which rubric points were covered and which were missing.
- Never just say "wrong". Never mock. Never inflate a wrong answer into a right one.
