# Contributing to Learn Everything

Thanks for wanting to help! Issues and pull requests are welcome in English or Spanish.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

- **Report a bug.** Open an issue with the bug template: what you did, what you expected, what happened, and the output of `python3 scripts/le.py --version`. Please remove anything personal from your notes or data before pasting it.
- **Suggest an idea.** Open an issue with the feature template. Say what problem it solves for someone studying for an exam.
- **Add a language.** See below. It is the easiest first contribution.
- **Improve the teaching.** The guides Claude follows are plain Markdown in `skills/`. Better explanations, exercise formats and feedback are very welcome.
- **Fix code or add tests.**

## Project principles

1. **Simple over clever.** Learners and contributors should understand how it works in a few minutes.
2. **No dependencies.** `scripts/le.py` uses the Python standard library only and must keep working on Python 3.8. The dashboard is one self-contained HTML file with no network requests.
3. **Claude teaches, the script counts.** Anything that must be computed the same way every time (schedules, streaks, XP, achievements) lives in `le.py`, never in the skill instructions.
4. **The learner's data is sacred.** Never modify their original files, write only inside the data folder, back up before every write, and ask before anything destructive.
5. **Few tokens.** Skill files are read on every use. Keep them short, and keep command output compact.

## Development setup

```bash
git clone https://github.com/DiegoRecover8/learn-everything.git
cd learn-everything
python3 -m unittest discover -s tests      # run the tests
claude --plugin-dir .                       # try the plugin without installing it
claude plugin validate .                    # check the manifests and skills
```

Tests run the real CLI against a temporary folder, so your own study data is never touched. To try commands by hand without touching it either, point the plugin at a scratch folder:

```bash
LEARN_EVERYTHING_HOME=/tmp/le-test python3 scripts/le.py status
LE_TODAY=2026-12-01 python3 scripts/le.py status --hook     # pretend it is another day
```

## Adding a language

The dashboard, the status lines at session start and the achievement names are translated in `scripts/i18n/`. One file per language, named with its two-letter code.

1. Copy `scripts/i18n/en.json` to `scripts/i18n/<code>.json` (for example `nl.json`).
2. Fill in `name` (in its own language), `flag`, `locale` (for dates, such as `nl-NL`) and `aliases` (how people may write the language name, lowercase).
3. Translate every value. Keep the `{placeholders}` exactly as they are, and keep the commands (`/learn`, `/learn:add`...) untranslated.
4. Run the tests: `test_every_language_pack_is_complete` checks that no key or placeholder is missing.
5. Add the language to the setup options in `skills/setup/SKILL.md` and to the language lists in both READMEs.

## Pull requests

- One topic per pull request, with a short description of the why.
- Add or update tests for any change to `le.py`.
- Run `python3 -m unittest discover -s tests` and `claude plugin validate .` before pushing.
- Update `CHANGELOG.md` under "Unreleased".
- If you change what users see or type, update both `README.md` and `README.es.md`.

## Releases

Maintainers bump the version in `scripts/le.py`, `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` (a test checks they match), move "Unreleased" in the changelog to the new version, and tag it as `vX.Y.Z`.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
