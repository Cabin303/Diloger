# Universal Dialogue Trainer — concept and architecture

Status: design baseline for the first implementation stage. This document intentionally records decisions and boundaries, not application code.

## 1. Product concept

Universal Dialogue Trainer is a local macOS application for practising spoken and written responses to user-provided English prompts.

The central loop is deliberately simple:

    present a counterpart prompt
            ↓
    user answers aloud or types
            ↓
    user or timer advances the session
            ↓
    present the next prompt
            ↓
    optionally transcribe the recording after the session

The product does not try to be a teacher. Its value is repetition, controlled time pressure, exposure to spoken prompts, and the ability to keep or export the user's own material.

## 2. Core principles

1. The user supplies the content.
2. The application presents prompts; it does not judge answers.
3. No AI dialogue generation and no semantic branching.
4. No real-time speech recognition.
5. Recording is optional.
6. All processing is local by default.
7. The simplest predictable interaction wins over clever automation.
8. Every automatic action must be visible and interruptible.

The application must never claim that an answer is correct, incorrect, fluent, grammatical, well-pronounced, relevant, or understood.

## 3. User flow

### Library

The user selects a Library folder. The app shows TXT and MD files as available content sets. A file can also be dragged onto the app to start a one-off session without importing it into the Library.

### Session setup

The user chooses:

- content file;
- Spoken or Written mode;
- sequential or random-without-repetition order;
- for Spoken Mode: Fixed Timer or Manual Advance;
- for Fixed Timer: delay duration;
- for Written Mode: Audio Prompt or Text Prompt;
- whether to record audio;
- whether to keep the recording after transcription;
- whether to save typed answers;
- TTS voice and speed when audio is used.

The first version should not require a question count or session duration. A session runs until the user presses Stop or the selected content is exhausted.

### Training

The training screen is intentionally sparse. It shows state rather than the prompt text:

- item position, if useful;
- mode and progression mode;
- recording indicator, when recording is enabled;
- timer/progress indicator in Fixed Timer mode;
- Repeat, Reveal, Skip, Pause/Resume, Next, and Stop as applicable.

The prompt text is hidden in Spoken Mode by default. Reveal is an explicit temporary aid. In Text Prompt Written Mode, the prompt is visible by definition.

### Finish

The minimal finish flow reports what actually happened: session duration, number of prompts reached, recording/transcription status, and available exports. A detailed history dashboard is not required for the first usable build.

## 4. UX decisions

### Speech-first interaction

The user should be able to look away from the screen. Keyboard shortcuts and large on-screen controls are both required. Suggested initial shortcuts:

| Action | Suggested key |
|---|---|
| Pause/Resume | Space |
| Next | Return |
| Repeat | R |
| Reveal | V |
| Skip | S |
| Stop | Escape |

Shortcuts must be configurable or documented, and must not fire while a text field is focused in Written Mode.

### Repeat, Reveal, Skip

- Repeat replays the current counterpart prompt. It does not create a new prompt.
- Reveal temporarily exposes the current prompt text and can be dismissed without changing the session.
- Skip records a skip event and advances without attempting to interpret the answer.

Default assumption: Repeat restarts the Fixed Timer after the repeated TTS finishes. Keep this behind the Session Engine, not inside the view.

### End of content

When the last item has been reached, the app stops advancing and waits. It does not silently wrap around or invent a repeat. The user can stop, repeat the last prompt, or explicitly start a new pass later.

## 5. Modes

### Spoken Mode / Fixed Timer

    load prompt
      → speak prompt
      → wait for TTS completion
      → start configured timer
      → advance to next prompt

The timer begins after TTS completes, not when synthesis starts. This gives a stable and explainable definition of the user's answer window. The timer does not inspect the microphone and may advance while the user is still speaking; that is an intentional time-pressure mode.

Recommended initial delay presets: 1, 2, 3, 5, 10, 15 seconds plus a custom value.

### Spoken Mode / Manual Advance

    load prompt
      → speak prompt
      → wait indefinitely
      → user presses Next
      → advance

Next is the user's declaration that the response is finished. The application does not infer completion.

### Written Mode

Written Mode has two explicit prompt variants:

- Audio Prompt: the question is spoken and hidden; the answer is typed.
- Text Prompt: the question is displayed; the answer is typed.

Typed answer persistence is a setting. If disabled, the answer field is cleared/discarded at the end and is not put into the transcript. If enabled, the typed answer is stored as user-authored text without correction.

## 6. Content model

### User-facing format

The first version deliberately supports a line-oriented model in both TXT and MD:

    What did you do yesterday?
    Where are you going tomorrow?
    Why did you choose this job?

