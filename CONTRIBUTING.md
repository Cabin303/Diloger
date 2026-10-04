# Contributing to Diloger

Thanks for helping. This is a small local macOS app with a few rules that keep
it that way.

## The two rules that matter

**1. Never commit user content.**

Recordings, transcripts, `config.json`, session folders, and screenshots of the
running app stay on the machine that made them. This is a hard requirement, not
a style preference — the app promises local-only processing.

`.gitignore` already covers `*.wav`, `*.aiff`, `sessions/`, `config.json`,
`logs/` and `.probe/`. If something slips past it, that is a bug in
`.gitignore`; fix that rather than removing the file from the ignore list.

Do not put real absolute paths or real file names into source, tests or docs.
Tests use obviously synthetic fixtures (`/Users/x/Desktop/...`), and anything
that must vary per machine is derived at runtime from `$HOME` or an environment
variable.

**2. Do not make the app a judge.**

Diloger does not evaluate the user's grammar, vocabulary, pronunciation,
correctness or intent, and must not start. It does not choose the next prompt
from an answer, and it does not run an LLM. A change that scores or interprets
user speech is out of scope even if it would be useful.

Cleaning up sentence boundaries and capitalisation for readability is fine.
Changing someone's words is not.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Or just run `./Diloger.command`, which does this for you.

## Before you open a pull request

```sh
.venv/bin/python -m pytest -q
```

Then run the probes your change could plausibly affect. They drive real QML and
the real audio stack, so they catch what unit tests cannot:

| If you touched | Run |
| --- | --- |
| transcript, History, Finished, export | `tools/verify_transcript_view.py` |
| Settings | `tools/verify_settings.py` |
| PlaybackBar, layout, themes | `tools/verify_playback_geometry.py`, `tools/verify_playback_behaviour.py` |
| colours, selection, typography | `tools/verify_theme_render.py` |
| recorder, player, WAV handling | `tools/verify_audio_integrity.py` |

All of them run headless with `QT_QPA_PLATFORM=offscreen` and print one line per
check. They are also not part of `pytest`; run them yourself.

Please say in the pull request which probes you ran and what they printed. A
claim without output is not evidence.

## Style

Match the surrounding code. Comments should explain **why** a non-obvious thing
is the way it is — especially where a workaround exists for a specific platform
bug. `whisper_cli.py` and `recorder.py` both document platform quirks that would
otherwise look like mistakes and get "fixed" away.

Keep domain logic free of Qt and QML. If a change needs `from PySide6...` inside
`domain/`, the logic probably belongs in an adapter instead.

## Commit messages

Write what the change does and why. Short imperative subject, blank line, then
context if the reason is not obvious from the diff. Mention the probe output you
relied on when the change was hard to verify by reading.