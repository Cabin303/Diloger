"""Application controller. Bridges the Session Engine to QML.

All domain rules live in SessionEngine; this class only marshals values and
invokes actions. QML never calls the engine directly.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Property, QCoreApplication, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QJSValue

from diloger.app.theme import THEME_MODES, Theme
from diloger.audio.clock import QtClock
from diloger.audio.player import RecordingPlayer
from diloger.audio.recorder import QtRecorder
from diloger.content.importer import ContentImportError, FileContentImporter
from diloger.domain.models import (
    Mode,
    Order,
    Session,
    SessionConfig,
    SpokenProgression,
    TranscriptionStatus,
    WrittenPromptVariant,
)
from diloger.session.engine import SessionEngine
from diloger.settings.config import AppConfig, load_config, save_config
from diloger.storage.export import ExportResult, export_markdown, suggested_file_name
from diloger.storage.session_store import (
    SessionStorage,
    build_transcript_markdown,
    readable_for,
    stored_readable_blocks,
    transcript_payload,
)
from diloger.transcription.formatter import NOTICE as TRANSCRIPT_NOTICE
from diloger.transcription.readable import readable_text
from diloger.transcription.whisper_cli import WhisperTranscriber, parse_whisper_json
from diloger.tts.mac_say import MacSayTts

DELAY_PRESETS = [1, 2, 3, 5, 10, 15]


def _as_patch(value) -> dict:
    """Normalise a QML argument into a plain dict.

    QML object literals such as `{ "mode": "spoken" }` reach a Python slot as a
    QJSValue, not a dict, so treating the argument as a mapping raised TypeError
    before any state changed. Converting here keeps every QML call site working
    while still accepting a real dict from Python callers and tests.
    """
    if isinstance(value, QJSValue):
        value = value.toVariant()
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(
        f"expected a QML object literal or dict, got {type(value).__name__}"
    )


class AppController(QObject):
    screenChanged = Signal()
    refreshRequested = Signal()
    statusMessage = Signal(str)
    errorMessage = Signal(str)
    libraryChanged = Signal()
    themeChanged = Signal()
    selectedContentPathChanged = Signal()

    # training view state
    stateChanged = Signal()
    positionChanged = Signal()
    promptVisibilityChanged = Signal()
    timerChanged = Signal()
    recordingChanged = Signal()

    # setup view
    setupChanged = Signal()

    # transcription view state
    transcribingChanged = Signal()

    # Settings is edited in a draft; these carry its state to QML.
    settingsChanged = Signal()
    settingsCloseRequested = Signal()

    # One notify for every read-only value QML binds to. A QML binding that
    # calls a @Slot never re-evaluates, so each of these must be a real
    # Property; this signal is the single thing they all listen to.
    viewStateChanged = Signal()

    def __init__(
        self,
        parent: Optional[QObject] = None,
        config_path: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._screen = "library"
        self._config: AppConfig = load_config(config_path)
        self._config_path = Path(config_path) if config_path else None
        # The controller owns the theme because the theme is a setting: QML reads
        # its tokens and Qt Basic reads its palette, both from this one object.
        self._theme = Theme(self, mode=self._config.theme)
        # Settings edits land here until Apply. None means "no draft open".
        self._draft: Optional[AppConfig] = None
        self._importer = FileContentImporter()
        self._tts = MacSayTts()
        self._recorder = QtRecorder()
        self._player = RecordingPlayer()
        self._player.error.connect(self.errorMessage)
        self._transcriber = WhisperTranscriber()
        self._clock = QtClock()
        self._clock.ticked.connect(self._on_tick)

        sessions_path = self._config.sessionsPath or str(SessionStorage().sessions_dir)
        self._storage = SessionStorage(Path(sessions_path))
        self._storage.sessions_dir.mkdir(parents=True, exist_ok=True)

        self._engine: Optional[SessionEngine] = None
        self._content = None
        self._files: list[dict] = []
        self._selected_file: str = ""
        self._session_dir: Optional[Path] = None

        # setup form state
        self._setup = {
            "mode": "spoken",
            "order": "sequential",
            "progression": "fixedTimer",
            "writtenPrompt": "audio",
            "delay": float(self._config.defaultDelaySeconds),
            "record": bool(self._config.recordingEnabledByDefault),
            "keepRecording": bool(self._config.keepRecordingByDefault),
            "saveTyped": bool(self._config.saveTypedAnswersByDefault),
        }

        self._voices: list[str] = []
        self._selected_voice = self._config.ttsVoice
        self._selected_rate = self._config.ttsRate

        self._transcript_segments: list[dict] = []
        self._transcript_raw: str = ""
        self._transcript_error: str = ""
        self._transcribing = False
        # Result of the last attempt, for the caller that started it.
        self._last_transcribe_error: dict = {}
        # Whether `finished` currently has this controller's handler attached.
        self._transcriber_connected = False
        self._orphan_count = 0
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._on_tick)

        if self._config.libraryPath:
            self._scan_library()

    # ---- screens -------------------------------------------------------

    def _get_theme(self) -> Theme:
        return self._theme

    theme = Property(QObject, _get_theme, constant=True)

    def _get_theme_mode(self) -> str:
        return self._config.theme

    themeMode = Property(str, _get_theme_mode, notify=settingsChanged)

    def _get_screen(self) -> str:
        return self._screen

    # A real Qt property, not a Python @property. QML binds to it through
    # `StackLayout.currentIndex`, and only a notify signal can invalidate that
    # binding: a plain Python attribute is read once and never re-evaluated, so
    # every navigation button would look dead.
    screen = Property(str, _get_screen, notify=screenChanged)

    def _go(self, screen: str) -> None:
        if self._screen == screen:
            return
        # A recording must never keep playing behind a screen the user cannot
        # control, so leaving a review screen stops it.
        if self._screen in ("history", "finished"):
            self._player.stop()
        self._screen = screen
        self.screenChanged.emit()

    @Slot()
    def showLibrary(self) -> None:
        if self._engine is not None and not self._engine.is_finished:
            return
        self._go("library")
        self._scan_library()

    @Slot()
    def showSetup(self) -> None:
        if not self._selected_file:
            self.errorMessage.emit("Choose a content file first.")
            return
        self._go("setup")

    @Slot()
    def showSettings(self) -> None:
        # Opening Settings starts a fresh draft from the saved config, so a
        # previous visit's half-finished edits never reappear.
        self._draft = self._copy_config(self._config)
        self.refresh_orphan_count()
        self._go("settings")
        self.settingsChanged.emit()
        self.viewStateChanged.emit()

    @Slot()
    def showTraining(self) -> None:
        self._go("training")

    @Slot()
    def showFinished(self) -> None:
        self._go("finished")

    @Slot()
    def showHistory(self) -> None:
        # Review only: never leave a running session behind the History screen.
        if self._engine is not None and not self._engine.is_finished:
            return
        self._go("history")

    @Slot()
    def quitApp(self) -> None:
        # Closing mid-recording must still finalise the session: the WAV header
        # gets closed and the event log is persisted instead of being lost.
        # Any temp WAV that is not retained stays recoverable via orphan cleanup.
        if self._engine is not None and not self._engine.is_finished:
            self._tick_timer.stop()
            self._engine.stop()
            self._finalize()
        if self._transcribing:
            self._transcriber.cancel()
        self._player.shutdown()
        QCoreApplication.quit()

    # ---- library -------------------------------------------------------

    def _get_library_path(self) -> str:
        return self._config.libraryPath

    libraryPath = Property(str, _get_library_path, notify=viewStateChanged)

    @Slot(result="QVariantList")
    def files(self) -> list:
        return self._files

    @Slot(result="QString")
    def selectedFile(self) -> str:
        return self._selected_file

    @Property(str, notify=selectedContentPathChanged)
    def selectedContentPath(self) -> str:
        """The one source of truth for which content file is chosen."""
        return self._selected_file

    @Slot(str)
    def selectFile(self, path: str) -> None:
        if path == self._selected_file:
            return
        self._selected_file = path
        self.selectedContentPathChanged.emit()
        self.refreshRequested.emit()

    @Slot()
    def rescanLibrary(self) -> None:
        self._scan_library()

    def _scan_library(self) -> None:
        if not self._config.libraryPath:
            self._files = []
            self.libraryChanged.emit()
            self.viewStateChanged.emit()
            return
        try:
            paths = self._importer.list_files(Path(self._config.libraryPath))
        except ContentImportError as exc:
            self._files = []
            self.libraryChanged.emit()
            self.viewStateChanged.emit()
            self.errorMessage.emit(str(exc))
            return
        self._files = [
            {"name": p.name, "path": str(p), "prompts": self._count_prompts(p)} for p in paths
        ]
        if self._selected_file not in {f["path"] for f in self._files}:
            self._selected_file = self._files[0]["path"] if self._files else ""
        self.libraryChanged.emit()
        self.viewStateChanged.emit()

    def _count_prompts(self, path: Path) -> int:
        try:
            return len(self._importer.load_file(path).prompts)
        except ContentImportError:
            return 0

    @Slot(str, result=bool)
    def loadDroppedFile(self, path: str) -> bool:
        p = Path(path)
        try:
            self._importer.load_file(p)
        except ContentImportError as exc:
            self.errorMessage.emit(str(exc))
            return False
        self._selected_file = str(p)
        self._content = self._importer.load_file(p)
        self.statusMessage.emit(f"Loaded {p.name} for a one-off session.")
        self.showSetup()
        return True

    # ---- setup ---------------------------------------------------------

    @Slot(result="QVariantMap")
    def setupState(self) -> dict:
        state = dict(self._setup)
        state["voice"] = self._selected_voice
        state["rate"] = self._selected_rate
        state["sourceName"] = Path(self._selected_file).name if self._selected_file else ""
        return state

    @Slot("QVariant")
    def updateSetup(self, patch) -> None:
        patch = _as_patch(patch)
        # `voice` and `rate` are session choices, not part of the persisted
        # setup dict. They were being filtered out by the key check below, so
        # the Setup screen's voice and rate controls did nothing at all.
        if "voice" in patch:
            self._selected_voice = str(patch["voice"])
        if "rate" in patch:
            try:
                self._selected_rate = int(patch["rate"])
            except (TypeError, ValueError):
                pass
        self._setup.update({k: v for k, v in patch.items() if k in self._setup})
        self.setupChanged.emit()
        self.viewStateChanged.emit()

    @Slot(result="QVariantList")
    def voices(self) -> list:
        if not self._voices:
            self._voices = self._tts.available_voices()
        return self._voices

    @Slot(result="QVariantList")
    def delayPresets(self) -> list:
        return DELAY_PRESETS

    def _get_tts_available(self) -> bool:
        return self._tts.is_available()

    ttsAvailable = Property(bool, _get_tts_available, notify=viewStateChanged)

    @Slot()
    def startSession(self) -> None:
        if not self._selected_file:
            self.errorMessage.emit("No content file selected.")
            return
        try:
            content = self._content if (self._content and self._content.sourcePath == self._selected_file) \
                else self._importer.load_file(Path(self._selected_file))
        except ContentImportError as exc:
            self.errorMessage.emit(str(exc))
            return

        mode = Mode(self._setup["mode"])
        progression = None
        written_prompt = None
        if mode is Mode.SPOKEN:
            progression = SpokenProgression(self._setup["progression"])
        else:
            written_prompt = WrittenPromptVariant(self._setup["writtenPrompt"])

        needs_audio = mode is Mode.SPOKEN or written_prompt is WrittenPromptVariant.AUDIO
        if needs_audio and not self._tts.is_available():
            self.errorMessage.emit(
                "Speech output is unavailable (macOS 'say' not found). "
                "Use Written Mode with a Text Prompt instead."
            )
            return
        if not self._tts.is_voice_available(self._selected_voice):
            fallback = next(
                (v for v in ("Kokoro Heart", "Kokoro Michael", "Samantha") if self._tts.is_voice_available(v)),
                None,
            )
            if fallback is None:
                self.errorMessage.emit(
                    f"Voice '{self._selected_voice}' is not installed. Pick another voice in Settings."
                )
                return
            self._selected_voice = fallback
            self.statusMessage.emit(f"Voice unavailable. Using {fallback}.")

        if self._setup["record"] and not self._recorder.is_available():
            self._setup["record"] = False
            self.statusMessage.emit("No microphone found. Continuing without recording.")

        self._tts.set_voice(self._selected_voice)
        self._tts.set_rate(self._selected_rate)

        if not self._storage.has_space():
            self.statusMessage.emit("Low disk space. Recording may be stopped to protect data.")

        self._content = content
        self._transcript_segments = []
        self._transcript_error = ""
        self._set_transcribing(False)
        self._engine = SessionEngine(
            tts=self._tts,
            recorder=self._recorder if self._setup["record"] else None,
            clock=self._clock,
            content=content,
            config=SessionConfig(
                mode=mode,
                spokenProgression=progression,
                writtenPrompt=written_prompt,
                order=Order(self._setup["order"]),
                configuredDelaySeconds=float(self._setup["delay"]),
                recordingEnabled=bool(self._setup["record"]),
                keepRecording=bool(self._setup["keepRecording"]),
                saveTypedAnswers=bool(self._setup["saveTyped"]),
                ttsVoice=self._selected_voice,
                ttsRate=self._selected_rate,
                whisperModelPath=self._config.whisperModelPath
                or (self._transcriber.discover_model() or ""),
            ),
            on_state_changed=self._on_engine_state,
        )
        self._session_dir = None
        self._tick_timer.start()
        self._go("training")
        self._engine.start()
        self._on_engine_state()

    # ---- training actions ----------------------------------------------

    @Slot()
    def next(self) -> None:
        if self._engine is not None:
            self._engine.next()

    @Slot()
    def skip(self) -> None:
        if self._engine is not None:
            self._engine.skip()

    @Slot()
    def repeat(self) -> None:
        if self._engine is not None:
            self._engine.repeat()

    @Slot()
    def reveal(self) -> None:
        if self._engine is not None:
            self._engine.reveal()

    @Slot()
    def hidePrompt(self) -> None:
        if self._engine is not None:
            self._engine.hide()

    @Slot()
    def togglePause(self) -> None:
        if self._engine is None:
            return
        if self._engine.state.value == "paused":
            self._engine.resume()
        else:
            self._engine.pause()

    @Slot(str)
    def setTypedAnswer(self, text: str) -> None:
        if self._engine is not None:
            self._engine.set_typed_answer(text)

    @Slot()
    def stopSession(self) -> None:
        if self._engine is None or self._engine.is_finished:
            return
        self._tick_timer.stop()
        self._engine.stop()
        self._finalize()
        self.showFinished()

    # ---- training view state -------------------------------------------

    @Slot(result=str)
    def trainingState(self) -> str:
        return self._engine.state.value if self._engine else "idle"

    def _get_progress_text(self) -> str:
        if self._engine is None:
            return ""
        return f"{self._engine.current_position} / {self._engine.total_prompts}"

    progressText = Property(str, _get_progress_text, notify=viewStateChanged)

    def _get_mode_text(self) -> str:
        if self._engine is None:
            return ""
        cfg = self._engine.session.config
        if cfg.mode is Mode.SPOKEN:
            label = "Spoken"
            if cfg.spokenProgression is SpokenProgression.FIXED_TIMER:
                return f"{label} · Fixed Timer {cfg.configuredDelaySeconds:g}s"
            return f"{label} · Manual"
        variant = "Audio Prompt" if cfg.writtenPrompt is WrittenPromptVariant.AUDIO else "Text Prompt"
        return f"Written · {variant}"

    modeText = Property(str, _get_mode_text, notify=viewStateChanged)

    def _get_prompt_visible(self) -> bool:
        return self._engine.prompt_text_visible if self._engine else False

    promptVisible = Property(bool, _get_prompt_visible, notify=viewStateChanged)

    def _get_prompt_text(self) -> str:
        prompt = self._engine.current_prompt if self._engine else None
        return prompt.text if prompt else ""

    promptText = Property(str, _get_prompt_text, notify=viewStateChanged)

    def _get_timer_remaining(self) -> float:
        return self._engine.timer_remaining() if self._engine else 0.0

    timerRemaining = Property(float, _get_timer_remaining, notify=viewStateChanged)

    def _get_timer_fraction(self) -> float:
        if self._engine is None or self._engine.total_prompts == 0:
            return 0.0
        cfg = self._engine.session.config
        if cfg.configuredDelaySeconds <= 0:
            return 0.0
        return max(0.0, min(1.0, self._engine.timer_remaining() / cfg.configuredDelaySeconds))

    timerFraction = Property(float, _get_timer_fraction, notify=viewStateChanged)

    def _get_is_paused(self) -> bool:
        return self._engine is not None and self._engine.state.value == "paused"

    isPaused = Property(bool, _get_is_paused, notify=viewStateChanged)

    def _get_is_at_end(self) -> bool:
        return self._engine is not None and self._engine.state.value == "atEnd"

    isAtEnd = Property(bool, _get_is_at_end, notify=viewStateChanged)

    def _get_is_last_prompt(self) -> bool:
        """True while standing on the final prompt, before pressing Next."""
        return self._engine is not None and self._engine.is_last_prompt

    isLastPrompt = Property(bool, _get_is_last_prompt, notify=viewStateChanged)

    def _get_is_typing(self) -> bool:
        return self._engine is not None and self._engine.state.value == "typing"

    isTyping = Property(bool, _get_is_typing, notify=viewStateChanged)

    def _get_is_recording(self) -> bool:
        if self._engine is None:
            return False
        return bool(self._engine.session.config.recordingEnabled) and not self._engine.is_finished

    isRecording = Property(bool, _get_is_recording, notify=viewStateChanged)

    def _get_can_next(self) -> bool:
        if self._engine is None or self._engine.is_finished:
            return False
        if self._engine.state.value in ("waiting", "typing"):
            return True
        return False

    canNext = Property(bool, _get_can_next, notify=viewStateChanged)

    def _on_tick(self) -> None:
        if self._engine is not None and self._engine.state.value in ("waiting", "paused"):
            self.timerChanged.emit()
            self.viewStateChanged.emit()

    def _on_engine_state(self) -> None:
        self.viewStateChanged.emit()
        self.stateChanged.emit()
        self.positionChanged.emit()
        self.promptVisibilityChanged.emit()
        self.recordingChanged.emit()

    # ---- finished / retention -------------------------------------------

    def _finalize(self) -> None:
        if self._engine is None:
            return
        session = self._engine.session
        recording = self._engine.recording_path()
        keep = bool(self._setup["keepRecording"])

        # An unrecorded, unsaved session leaves no permanent folder behind.
        needs_save = keep or bool(recording) or bool(session.typedAnswers)
        if not needs_save:
            return
        try:
            self._session_dir = self._storage.create_session_dir(session.wallStartedAt)
        except OSError as exc:
            self.errorMessage.emit(f"Could not create session folder: {exc.strerror or exc}")
            return

        if recording is not None:
            if keep:
                # Kept audio belongs to the session folder from the start.
                target = self._session_dir / "recording.wav"
                try:
                    shutil.move(str(recording), str(target))
                except OSError as exc:
                    self.errorMessage.emit(f"Could not save recording: {exc.strerror or exc}")
                    return
                session.recordingPath = str(target)
                session.recordingRetained = True
            else:
                # Temporary by default. It stays available for post-session
                # Whisper and is removed once that transcription succeeds.
                session.recordingPath = str(recording)
                session.recordingRetained = False
        else:
            session.recordingRetained = False

        self._write_session_json()
        self.viewStateChanged.emit()
        # The Finished and History screens read `finishedInfo()` /
        # `sessionList()` on refreshRequested, and every page of the
        # StackLayout completes at startup, so their Component.onCompleted ran
        # before this session existed. Without this signal the Finished screen
        # keeps its default info and every audio button stays disabled, which
        # makes the recording unreachable and untranscribable.
        self.refreshRequested.emit()

    def _discard_temp_recording(self) -> None:
        """Remove temporary audio after a successful transcription."""
        if self._engine is None:
            return
        session = self._engine.session
        if session.recordingRetained or not session.recordingPath:
            return
        path = Path(session.recordingPath)
        if path.parent == Path(tempfile.gettempdir()):
            self._storage.delete(path)
            session.recordingPath = None
            # The player is bound to `canPlayCurrentRecording`, whose notify
            # signal is this one. Without it the Finished screen keeps showing
            # Play/Pause/Stop for audio that no longer exists, and pressing Play
            # does nothing at all.
            self.viewStateChanged.emit()

    def _write_session_json(self) -> None:
        if self._engine is None or self._session_dir is None:
            return
        try:
            payload = self._engine.session.to_dict()
            path = self._storage.write_session_json(self._session_dir, payload)
            if self._engine.session.transcriptionStatus is TranscriptionStatus.SUCCEEDED:
                transcript_path = self._storage.write_transcript_json(
                    self._session_dir,
                    transcript_payload(
                        payload,
                        self._transcript_segments,
                        model=self._engine.session.config.whisperModelPath,
                        event_log=[e.to_dict() for e in self._engine.session.eventLog],
                        search_dirs=self._content_search_dirs(),
                    ),
                )
                # Recorded, never cleared: a later retry must not drop the
                # path to a transcript that already exists on disk.
                self._engine.session.transcriptJsonPath = str(transcript_path)
                if self._transcript_raw:
                    # whisper-cli's own JSON, byte for byte, next to the
                    # parsed copy: the transcript can be rebuilt or checked
                    # from it later.
                    self._storage.write_raw_whisper_json(
                        self._session_dir, self._transcript_raw
                    )
        except OSError as exc:
            self.errorMessage.emit(f"Could not write session data: {exc.strerror or exc}")

    def _live_transcript_blocks(self) -> list:
        """Readable blocks for the session that just finished.

        Built from the same raw segments that were just written to
        transcript.json, so the Finished screen and a later History visit show
        one view rather than two that drift apart.
        """
        if self._engine is None:
            return []
        return readable_for(
            self._engine.session.to_dict(),
            self._transcript_segments,
            [e.to_dict() for e in self._engine.session.eventLog],
            self._content_search_dirs(),
        )

    @Slot(result="QVariantMap")
    def finishedInfo(self) -> dict:
        if self._engine is None:
            return {}
        session = self._engine.session
        return {
            "durationSeconds": int(session.durationSeconds),
            "promptsReached": session.promptsReached,
            "totalPrompts": self._engine.total_prompts,
            "hasRecording": bool(session.recordingPath),
            "recordingRetained": bool(session.recordingRetained),
            "recordingIsTemporary": bool(
                session.recordingPath
                and Path(session.recordingPath).parent == Path(tempfile.gettempdir())
            ),
            "recordingPath": session.recordingPath or "",
            "transcriptionStatus": session.transcriptionStatus.value,
            "transcriptJsonPath": session.transcriptJsonPath or "",
            "transcriptMarkdownPath": session.transcriptMarkdownPath or "",
            # Structured blocks, not the export document and not one joined
            # line: the timings are the evidence that the words came from this
            # recording, and the labels say who spoke without pretending the
            # split is known when it is not.
            "transcriptBlocks": self._live_transcript_blocks(),
            "transcriptNotice": TRANSCRIPT_NOTICE,
            "hasTranscript": bool(self._transcript_segments),
            "transcriptError": self._transcript_error,
            "sessionDir": str(self._session_dir) if self._session_dir else "",
            "keepRecording": bool(self._setup["keepRecording"]),
        }

    @Slot()
    def retryTranscription(self) -> None:
        if self._engine is None or self._session_dir is None:
            self.errorMessage.emit("Nothing to transcribe.")
            return
        session = self._engine.session
        if not session.recordingPath:
            self.errorMessage.emit(
                "No recording was kept, so there is nothing to transcribe. "
                "Enable 'Keep recording' to be able to retry later."
            )
            return

        def store(segments: list[dict], raw: str) -> None:
            self._transcript_segments = segments
            self._transcript_raw = raw
            session.transcriptionStatus = TranscriptionStatus.SUCCEEDED

        self._run_transcription(Path(session.recordingPath), session, store)

    @Slot(str, result="QVariantMap")
    def transcribeSession(self, session_id: str) -> dict:
        """Transcribe a stored session's own recording, without a new session.

        History acts on what is already on disk, so this reads the folder under
        containment, hands its recording.wav to the same whisper-cli path the
        Finished screen uses, and writes the result back into that folder.
        """
        folder = self._safe_session_dir(session_id)
        if folder is None:
            self.errorMessage.emit("That session could not be found.")
            return {"status": "error", "error": "session not found"}
        recording = self._storage.recording_path(folder)
        if recording is None:
            self.errorMessage.emit(
                "This session has no recording to transcribe. Its audio was "
                "not kept."
            )
            return {"status": "error", "error": "no recording"}
        stored = self._storage.read_session_json(folder)
        if not stored:
            self.errorMessage.emit(
                "session.json is missing or unreadable for this folder, so the "
                "transcript cannot be filed against it."
            )
            return {"status": "error", "error": "no session.json"}

        state = {"status": "running"}
        # Written before the run starts so History shows "running" instead of
        # the stale "notRequested" for the whole length of a long transcription.
        running = dict(stored)
        running["transcriptionStatus"] = TranscriptionStatus.RUNNING.value
        try:
            self._storage.write_session_json(folder, running)
        except OSError:
            pass

        def store(segments: list[dict], raw: str) -> None:
            self._store_transcript_for(folder, stored, segments, raw)

        def failed(message: str) -> None:
            self._mark_transcript_failed_for(folder, stored)

        started = self._run_transcription(recording, stored, store, failed)
        if not started:
            state = self._last_transcribe_error
        self.refreshRequested.emit()
        return state

    def _mark_transcript_failed_for(self, folder: Path, stored: dict) -> None:
        """Record a failed attempt on the folder, keeping every other field."""
        updated = dict(stored)
        updated["transcriptionStatus"] = TranscriptionStatus.FAILED.value
        try:
            self._storage.write_session_json(folder, updated)
        except OSError as exc:
            self.errorMessage.emit(
                f"Could not record the transcription failure: {exc.strerror or exc}"
            )

    def _store_transcript_for(
        self, folder: Path, stored: dict, segments: list[dict], raw: str
    ) -> None:
        """Write transcript.json, whisper.json and transcript.md for a folder."""
        try:
            self._storage.write_transcript_json(
                folder,
                transcript_payload(
                    stored,
                    segments,
                    model=stored.get("whisperModelPath", ""),
                    event_log=stored.get("eventLog") or [],
                    search_dirs=self._content_search_dirs(),
                ),
            )
            if raw:
                self._storage.write_raw_whisper_json(folder, raw)
            markdown = build_transcript_markdown(
                stored,
                segments,
                stored.get("typedAnswers") or {},
                None,
                recording_available=self._storage.recording_path(folder) is not None,
                # Whisper ran and this is its output, so an empty list means a
                # silent recording, not a transcript that never happened.
                transcript_generated=True,
            )
            self._storage.write_transcript_markdown(folder, markdown)
        except OSError as exc:
            self.errorMessage.emit(f"Could not write transcript: {exc.strerror or exc}")
            return

        updated = dict(stored)
        updated["transcriptionStatus"] = TranscriptionStatus.SUCCEEDED.value
        updated["whisperModelPath"] = self._config.whisperModelPath or updated.get(
            "whisperModelPath", ""
        )
        updated["transcriptJsonPath"] = str(folder / "transcript.json")
        updated["transcriptMarkdownPath"] = str(folder / "transcript.md")
        self._storage.write_session_json(folder, updated)

    def _run_transcription(
        self,
        audio_path: Path,
        session_payload,
        store,
        fail=None,
    ) -> bool:
        """Run whisper-cli once and hand the outcome to the caller's callbacks.

        `session_payload` is a live `Session` for the current session and a
        stored dict for a history folder; only the live one owns status flags
        and re-writes its own files, so a failed history run cannot mark the
        session the user just finished as failed.
        """
        if self._transcribing:
            self._last_transcribe_error = {
                "status": "error",
                "error": "A transcription is already running.",
            }
            self.errorMessage.emit("A transcription is already running.")
            return False

        runtime = self._config.whisperCLIPath or self._transcriber.discover_runtime() or ""
        model = self._config.whisperModelPath or self._transcriber.discover_model() or ""
        ok, reason = self._transcriber.check_paths(runtime, model)
        if not ok:
            self._last_transcribe_error = {"status": "error", "error": reason}
            self.errorMessage.emit(reason)
            return False

        self._set_transcribing(True)
        self._transcript_error = ""
        self._last_transcribe_error = {}
        is_current = isinstance(session_payload, Session)
        if is_current:
            session_payload.transcriptionStatus = TranscriptionStatus.RUNNING
            session_payload.apply_whisper_model(model)
        self.statusMessage.emit(reason if reason != "ok" else "Transcribing…")

        def done(ok_flag: bool, detail: str) -> None:
            # The adapter already settles once, but a stale queued callback from
            # a previous run must not resurrect a finished transcription.
            if not self._transcribing:
                return
            self._set_transcribing(False)
            if ok_flag:
                try:
                    payload = Path(detail).read_text(encoding="utf-8")
                except OSError as exc:
                    self._report_transcribe_failure(
                        f"Cannot read Whisper output: {exc}", fail, current=is_current
                    )
                    return
                finally:
                    # The scratch dir only exists for the JSON handoff.
                    self._transcriber.cleanup_output()
                store(parse_whisper_json(payload), payload)
                self.statusMessage.emit("Transcription complete.")
                if is_current and not session_payload.recordingRetained:
                    self._discard_temp_recording()
            else:
                self._report_transcribe_failure(detail, fail, current=is_current)
            if is_current:
                self._write_session_json()
                self._write_markdown()
            self.refreshRequested.emit()

        # One connection per attempt: a retry must not leave the previous
        # closure listening, or an old handler can consume a later result.
        # Disconnecting an unconnected signal only raises a warning in PySide6,
        # so the first attempt is tracked instead of blindly disconnecting.
        if self._transcriber_connected:
            self._transcriber.finished.disconnect()
        self._transcriber.finished.connect(done, QtQueuedConnection())
        self._transcriber_connected = True
        self._transcriber.transcribe(audio_path, runtime, model)
        return True

    def _report_transcribe_failure(
        self, message: str, fail=None, current: bool = False
    ) -> None:
        """Record a failed run.

        Only a failure of the session the user is looking at right now may mark
        that session failed. A History run belongs to a stored folder, and its
        own callback writes the status there; letting it touch the live session
        would put a red Failed on a transcript that is perfectly fine.
        """
        self._last_transcribe_error = {"status": "error", "error": message}
        if current:
            self._transcript_error = message
            if self._engine is not None:
                self._engine.session.transcriptionStatus = TranscriptionStatus.FAILED
        if fail is not None:
            fail(message)
        self.errorMessage.emit(f"Transcription failed: {message}")

    def _transcribe_failed(self, message: str) -> None:
        self._report_transcribe_failure(message, current=True)

    @Slot()
    def cancelTranscription(self) -> None:
        self._transcriber.cancel()

    def _set_transcribing(self, value: bool) -> None:
        """Own the flag so `isTranscribing` always notifies QML.

        The Finished screen enables/disables Keep audio and Delete audio from
        this value; as a plain Slot it would be sampled once and the buttons
        would stay live while whisper-cli is running.
        """
        if self._transcribing == value:
            return
        self._transcribing = value
        self.transcribingChanged.emit()

    def _get_transcribing(self) -> bool:
        return self._transcribing

    transcribing = Property(bool, _get_transcribing, notify=transcribingChanged)

    def _write_markdown(self) -> None:
        if self._engine is None or self._session_dir is None:
            return
        session = self._engine.session
        prompt_texts = {}
        if self._content is not None:
            by_id = {p.id: p.text for p in self._content.prompts}
            # Only prompts the session actually reached belong in the export.
            for position in session.promptOrder[: session.promptsReached]:
                text = by_id.get(position)
                if text is not None:
                    prompt_texts[position] = text
        payload = session.to_dict()
        markdown = build_transcript_markdown(
            payload,
            self._transcript_segments,
            session.typedAnswers if session.config.saveTypedAnswers else {},
            prompt_texts,
            recording_available=bool(session.recordingPath),
            transcript_generated=session.transcriptionStatus
            is TranscriptionStatus.SUCCEEDED,
        )
        try:
            path = self._storage.write_transcript_markdown(self._session_dir, markdown)
            self._engine.session.transcriptMarkdownPath = str(path)
            self._write_session_json()
        except OSError as exc:
            self.errorMessage.emit(f"Could not write Markdown: {exc.strerror or exc}")

    def _export_directory(self) -> Path:
        """Where the Save dialog opens: the last export folder, or a real one.

        The remembered folder may have been deleted since the last export, so it
        is used only while it still exists; the sessions folder is the fallback
        because the app has already made it.
        """
        remembered = self._config.exportDirectory
        if remembered and Path(remembered).is_dir():
            return Path(remembered)
        return self._storage.sessions_dir

    def _select_export_target(self, directory: Path, suggested: str) -> str:
        """Native Save dialog. An empty return means the user cancelled."""
        from PySide6.QtWidgets import QFileDialog

        return QFileDialog.getSaveFileName(
            None,
            "Export Markdown transcript",
            str(directory / suggested),
            "Markdown (*.md);;All files (*)",
        )[0]

    def _remember_export_directory(self, directory: str) -> None:
        """Remember the folder, never the file, and only after a real save."""
        if not directory:
            return
        if self._config.exportDirectory == directory:
            return
        self._config.exportDirectory = directory
        try:
            save_config(self._config, self._config_path)
        except OSError:
            # A failed remember must not fail the export the user just got.
            pass
        self.settingsChanged.emit()

    def _run_export(self, markdown: str, session: dict, session_id: str) -> dict:
        """Ask for a destination, write the file there, report what happened."""
        directory = self._export_directory()
        suggested = suggested_file_name(session, session_id)
        try:
            chosen = self._select_export_target(directory, suggested)
        except Exception as exc:  # a failing dialog must not take the app down
            self.errorMessage.emit(f"Could not open the Save dialog: {exc}")
            return ExportResult("error", error=str(exc)).to_dict()
        if not chosen:
            # Cancel: no file, no remembered folder, no error message.
            return ExportResult("cancelled").to_dict()

        result = export_markdown(chosen, markdown)
        if result.saved:
            self._remember_export_directory(result.directory)
            self.statusMessage.emit(f"Saved {result.path}")
        else:
            self.errorMessage.emit(result.error)
        return result.to_dict()

    def _current_session_markdown(self) -> str:
        """The Markdown for the session that just finished, prompts included."""
        self._write_markdown()
        if self._session_dir is None:
            return ""
        return self._storage.read_transcript_markdown(self._session_dir)

    def _stored_session_markdown(self, folder: Path, stored: dict) -> str:
        """Rebuild a stored session's Markdown from what is in its folder.

        Regenerating beats copying `transcript.md`: it works for a folder whose
        Markdown was lost, and it picks up the timestamps stored in
        transcript.json even when the file predates the formatted export.
        """
        segments = list(self._storage.read_transcript_json(folder).get("segments") or [])
        # `succeeded` is the run's own verdict. Requiring segments as well would
        # relabel a silent recording as a transcript that never happened.
        generated = str(stored.get("transcriptionStatus") or "") == "succeeded"
        blocks = stored_readable_blocks(folder, stored, self._content_search_dirs())
        return build_transcript_markdown(
            stored,
            segments,
            stored.get("typedAnswers") or {},
            None,
            recording_available=self._storage.recording_path(folder) is not None,
            transcript_generated=generated,
            readable_blocks=blocks,
        )

    @Slot(result="QVariantMap")
    def exportCurrentSession(self) -> dict:
        """Export Markdown for the finished session to a file the user picks."""
        if self._engine is None or self._session_dir is None:
            self.errorMessage.emit("There is no finished session to export.")
            return ExportResult("error", error="no session").to_dict()
        markdown = self._current_session_markdown()
        session = self._engine.session.to_dict()
        return self._run_export(markdown, session, self._session_dir.name)

    @Slot(str, result="QVariantMap")
    def exportSession(self, session_id: str) -> dict:
        """Export a stored session from History, without replaying it."""
        folder = self._safe_session_dir(session_id)
        if folder is None:
            self.errorMessage.emit("That session could not be found.")
            return ExportResult("error", error="session not found").to_dict()
        stored = self._storage.read_session_json(folder)
        if not stored:
            self.errorMessage.emit(
                "session.json is missing or unreadable for this folder, so "
                "there is nothing to describe in an export."
            )
            return ExportResult("error", error="no session.json").to_dict()
        markdown = self._stored_session_markdown(folder, stored)
        return self._run_export(markdown, stored, folder.name)

    # ---- playback -------------------------------------------------------
    # Plays an existing recording.wav in place; no audio is ever copied.

    @Property(QObject, constant=True)
    def player(self) -> QObject:
        """The shared local player, exposed to QML as `player`."""
        return self._player

    def _get_can_play_current_recording(self) -> bool:
        path = self._current_recording_path()
        return bool(path and Path(path).is_file())

    canPlayCurrentRecording = Property(
        bool, _get_can_play_current_recording, notify=viewStateChanged
    )

    @Slot(result=str)
    def playbackSource(self) -> str:
        """Path of the retained recording available for replay, or ""."""
        return self._current_recording_path()

    @Slot()
    def playCurrentRecording(self) -> None:
        path = self._current_recording_path()
        if not path:
            self.errorMessage.emit("There is no recording to play.")
            return
        self._player.load(path)

    @Slot(str, result=str)
    def sessionRecordingPath(self, session_id: str) -> str:
        """The retained recording of a stored session, or "".

        History passes a session id, never a path: the folder is resolved under
        containment here, so QML cannot make the player read something outside
        the sessions directory.
        """
        folder = self._safe_session_dir(session_id)
        if folder is None:
            return ""
        recording = self._storage.recording_path(folder)
        return str(recording) if recording else ""

    @Slot(str, result=bool)
    def playSessionRecording(self, session_id: str) -> bool:
        path = self.sessionRecordingPath(session_id)
        if not path:
            self.errorMessage.emit("There is no recording to play.")
            return False
        self._player.load(path)
        return True

    @Slot()
    def pausePlayback(self) -> None:
        self._player.pause()

    @Slot()
    def stopPlayback(self) -> None:
        self._player.stop()

    @Slot(int)
    def seekPlayback(self, position_ms: int) -> None:
        self._player.seekTo(position_ms)

    def _current_recording_path(self) -> str:
        """The retained recording of the current session, if there is one."""
        if self._session_dir is not None:
            retained = self._storage.recording_path(self._session_dir)
            if retained is not None:
                return str(retained)
        if self._engine is not None and self._engine.session.recordingPath:
            candidate = Path(self._engine.session.recordingPath)
            if candidate.is_file():
                return str(candidate)
        return ""

    # ---- history --------------------------------------------------------

    @Slot(result="QVariantList")
    def sessionHistory(self) -> list:
        return self._storage.list_sessions()

    @Slot(result=str)
    def sessionsPath(self) -> str:
        return str(self._storage.sessions_dir)

    @Slot(str, result="QVariantList")
    def transcriptBlocksForSession(self, session_id: str) -> list:
        """Readable transcript blocks for one stored session.

        Structured blocks, never `transcript.md`. The Markdown file is an export
        document -- it carries a `# Dialogue Trainer Session` header, `- Date:`
        lines and a `## Prompts and typed answers` section, and pasting all of
        that into a `Text` item is what put literal Markdown on the History
        screen. The words themselves come from the raw Whisper segments; this
        only supplies the readable presentation of them.
        """
        folder = self._safe_session_dir(session_id)
        if folder is None:
            self.errorMessage.emit("That session could not be found.")
            return []
        stored = self._storage.read_session_json(folder)
        blocks = stored_readable_blocks(folder, stored, self._content_search_dirs())
        if not blocks:
            generated = str(stored.get("transcriptionStatus") or "") == "succeeded"
            self.statusMessage.emit(
                "This session has no transcript yet. Run Transcribe to create one."
                if not generated
                else "No speech was recognised in this recording."
            )
        return blocks

    @Slot(str, result=str)
    def transcriptTextForSession(self, session_id: str) -> str:
        """The same blocks as plain text, for copy/paste and tests.

        Still not the export: this carries no `#`, no `- Date:` and no export
        section, so it is safe to put in front of a reader.
        """
        blocks = self.transcriptBlocksForSession(session_id)
        return readable_text(blocks)

    def _content_search_dirs(self) -> list:
        """Folders a missing content file may have moved to.

        `prompt_texts_for_session` already prefers the path recorded in the
        session. This only adds the folders a file may since have been moved
        into, so a session can still show the question the user asked after they
        reorganise their Desktop.
        """
        found = []
        for path in (self._config.libraryPath, self._config.sessionsPath, self._selected_file):
            if path:
                found.append(Path(str(path)).parent)
        return [folder for folder in found if folder.is_dir()]

    @Slot(str, result=bool)
    def deleteSession(self, session_id: str) -> bool:
        folder = self._safe_session_dir(session_id)
        if folder is None:
            self.errorMessage.emit("That session could not be found.")
            return False
        if not self._storage.delete_session(session_id):
            self.errorMessage.emit(f"Could not delete session {session_id}.")
            return False
        if self._session_dir is not None and self._session_dir.resolve() == folder.resolve():
            self._player.clearSource()
        self.statusMessage.emit(f"Deleted session {session_id}.")
        self.refreshRequested.emit()
        return True

    @Slot(result="QVariantMap")
    def clearAllSessions(self) -> dict:
        """Delete every stored session folder, reporting what actually happened.

        The count that comes back is what the user is told: a silent True would
        hide folders that could not be removed and make the history look empty
        when it is not.
        """
        # Playback is stopped first: a file that is about to disappear must not
        # keep playing from a screen that has no control for it.
        self._player.stop()
        self._player.clearSource()
        result = self._storage.clear_all_sessions()
        if self._session_dir is not None:
            self._session_dir = None
        if result.failures:
            self.errorMessage.emit(
                f"Deleted {result.removed} of {result.total} session(s). "
                f"{len(result.failures)} could not be removed: "
                + "; ".join(result.failures)
            )
        elif result.removed:
            self.statusMessage.emit(f"Deleted {result.removed} session(s).")
        else:
            self.statusMessage.emit("There were no stored sessions to delete.")
        self.refreshRequested.emit()
        self.viewStateChanged.emit()
        return {
            "total": result.total,
            "removed": result.removed,
            "remaining": result.remaining,
            "skipped": result.skipped,
            "failed": len(result.failures),
            "errors": list(result.failures),
        }

    def _safe_session_dir(self, session_id: str) -> Optional[Path]:
        """Resolve a session id to a real folder inside the sessions directory."""
        name = str(session_id or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return None
        root = self._storage.sessions_dir.resolve()
        candidate = (root / name).resolve()
        if candidate.parent != root or not candidate.is_dir():
            return None
        return candidate

    @Slot()
    def keepOrDeleteAudio(self) -> None:
        if self._engine is None or self._session_dir is None:
            return
        session = self._engine.session
        if session.recordingPath:
            self.statusMessage.emit("Recording is already kept in the session folder.")
            return
        temp = self._storage.temp_audio_path(session.sessionId)
        if not temp.exists():
            self.statusMessage.emit("No temporary recording to keep.")
            return
        target = self._session_dir / "recording.wav"
        try:
            target.write_bytes(temp.read_bytes())
        except OSError as exc:
            self.errorMessage.emit(f"Could not keep audio: {exc.strerror or exc}")
            return
        session.recordingPath = str(target)
        session.recordingRetained = True
        self._storage.delete(temp)
        self._write_session_json()
        self.refreshRequested.emit()

    @Slot()
    def discardAudio(self) -> None:
        if self._engine is None or self._session_dir is None:
            return
        session = self._engine.session
        if session.recordingPath:
            self._storage.delete(Path(session.recordingPath))
            session.recordingPath = None
            session.recordingRetained = False
            self._transcript_segments = []
            session.transcriptionStatus = TranscriptionStatus.NOT_REQUESTED
            self._transcript_error = ""
            self._write_session_json()
            self._write_markdown()
        self.refreshRequested.emit()

    # ---- settings --------------------------------------------------------
    # Settings is staged: every control edits a draft copy, nothing reaches
    # config.json until Apply. `updateSettings` used to save on each keystroke
    # and each toggle, which made "Back" meaningless as a cancel.

    def _copy_config(self, cfg: AppConfig) -> AppConfig:
        return AppConfig(**{**cfg.__dict__, "shortcuts": dict(cfg.shortcuts or {})})

    def _settings_draft(self) -> AppConfig:
        """The draft, created from the saved config on first use."""
        if self._draft is None:
            self._draft = self._copy_config(self._config)
        return self._draft

    def _get_settings_dirty(self) -> bool:
        """True when the draft differs from what is actually in effect."""
        if self._draft is None:
            return False
        return self._draft.__dict__ != self._config.__dict__

    settingsDirty = Property(bool, _get_settings_dirty, notify=settingsChanged)

    @Slot(result="QVariantMap")
    def settingsState(self) -> dict:
        """Read-only view of the draft, plus everything Settings needs to draw.

        The active config is not exposed here: the screen must always describe
        what the user is editing, never what happens to be saved.
        """
        draft = self._settings_draft()
        return {
            "libraryPath": draft.libraryPath,
            "sessionsPath": draft.sessionsPath,
            "exportDirectory": draft.exportDirectory,
            "whisperCLIPath": draft.whisperCLIPath,
            "whisperModelPath": draft.whisperModelPath,
            "ttsVoice": draft.ttsVoice,
            "ttsRate": draft.ttsRate,
            "defaultDelaySeconds": draft.defaultDelaySeconds,
            "recordingEnabledByDefault": draft.recordingEnabledByDefault,
            "keepRecordingByDefault": draft.keepRecordingByDefault,
            "saveTypedAnswersByDefault": draft.saveTypedAnswersByDefault,
            "theme": draft.theme,
            "themeModes": list(THEME_MODES),
            "voices": self.voices(),
            "shortcuts": dict(draft.shortcuts or {}),
            "whisperStatus": self._whisper_status(),
            "ttsAvailable": self._tts.is_available(),
            "orphanFiles": self._orphan_count,
            "dirty": self._get_settings_dirty(),
            "problems": draft.validate(),
        }

    def _whisper_status(self) -> str:
        ok, reason = self._transcriber.is_available()
        return reason

    @Slot("QVariant")
    def updateSettingsDraft(self, patch) -> None:
        """Edit the draft. Never writes config.json and never moves storage."""
        patch = _as_patch(patch)
        draft = self._settings_draft()
        for key, value in patch.items():
            if key not in AppConfig.__dataclass_fields__:
                continue
            current = getattr(draft, key)
            try:
                if isinstance(current, bool):
                    setattr(draft, key, bool(value))
                elif isinstance(current, float):
                    setattr(draft, key, float(value))
                elif isinstance(current, int):
                    setattr(draft, key, int(value))
                else:
                    setattr(draft, key, str(value))
            except (TypeError, ValueError):
                # A value the field cannot hold leaves the draft as it was; the
                # problem is reported when Apply validates it.
                continue
        self.settingsChanged.emit()
        self.viewStateChanged.emit()

    @Slot(result=bool)
    def applySettings(self) -> bool:
        """Validate the draft, save it atomically, then make it the live config.

        The old config stays in force unless the write actually succeeded, so a
        validation problem or a full disk never leaves the app half-configured.
        """
        draft = self._settings_draft()
        problems = draft.validate()
        if problems:
            self.errorMessage.emit("Settings not applied. " + " ".join(problems))
            self.settingsChanged.emit()
            return False

        previous = self._config
        try:
            save_config(draft, self._config_path)
        except OSError as exc:
            self.errorMessage.emit(
                f"Could not save settings: {exc.strerror or exc}. "
                "The previous settings are still in use."
            )
            return False

        self._config = draft
        # The voice the app speaks with follows the saved default until the next
        # Setup screen overrides it for a single session.
        self._selected_voice = draft.ttsVoice
        self._selected_rate = draft.ttsRate

        if draft.theme != previous.theme:
            # Repaint before anything else so a theme switch is visible even if
            # a later branch below reports a problem.
            self._theme.setMode(draft.theme)
            self._theme.apply()
            self._theme.applyToAllWindows()
            self.themeChanged.emit()

        sessions_path = draft.sessionsPath or str(self._storage.sessions_dir)
        if draft.sessionsPath and Path(draft.sessionsPath) != self._storage.sessions_dir:
            storage = SessionStorage(Path(draft.sessionsPath))
            try:
                storage.sessions_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # The config is already saved; report it and keep using the
                # folder that works instead of switching to a broken one.
                self.errorMessage.emit(
                    f"Cannot use sessions folder: {exc.strerror or exc}. "
                    f"History is still read from {self._storage.sessions_dir}."
                )
            else:
                self._storage = storage
        elif not draft.sessionsPath:
            self._storage = SessionStorage(Path(sessions_path))

        if draft.libraryPath != previous.libraryPath:
            self._scan_library()
        if draft.keepRecordingByDefault != previous.keepRecordingByDefault:
            self._setup["keepRecording"] = draft.keepRecordingByDefault
        if draft.recordingEnabledByDefault != previous.recordingEnabledByDefault:
            self._setup["record"] = draft.recordingEnabledByDefault
        if draft.saveTypedAnswersByDefault != previous.saveTypedAnswersByDefault:
            self._setup["saveTyped"] = draft.saveTypedAnswersByDefault

        # A fresh draft, so Apply stays disabled until the next change.
        self._draft = self._copy_config(self._config)
        self.statusMessage.emit("Settings applied.")
        self.settingsChanged.emit()
        self.setupChanged.emit()
        self.viewStateChanged.emit()
        self.refreshRequested.emit()
        return True

    @Slot()
    def discardSettings(self) -> None:
        """Throw the draft away and go back to the saved values."""
        self._draft = None
        self.statusMessage.emit("Settings changes discarded.")
        self.settingsChanged.emit()
        self.viewStateChanged.emit()

    @Slot(result=bool)
    def leaveSettings(self) -> bool:
        """Try to navigate away. False means the user must decide first.

        A silent exit would throw away work the user just did, and applying
        without asking would save changes they may only have been testing, so a
        dirty draft is resolved by an explicit choice instead.
        """
        if not self._get_settings_dirty():
            self._draft = None
            self._go("library")
            return True
        self.settingsCloseRequested.emit()
        return False

    @Slot()
    def applyAndLeaveSettings(self) -> None:
        if self.applySettings():
            self._go("library")

    @Slot()
    def discardAndLeaveSettings(self) -> None:
        self.discardSettings()
        self._go("library")

    @Slot(result=bool)
    def autoDetectWhisper(self) -> bool:
        """Fill the draft with a locally installed runtime and model."""
        runtime = self._transcriber.discover_runtime()
        model = self._transcriber.discover_model()
        patch = {}
        if runtime:
            patch["whisperCLIPath"] = runtime
        if model:
            patch["whisperModelPath"] = model
        if not patch:
            self.errorMessage.emit("No local whisper-cli or model found.")
            return False
        self.updateSettingsDraft(patch)
        self.statusMessage.emit(
            "Detected local Whisper runtime and model. Apply changes to keep them."
        )
        return True

    @Slot(str, result=bool)
    def chooseFolder(self, which: str) -> bool:
        """Pick a folder into the draft when Settings is open, else save it.

        On the Library screen there is no draft to stage into and no Apply
        button, so that choice is applied at once; from Settings it waits for
        Apply like every other field. Cancelling the dialog changes nothing.
        """
        from PySide6.QtWidgets import QFileDialog

        fields = {
            "library": "libraryPath",
            "sessions": "sessionsPath",
            "export": "exportDirectory",
        }
        field = fields.get(which)
        if field is None:
            return False

        current = self._picker_start()
        start = getattr(current, field)
        if not start:
            start = str(
                self._config.libraryPath
                or self._config.exportDirectory
                or self._storage.sessions_dir
            )
        folder = QFileDialog.getExistingDirectory(None, f"Select {which} folder", start)
        if not folder:
            return False
        self._stage_or_apply({field: folder})
        return True

    @Slot(str, result=bool)
    def chooseFile(self, which: str) -> bool:
        from PySide6.QtWidgets import QFileDialog

        draft = self._settings_draft()
        if which == "whisperCLI":
            path, _ = QFileDialog.getOpenFileName(
                None, "Select whisper-cli executable", self._picker_start().whisperCLIPath or "/opt/homebrew/bin"
            )
            if not path:
                return False
            self._stage_or_apply({"whisperCLIPath": path})
            return True
        if which == "whisperModel":
            path, _ = QFileDialog.getOpenFileName(
                None, "Select Whisper model (.bin)", self._picker_start().whisperModelPath or str(Path.home() / "whisper" / "models")
            )
            if not path:
                return False
            self._stage_or_apply({"whisperModelPath": path})
            return True
        return False

    def _picker_start(self) -> AppConfig:
        """The config a picker should open at: the draft in Settings, else saved."""
        if self._settings_open() and self._draft is not None:
            return self._draft
        return self._config

    def _settings_open(self) -> bool:
        """Whether a draft has a screen to be staged into.

        The draft itself is not the signal: applying leaves a fresh draft
        behind while the user is already back on the Library screen, and
        staging into that invisible copy would drop the choice instead of
        saving it.
        """
        return self._screen == "settings"

    def _stage_or_apply(self, patch: dict) -> None:
        """Write a picker result to the draft, or save it when there is none.

        See `chooseFolder`: a picker opened outside Settings has nowhere to
        stage its result, so it applies immediately instead of dropping it.
        """
        if self._settings_open():
            self._settings_draft()
            self.updateSettingsDraft(patch)
            return
        self._draft = self._copy_config(self._config)
        self.updateSettingsDraft(patch)
        self.applySettings()

    @Slot(result="QVariantMap")
    def deleteTemporaryRecordings(self) -> dict:
        """Delete leftover temporary recordings found in the temp directory.

        Only orphan temp WAVs are removed; session folders, retained recordings
        and user content are not reachable from here.
        """
        removed, failures = self._storage.delete_orphan_temp_audio()
        self.refresh_orphan_count()
        if failures:
            self.errorMessage.emit(
                f"Deleted {removed} temporary recording(s). "
                f"{len(failures)} could not be removed: " + "; ".join(failures)
            )
        elif removed:
            self.statusMessage.emit(f"Deleted {removed} temporary recording file(s).")
        else:
            self.statusMessage.emit("There were no temporary recordings to delete.")
        self.settingsChanged.emit()
        self.viewStateChanged.emit()
        return {"removed": removed, "failed": len(failures), "errors": failures}

    def refresh_orphan_count(self) -> None:
        self._orphan_count = len(self._storage.orphan_temp_audio())


def QtQueuedConnection():
    from PySide6.QtCore import Qt

    return Qt.ConnectionType.QueuedConnection
