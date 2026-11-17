# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's private reporting instead: the **Security** tab of this repository, then **Report a vulnerability**. If that option is not available, contact the maintainer privately through [@DiegoRecover8](https://github.com/DiegoRecover8).

Include what you found, how to reproduce it and what an attacker could do with it. You will get an answer as soon as possible, and credit in the changelog if you want it.

## What matters most here

Learn Everything reads files the learner chooses and keeps their study data on their own computer. Reports about any of these are especially welcome:

- writing or deleting anything outside the data folder (`~/learn-everything/` or `LEARN_EVERYTHING_HOME`);
- modifying the learner's original files;
- a crafted export zip, PDF, notebook, document or notes file that makes `le.py` write outside its folder, run code or hang;
- the dashboard loading anything from the network or running injected script.
