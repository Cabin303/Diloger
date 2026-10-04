# Universal Dialogue Trainer — operational context

## Purpose

Universal local macOS trainer for spoken and written practice. The app presents user-provided English prompts, gives the user time to answer, and optionally keeps a local record for later listening and transcription.

It is a trainer, not an AI tutor, chatbot, grammar checker, pronunciation evaluator, or answer grader.

## Non-negotiable principles

- Do not use real-time STT, VAD-based turn detection, or Whisper during a session.
- Do not analyse the meaning, grammar, vocabulary, pronunciation, correctness, or intent of user answers.
- Do not generate dialogue with an LLM or choose the next prompt from the answer.
- Use existing local Whisper models when possible; never download models automatically.
- Keep user content, audio, transcripts, and configuration local.
- Keep user content separate from application code.
- A session must work without recording.
- Recording is opt-in and uses temporary storage by default; delete temporary audio after successful post-session transcription unless the user selected Keep recording.
- English is the only supported language for the first version.

## Current recommended stack

- Python 3.11+ in a project virtual environment.
- PySide6 for the official Qt for Python bindings.
- Qt Quick/QML for the visual UI; Python owns domain logic and services.
- Qt Multimedia/Core Audio integration for microphone recording and playback, validated on macOS.
- TTS provider abstraction. First provider: macOS say using the installed Kokoro voices Kokoro Heart and Kokoro Michael. Direct Kokoro/GUI integration is optional research, not a dependency.
- Local whisper-cli (whisper.cpp) invoked after a session as a controlled subprocess.
- JSON for session metadata and event log; Markdown for human-readable transcript/export.

PyQt6 is not the default because PySide6 has the more suitable official Qt for Python/LGPL-oriented distribution path for this project. Tauri and Electron are not the MVP stack: they add a web frontend and bridge/runtime complexity that is not needed for the macOS-first application.

Do not embed Whisper into business logic. A future embedded whisper.cpp adapter may be considered only after measuring subprocess latency and packaging cost.

## Local Whisper models

Models are local files, never downloaded by the app. Conventional locations are
probed when Settings has no model path yet (`~/whisper/models`,
`~/.cache/whisper.cpp`, `~/models/whisper`, or `$DILOGER_WHISPER_MODELS`):

- ggml-large-v3-turbo.bin
- ggml-large-v3.bin

The configured model path must remain user-editable. Do not assume the paths exist at runtime. Prefer ggml-large-v3-turbo.bin as the first test candidate because it is smaller, but do not silently select it when the user configured another model.

Runtime/model compatibility must be checked at application startup or before transcription.

## Content rules

- The application scans a user-selected Library folder.
- A TXT or MD file is also accepted by drag-and-drop for a one-off session.
- MVP parsing rule: each non-empty line is one spoken prompt or utterance; blank lines are ignored.
- No required headings, sections, YAML front matter, speaker markup, or database editor.
- Only counterpart prompts are spoken. The user's answer is never supplied as a branching input.
- Keep the importer independent from UI and Session Engine.

## Modes

### Spoken Mode

The prompt is spoken. Its text is hidden by default and can be temporarily shown with Reveal. Controls: Repeat, Reveal, Skip, Pause, Resume, Stop, and Next where applicable.

Two progression modes:

- Fixed Timer: after TTS completion, wait the configured duration, then advance. The timer does not try to detect whether the user stopped speaking.
- Manual Advance: wait until the user presses Next.

The session continues until the user stops it. When the selected list ends, stay on the last item and wait for an explicit user decision; never silently loop.

### Written Mode

The user types an answer. Written Mode has two prompt variants:

- Audio Prompt: prompt is spoken only; prompt text is hidden.
- Text Prompt: prompt is shown as text; audio is optional/not required.

Saving typed answers is a user setting. It must not be assumed merely because Written Mode was used.

## Audio pipeline

Spoken prompt: content item → TTS provider → playback completion event → timer or manual waiting.

Optional recording: microphone → local temporary WAV → session end → Whisper → transcript → delete temporary WAV unless Keep recording was selected.

Recording must be independent from TTS, prompt progression, and Whisper. It must not wait for speech detection. Keep the event log with prompt start, TTS completion, repeat, skip, pause/resume, next, and session end timestamps.

## Storage

Suggested user-selectable directories:

- content/ or an externally selected Library folder for source files;
- sessions/YYYY-MM-DD_HH-mm-ss_id/ for retained session data.

Do not create a permanent session directory for an unrecorded, unsaved session unless the user explicitly enables saving. The exact retention policy belongs in the settings UI and must be visible before a recording starts.

## Privacy and errors

All processing is local by default. Handle missing microphone permission, missing model, invalid runtime, unavailable TTS, disk-full, interrupted recording, malformed content, and cancelled transcription as recoverable states with a clear message and no data loss where possible.

Never hide a failed transcription behind an empty transcript. Preserve the recording when the user selected Keep recording or when deletion would risk losing an unprocessed recording.

## MVP boundary

Include: Library folder, drag-and-drop TXT/MD, line-based importer, Spoken Mode, Written Mode, Fixed Timer, Manual Advance, TTS, Repeat/Reveal/Skip/Pause/Resume/Stop/Next, optional recording, temporary-audio retention policy, post-session local Whisper, Markdown transcript, save/export, and editable settings.

Defer: semantic evaluation, grammar/pronunciation scoring, real-time STT, cloud services, accounts, sync, database backend, gamification, automatic silence detection, complex Markdown structure, branching dialogues, mobile/Windows support, and automatic model downloads.

## Development rules

- Keep domain models and state transitions independent of QML views.
- Use protocols/adapters for content import, TTS, recording, transcription, and storage.
- Prefer explicit state machines and event logs over implicit UI callbacks.
- Add tests for parser behavior, session transitions, timer anchoring, retention, and error recovery before visual polish.
- Do not add a feature only because a local model or framework makes it possible.

## Documentation

The current concept and architectural decisions live in docs/concept.md.

For the current implementation and correction task, read
`docs/agent-task-current.md`. It is the authoritative ordered brief for fixing
the current UI, transcript formatting, Finished/History actions, export flow,
and theme consistency before adding new features.

The latest dated correction task is `docs/agent-task-2026-10-04.md`. It supersedes
the older task for the current GUI audit, especially the P0 PlaybackBar clipping
that hides Play/Pause/Stop in Finished and History.

The newest revision task is `docs/agent-task-2026-10-04-revision.md`. It is the
current operational brief for the next agent and supersedes earlier correction
briefs where they conflict, especially on readable transcript formatting,
screen-vs-Markdown separation, typed-answer checkbox semantics, and GUI
acceptance evidence. Preserve the existing uncommitted work and follow its
ordered implementation and smoke-check requirements.

The newest transcript-specific task is
`docs/agent-task-2026-10-04-transcript-turns.md`. It is mandatory for the next
transcript correction: the result must be grouped as Turn -> Question -> Your
answer, using post-session answer windows derived from the event log. Do not
accept a chronological list of mixed Whisper segments as a finished solution.
