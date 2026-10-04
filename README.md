# Diloger

A local macOS trainer for spoken and written dialogue practice.

Diloger plays English prompts from your own text files, gives you time to answer,
and keeps an optional local record you can listen to and transcribe later. It is
a practice timer and recorder — **not** a tutor. It never grades, corrects,
interprets or scores what you say.

Everything runs on your machine. There is no account, no sync, and no network
call in the app.

## What it does

- **Spoken mode** — the prompt is spoken aloud and the text stays hidden until
  you reveal it. Advance on a fixed timer or manually.
- **Written mode** — answer by typing, with an audio prompt or a text prompt.
- **Local transcription** — after a session, your recording is transcribed on
  this machine with a local Whisper model, then formatted into readable turns.
- **History and export** — browse past sessions and export a Markdown
  transcript.

## What it deliberately does not do

Diloger will not:

- judge your grammar, vocabulary, pronunciation or intent;
- use an LLM to talk with you or pick the next prompt;
- transcribe in real time or detect when you stop speaking;
- download models, or send anything to a server.

A wrong word stays the wrong word. That is a property of the recording, not a
mistake to be tidied away.

## Requirements

- macOS with a working microphone and speakers
- Python 3.11 or newer
- PySide6 (installed automatically by the launcher)
- macOS `say` for speech (built in)
- Optional: a local [whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  runtime and GGML model for post-session transcription

Transcription is optional. Without Whisper the app still runs a full session; you
simply get no transcript.

## Getting started

```sh
git clone <your-fork-url> Diloger
cd Diloger
./Diloger.command
```

`Diloger.command` creates `.venv`, installs the project, and starts the app. It
never hides diagnostics: failures are printed and written to `logs/launch.log`,
and the Terminal window is held open until you acknowledge the error.

To run it yourself instead:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m diloger.app.main
```

To check your setup — QML, audio input, TTS and Whisper — and exit:

```sh
.venv/bin/python -m diloger.app.main --check
```

## Adding practice material

Point the app at a Library folder of `.txt` or `.md` files, or drag a single
`.txt`/`.md` file onto the window.

Each non-empty line is one prompt. Blank lines are ignored. That is the entire
format:

```text
What did you do yesterday?
Where are you going tomorrow?
Why did you choose this job?
```

No headings, front matter, speaker tags or database needed.

## Where your data lives

Nothing is uploaded. Settings chooses the two folders:

- **Library** — your source `.txt`/`.md` files.
- **Sessions** — retained session data, one folder per session.

Sessions are written to `~/Library/Application Support/Diloger`. Each retained
session can contain:

| File | Purpose |
| --- | --- |
| `session.json` | metadata, prompts, settings used, event log |
| `recording.wav` | mono, 16-bit, 16 kHz — the format Whisper wants |
| `whisper.json` | whisper-cli's own output, kept verbatim |
| `transcript.json` | raw Whisper segments plus a readable rendering |
| `transcript.md` | the same readable transcript, for reading and export |

Audio is written to temporary storage first and deleted after a successful
transcription unless you chose **Keep recording**. If transcription fails, the
recording is kept — losing unprocessed audio is never silent.

### Raw vs. readable transcript

`transcript.json` keeps two views of the same audio on purpose:

- **raw segments** — exactly what Whisper returned, verbatim and uncorrected;
- **readable blocks** — the same words, joined into sentences and grouped into
  `Question` / `Recorded answer` turns for reading.

Sentence boundaries and capitalisation are tidied up. **Words are not.** The app
does not evaluate meaning, so it cannot tell you what you meant to say — only
what was recorded. Where the app cannot confidently attribute audio to you, it
says `Recorded speech` rather than guessing.

Turn boundaries come from the session's own event log, so a question and the
answer that followed it stay together.

## Development

```sh
.venv/bin/python -m pytest -q          # unit tests
```

Beyond the unit tests, `tools/` holds verification probes. They drive the real
QML and the real audio stack rather than asserting against mocks, so they catch
the layout, contrast and format problems that unit tests cannot see:

```sh
.venv/bin/python tools/verify_transcript_view.py      # History and Finished render the same blocks
.venv/bin/python tools/verify_settings.py             # Settings layout on both themes
.venv/bin/python tools/verify_playback_geometry.py    # playback controls are not clipped
.venv/bin/python tools/verify_playback_behaviour.py   # navigation stops playback
.venv/bin/python tools/verify_theme_render.py         # text contrast, including selection
.venv/bin/python tools/verify_audio_integrity.py      # canonical WAV, and Play rewrites nothing
```

Probe output, recordings and screenshots land in `.probe/`, which is ignored by
Git. Nothing under `.probe/` should ever be committed: it can contain real
recordings and screenshots of real content.

### Architecture

Domain logic and state transitions are independent of QML. Adapters sit behind
protocols for content import, TTS, recording, transcription and storage.

- `src/diloger/domain/` — models and state, no Qt
- `src/diloger/content/` — line-based importer
- `src/diloger/audio/` — recorder, player, WAV conversion
- `src/diloger/transcription/` — whisper.cpp adapter, readable formatting
- `src/diloger/storage/` — sessions, raw/readable split, Markdown export
- `src/diloger/qml/` — views only

`docs/concept.md` and `docs/implementation-spec.md` describe the design and the
decisions behind it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Two rules matter more than the rest:

1. **Never commit user content.** No recordings, transcripts, `config.json`,
   session folders or screenshots of real content.
2. **Do not make the app a judge.** If a change starts evaluating what the user
   said, it does not belong here.

## License

[MIT](LICENSE)