Rules:

- one non-empty line = one prompt;
- blank lines are ignored;
- leading/trailing whitespace is trimmed;
- no headings, sections, speaker labels, YAML, or required Markdown syntax;
- UTF-8 is preferred; invalid encoding produces a clear import error.

Markdown is initially accepted as a convenient text extension, not treated as a full document language. A later parser can add richer structure without changing the internal prompt interface.

Only counterpart prompts are spoken. The next prompt is selected by order/randomisation, never by the answer.

### Internal representation

    ContentSet
      id
      sourcePath
      displayName
      format
      prompts[]

    Prompt
      id
      ordinal
      text

Do not add topic, category, speaker, branch, or expected-answer entities until a real use case requires them.

## 7. Session model

    Session
      sessionId
      startedAt
      endedAt
      contentSourcePath
      contentFingerprint
      mode: spoken | written
      spokenProgression: fixedTimer | manual | null
      writtenPrompt: audio | text | null
      order: sequential | randomWithoutRepetition
      configuredDelaySeconds
      promptOrder[]
      promptsReached
      recordingEnabled
      keepRecording
      saveTypedAnswers
      ttsProvider
      ttsVoice
      ttsRate
      whisperModelPath
      recordingPath
      transcriptPath
      status
      eventLog[]

The event log is the source for technical timing, not an attempt to infer meaning. Useful events include sessionStarted, promptLoaded, ttsStarted, ttsFinished, repeat, revealed, skipped, timerStarted, nextPressed, paused, resumed, sessionStopped, contentExhausted, and sessionFinished.

## 8. Session Engine

The engine is a platform-independent state machine. QML renders its state but does not own transitions; Python owns the state transitions and service orchestration.

Suggested states:

    idle
    loading
    speakingPrompt
    waitingForManualAdvance
    waitingForTimer
    paused
    contentExhausted
    finishing
    finished
    failed

The engine owns progression, timer anchoring, repeat/skip semantics, and event logging. TTS reports completion to the engine. The recorder reports availability and file state. Whisper is not a dependency of the engine and cannot block a live transition.

## 9. Audio architecture

There are two independent audio paths.

Prompt playback:

    Prompt text → TTS provider → playback → completion callback

Optional user recording:

    Microphone → Qt Multimedia/Core Audio recorder → temporary WAV
                                       ↓
                                 session finished
                                       ↓
                                 Whisper backend

Recording starts only after the user enables it before the session. It continues independently of prompt playback and timer/manual progression. It is not used for turn detection.

Keep the recording path in a stable PCM format suitable for Whisper, preferably mono 16 kHz WAV. The application should not rely on TTS files as Whisper input.

## 10. TTS

### Finding

The supplied macOS installation contains both /Applications/KokoroVoice.app and /Applications/kokoro-clipboard-tts.app. The system say -v '?' command exposes Kokoro Heart and Kokoro Michael as English voices. A local test successfully generated an AIFF file with Kokoro Heart.

### Recommendation

Use a TTSProvider protocol and make the first provider the macOS speech command/API configured to use the installed Kokoro voices. This uses the desired voice family without coupling the trainer to a GUI clipboard workflow.

Provider responsibilities:

- speak text;
- stop current speech;
- report completion/failure;
- list or validate available voices;
- set voice and rate;
- optionally render/cache a prompt to a local audio file.

The application should first attempt a direct local Kokoro integration only if it provides a stable callable command/API. The installed GUI apps are not a required dependency. If direct integration proves more stable, add it as another provider. If Kokoro is unavailable, fall back to a selected native macOS English voice.

TTS caching is optional. Start with dynamic playback because prompts are short and caching introduces lifecycle and storage decisions. Add a bounded cache only if repeated playback latency is visibly disruptive.

## 11. Recording

Recording is disabled by default. The user explicitly enables it before starting a session.

Default retention policy:

1. Record to a temporary session file.
2. If the session ends normally and transcription succeeds, delete the temporary WAV.
3. If Keep recording was selected, move/copy the WAV into the session export folder.
4. If recording or transcription fails, do not silently delete the only recoverable audio; ask or preserve it with a clear status.

This satisfies both use cases: normal practice without computer clutter, and deliberate later listening/export.

## 12. Whisper

Use whisper.cpp through whisper-cli after the session. Existing local models, in the conventional locations the app probes when
Settings has no model path yet:

    ~/whisper/models/ggml-large-v3-turbo.bin
    ~/whisper/models/ggml-large-v3.bin

The current machine reports whisper.cpp 1.9.1 with Metal, BLAS, and Apple Silicon CPU backends. The CLI accepts GGML model paths and can emit text, JSON, VTT, SRT, and CSV outputs. It also exposes segment timestamps and optional word/token timing.

Model selection:

- store an explicit editable model path in configuration;
- validate the path and runtime before the user starts transcription;
- offer the two existing models when detected;
- use Turbo as a first practical benchmark candidate, not as an automatic truth;
- never download or convert models automatically.

Expected trade-off: large-v3-turbo is substantially smaller and should be faster on a 16 GB Apple Silicon Mac; large-v3 may be preferred when transcription quality is more important than processing time. Measure on representative recordings before choosing a default.

Whisper output is factual transcription only. Do not use it to decide whether the answer is finished or relevant.

## 13. Transcript and timing alignment

The app knows prompt and control-event times. Whisper provides segment timestamps. The first implementation may attempt a technical mapping:

    prompt event intervals + Whisper segment intervals → best-effort answer blocks

This is not guaranteed to be exact because the recording is continuous, prompts may be repeated, and the user may answer through timer boundaries.

Fallback is mandatory: export one complete unsegmented transcript. Never invent question/answer boundaries or alter the user's words to make the Markdown look tidy.

Transcript formats:

- JSON: machine-readable raw Whisper result plus session metadata;
- Markdown: human-readable export with prompt text, event information where reliable, and raw transcript text;
- optional plain TXT later.

No spelling correction, grammar correction, punctuation enhancement, translation, or semantic summary is allowed in the transcript pipeline.

## 14. Storage

User-selected Library folder:

    <library>/
      questions.txt
      scenario.md

Retained session folder, only when something is saved:

    sessions/
      2026-09-30_224500_a1b2c3/
        session.json
        transcript.json
        transcript.md
        recording.wav        # only when Keep recording was selected

The session JSON records the original source path, a content fingerprint, settings, actual prompt order, and event log. A copied content file is not required for MVP, but the fingerprint allows the app to warn when the source later changes.

## 15. Privacy

Default behaviour:

- no cloud TTS;
- no cloud STT;
- no accounts or telemetry;
- no internet dependency for a normal session;
- no permanent audio unless explicitly retained;
- no automatic model download.

The settings screen should show the selected model path, Library path, session path, TTS provider/voice, and recording retention policy.

## 16. Technology comparison

### Python + PySide6 + Qt Quick/QML

Recommended. Python keeps the application logic, subprocess orchestration, file handling and test tooling simple. PySide6 is the official Qt for Python binding, while Qt Quick/QML provides a declarative UI with fluid controls, transitions and custom styling. The result is a native desktop application with a modern visual layer without adding a separate web frontend or Rust bridge. Qt Multimedia and the macOS audio backend must be validated in Phase 0.

### Python + PyQt6

Technically viable and visually comparable to PySide6 because both use Qt. It is not the default because the licensing/distribution decision is less convenient for this project. Use it only if an existing dependency or a confirmed licensing requirement makes it necessary.

### Tauri

Good visual potential and a relatively small runtime, but requires a web frontend plus Rust commands or sidecars for local Python/audio/Whisper integration. It is a reasonable future cross-platform option, not the simplest first implementation for a Python-first project.

### Electron

Good tooling and easy web UI development, but the runtime and memory footprint are unnecessary for this local trainer. Native audio/TTS/Whisper still require bridges or subprocess adapters. It is not recommended for MVP.

### Recommendation

Use Python 3.11+, PySide6, Qt Quick/QML, Qt Multimedia, and protocol-based Python service adapters with external whisper-cli. The UI is declarative and visually flexible; domain logic remains in Python and is testable without the UI.

## 17. Recommended architecture

    QML Views
          ↓ commands / observable state
    Python Session Engine (pure state machine)
          ├── Content Repository → TXT/MD line importer
          ├── TTS Provider → Kokoro system voice / fallback macOS voice
          ├── Recorder → Qt Multimedia/Core Audio temporary WAV
          ├── Session Store → JSON, retention, export
          └── Transcription Service → whisper-cli after session only

Boundaries:

- Content Repository does not know about QML.
- Session Engine does not know whether TTS is Kokoro or a system voice.
- TTS does not know about Whisper.
- Recorder does not decide when the user has finished speaking.
- Transcription Service does not alter Session Engine progression.
- Storage does not require a particular importer.

## 18. MVP scope

### Include

- macOS Apple Silicon desktop app;
- Library folder plus drag-and-drop TXT/MD;
- one non-empty line per prompt;
- Spoken Mode with hidden prompt text and Reveal;
- Fixed Timer and Manual Advance;
- sequential and random-without-repetition order;
- Repeat, Reveal, Skip, Pause, Resume, Next, Stop;
- Written Mode with Audio Prompt and Text Prompt variants;
- optional recording;
- temporary audio retention with explicit Keep recording;
- local TTS with Kokoro system voice when available;
- post-session local Whisper using an existing model;
- Markdown and JSON transcript/session export;
- editable paths, voice, rate, delay, model, Library and session directories;
- clear handling of missing permissions, missing model/runtime, unavailable TTS, and disk errors.

### Exclude

- real-time STT;
- answer evaluation;
- grammar/pronunciation/fluency scoring;
- AI dialogue or LLM calls;
- silence detection and automatic end-of-speech;
- cloud APIs;
- accounts, synchronization, online database, social features;
- complex Markdown headings/sections/branching;
- mobile and Windows builds;
- gamification and analytics beyond objective session metadata.

## 19. Future scope

Only add these after real use exposes a need:

1. richer Markdown blocks and optional speaker labels;
2. reusable prompt collections and filtering;
3. better history and replay navigation;
4. audio caching and prompt pre-generation;
5. direct Kokoro sidecar/provider if it measurably improves latency or voice control;
6. reliable prompt/answer segmentation experiments;
7. additional local TTS voices/languages;
8. alternative platforms.

Branching scenarios may be supported later, but branch selection must remain explicit and content-driven; it must not become answer interpretation by stealth.

## 20. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Kokoro GUI has no stable API | Use registered say voices through a TTS adapter; GUI remains optional. |
| macOS speech API changes | Isolate TTS provider and keep a native voice fallback. |
| 16 GB RAM pressure from Whisper | Transcribe after the session, not concurrently; expose model choice; test Turbo first. |
| Whisper timestamps do not match prompts | Keep raw event log and always provide a complete transcript fallback. |
| Timer advances while the user speaks | Explain this as intentional Fixed Timer behaviour; Manual Advance is the long-answer mode. |
| Audio accumulates on disk | Temporary recording by default; delete after successful transcription unless Keep recording. |
| Permission/runtime failure | Validate before start, show actionable errors, preserve recoverable files. |
| UI becomes a reading exercise | Hide prompt text by default and make Reveal explicit. |
| Scope expands into an AI tutor | Treat every proposed evaluation/generation feature as out of scope unless the product brief changes. |

## 21. Open questions and working assumptions

These are intentionally left as implementation hypotheses rather than questions for the user:

- Repeat restarts the timer after the repeated TTS finishes.
- Pause pauses prompt progression and timer; recording, when enabled, remains continuous so the audio timeline is not fragmented.
- Stop ends the session and invokes post-session processing only when recording or saved typed answers exist.
- A failed transcription preserves audio temporarily and offers retry/keep/delete rather than silently deleting it.
- A session with no recording and no saved typed answers may remain ephemeral.
- The Library initially displays filenames; metadata and tagging are deferred.
- The actual Kokoro voice names are detected at runtime instead of being assumed.

These assumptions should be revisited after the first hands-on workflow test, not debated indefinitely in advance.

## 22. Development roadmap

### Phase 0 — technical probes

- Verify microphone permission and stable mono WAV capture.
- Verify say with Kokoro Heart/Kokoro Michael, playback completion, rate control, and failure handling.
- Verify whisper-cli with both existing GGML models on a representative WAV recording.
- Measure transcription time and memory on the user's Mac.
- Confirm temporary-file deletion and recovery behaviour.

### Phase 1 — smallest usable trainer

- Library/drag-and-drop;
- line importer;
- Spoken Mode;
- Fixed Timer and Manual Advance;
- TTS;
- keyboard/mouse controls;
- optional recording and Stop;
- post-session transcription/export.

### Phase 2 — writing practice and retention

- Written Mode Audio Prompt/Text Prompt;
- Save typed answers setting;
- Keep recording setting;
- session folders and minimal history;
- replay/export UX.

### Phase 3 — refinement

- random order without repetition;
- robust errors and recovery;
- prompt caching if required;
- best-effort technical segmentation with complete-transcript fallback;
- optional direct Kokoro provider if justified by measurements.

## Sources and technical references

- https://github.com/ggml-org/whisper.cpp
- https://github.com/ggml-org/whisper.cpp/blob/master/include/whisper.h
- https://developer.apple.com/documentation/appkit/nsspeechsynthesizer
- https://doc.qt.io/qt-6/qaudiosource.html
- https://github.com/seanbud/Kokoro-Clipboard-TTS
- https://huggingface.co/hexgrad/Kokoro-82M
