"""Session Engine: explicit state machine. No Qt, no audio runtime knowledge."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from diloger.content.fingerprint import fingerprint
from diloger.content.order import build_order
from diloger.domain.models import (
    ContentSet,
    Event,
    EventType,
    Mode,
    Order,
    Session,
    SessionConfig,
    SessionState,
    SessionStatus,
    SpokenProgression,
    TranscriptionStatus,
    WrittenPromptVariant,
)
from diloger.domain.ports import Clock, Recorder, TtsProvider


class SessionEngine:
    """Owns progression, timing and the event log.

    UI observes via callbacks. TTS/recorder are driven through ports so the
    engine can be tested with fakes.
    """

    def __init__(
        self,
        tts: TtsProvider,
        recorder: Optional[Recorder],
        clock: Clock,
        content: ContentSet,
        config: SessionConfig,
        storage_sessions_dir: Optional[Path] = None,
        on_state_changed: Optional[Callable[[], None]] = None,
    ) -> None:
        self._tts = tts
        self._recorder = recorder
        self._clock = clock
        self._content = content
        self._config = config
        self._on_state_changed = on_state_changed

        self._order: list[str] = build_order(content.prompts, config.order)
        self._index = 0
        self._revealed = False
        self._paused = False
        self._timer_remaining = 0.0
        self._timer_started_at: Optional[float] = None
        self._finished = False
        self._resume_replays = False

        now = clock.now()
        self._session = Session(
            sessionId=uuid.uuid4().hex[:8],
            contentSourcePath=content.sourcePath,
            contentFingerprint=fingerprint(Path(content.sourcePath)),
            config=config,
            promptOrder=list(self._order),
            wallStartedAt=time.time(),
            startedAt=now,
        )

        # Bind the recorder to this session so its temporary WAV is unique and
        # a stale file from an earlier session can never be reused or deleted.
        prepare = getattr(self._recorder, "prepare_for_session", None)
        if callable(prepare):
            prepare(self._session.sessionId)

        if storage_sessions_dir is not None:
            self._sessions_dir = Path(storage_sessions_dir)
        else:
            self._sessions_dir = None

        self._state = SessionState.IDLE
        self._typed_answer = ""

    # ---- state ---------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session(self) -> Session:
        return self._session

    @property
    def is_finished(self) -> bool:
        return self._finished

    @property
    def revealed(self) -> bool:
        return self._revealed

    @property
    def total_prompts(self) -> int:
        return len(self._order)

    @property
    def current_prompt(self):
        if self._index >= len(self._order):
            return None
        pid = self._order[self._index]
        for p in self._content.prompts:
            if p.id == pid:
                return p
        return None

    @property
    def current_position(self) -> int:
        return min(self._index + 1, len(self._order))

    @property
    def is_last_prompt(self) -> bool:
        """True while the trainer stands on the final item of the order.

        Distinct from `state is AT_END`, which only happens after the user
        presses Next/Skip on that item. The UI needs to say "this is the last
        prompt" before the user has already gone past it.
        """
        return self._index + 1 >= len(self._order) and not self._finished

    @property
    def prompt_text_visible(self) -> bool:
        if self._config.mode is Mode.WRITTEN:
            if self._config.writtenPrompt is WrittenPromptVariant.TEXT:
                return True
        return self._revealed

    def timer_remaining(self) -> float:
        if self._state not in (SessionState.WAITING, SessionState.PAUSED):
            return 0.0
        if self._timer_started_at is None:
            return self._timer_remaining
        elapsed = self._clock.now() - self._timer_started_at
        return max(0.0, self._timer_remaining - elapsed)

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._state is not SessionState.IDLE:
            return
        self._log(EventType.SESSION_START, detail=self._config.mode.value)
        self._set_state(SessionState.PREPARING)
        if self._config.recordingEnabled and self._recorder is not None:
            self._start_recording()
        self._load_current(advance_index=False)

    def stop(self) -> None:
        if self._finished:
            return
        self._clock.cancel()
        self._tts.stop()
        if self._config.recordingEnabled and self._recorder is not None:
            path = self._recorder.stop()
            if path is not None:
                self._session.recordingPath = str(path)
                self._log(EventType.RECORDING_STOP, detail=str(path))
        self._session.endedAt = self._clock.now()
        self._session.wallEndedAt = time.time()
        self._session.status = SessionStatus.STOPPED
        self._log(EventType.SESSION_STOP, detail=f"reached={self._session.promptsReached}")
        self._finished = True
        self._set_state(SessionState.FINISHED)

    # ---- prompt progression -------------------------------------------

    def next(self) -> None:
        if self._finished or self._paused:
            return
        self._clock.cancel()
        self._timer_started_at = None
        self._timer_remaining = 0.0
        if self._config.mode is Mode.WRITTEN:
            self._capture_typed_answer()

        if self._index + 1 >= len(self._order):
            self._log(EventType.CONTENT_EXHAUSTED, detail="last item")
            self._set_state(SessionState.AT_END)
            return

        self._log(EventType.NEXT, prompt_id=self._current_id())
        self._index += 1
        self._load_current(advance_index=False)

    def skip(self) -> None:
        if self._finished or self._paused:
            return
        self._clock.cancel()
        self._timer_started_at = None
        self._timer_remaining = 0.0
        if self._config.mode is Mode.WRITTEN:
            self._capture_typed_answer()
        self._log(EventType.SKIP, prompt_id=self._current_id())

        if self._index + 1 >= len(self._order):
            self._log(EventType.CONTENT_EXHAUSTED, detail="skipped last item")
            self._set_state(SessionState.AT_END)
            return

        self._index += 1
        self._load_current(advance_index=False)

    def repeat(self) -> None:
        """Replay the current prompt. Does not change index."""
        if self._finished or self._paused or self._state is SessionState.AT_END:
            return
        self._clock.cancel()
        self._timer_started_at = None
        self._timer_remaining = 0.0
        self._log(EventType.REPEAT, prompt_id=self._current_id())
        self._speak_current()

    def reveal(self) -> None:
        self._revealed = True
        self._log(EventType.REVEAL, prompt_id=self._current_id())

    def hide(self) -> None:
        self._revealed = False

    # ---- pause ---------------------------------------------------------

    def pause(self) -> None:
        if self._finished or self._paused or self._state is SessionState.AT_END:
            return
        # Pause while the answer window is running: freeze the remaining time
        # and continue it on resume. Pause during speech: replay the prompt.
        self._resume_replays = self._state is not SessionState.WAITING
        if self._state is SessionState.WAITING and self._timer_started_at is not None:
            self._timer_remaining = max(
                0.0, self._timer_remaining - (self._clock.now() - self._timer_started_at)
            )
        self._clock.cancel()
        self._timer_started_at = None
        self._tts.stop()
        self._paused = True
        self._log(EventType.PAUSE, prompt_id=self._current_id())
        self._set_state(SessionState.PAUSED)

    def resume(self) -> None:
        if self._finished or not self._paused:
            return
        self._paused = False
        self._log(EventType.RESUME, prompt_id=self._current_id())

        if self._resume_replays and self._needs_audio():
            self._speak_current()
            return
        if self._config.mode is Mode.WRITTEN:
            self._set_state(SessionState.TYPING)
            return

        self._set_state(SessionState.WAITING)
        if (
            self._config.spokenProgression is SpokenProgression.FIXED_TIMER
            and self._timer_remaining > 0
        ):
            self._timer_started_at = self._clock.now()
            self._clock.schedule(self._timer_remaining, self._on_timer_expired)

    # ---- written mode ---------------------------------------------------

    def set_typed_answer(self, text: str) -> None:
        self._typed_answer = text

    def _capture_typed_answer(self) -> None:
        if self._config.mode is not Mode.WRITTEN:
            return
        pid = self._current_id()
        if pid is None:
            return
        if not self._config.saveTypedAnswers:
            self._typed_answer = ""
            return
        answer = self._typed_answer.strip()
        if answer:
            self._session.typedAnswers[pid] = answer
            self._log(EventType.ANSWER_TYPED, prompt_id=pid, detail=answer)
        self._typed_answer = ""

    # ---- internals -----------------------------------------------------

    def _current_id(self) -> Optional[str]:
        if self._index >= len(self._order):
            return None
        return self._order[self._index]

    def _needs_audio(self) -> bool:
        if self._config.mode is Mode.SPOKEN:
            return True
        return self._config.writtenPrompt is WrittenPromptVariant.AUDIO

    def _set_state(self, state: SessionState) -> None:
        self._state = state
        if self._on_state_changed is not None:
            self._on_state_changed()

    def _log(
        self,
        event_type: EventType,
        prompt_id: Optional[str] = None,
        detail: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> None:
        # Stored as an offset from session start: a monotonic clock value
        # would be meaningless (1970-looking) in a durable file.
        self._session.eventLog.append(
            Event(
                type=event_type,
                atOffset=self._clock.now() - self._session.startedAt,
                promptId=prompt_id,
                detail=detail,
                data=data or {},
            )
        )

    def _load_current(self, advance_index: bool) -> None:
        prompt = self.current_prompt
        if prompt is None:
            self._set_state(SessionState.AT_END)
            return
        if advance_index:
            self._index += 1
        self._revealed = False
        self._session.promptsReached = max(self._session.promptsReached, self._index + 1)
        self._log(EventType.PROMPT_START, prompt_id=prompt.id, detail=str(self._index + 1))
        if self._index + 1 >= len(self._order):
            # Record the boundary, but keep waiting on this item: the session
            # must not loop and must not end without an explicit user action.
            self._log(EventType.CONTENT_EXHAUSTED, detail="last item reached")

        if self._needs_audio():
            self._speak_current()
        elif self._config.mode is Mode.WRITTEN:
            self._set_state(SessionState.TYPING)
        else:
            self._set_state(SessionState.WAITING)

    def _speak_current(self) -> None:
        prompt = self.current_prompt
        if prompt is None:
            self._set_state(SessionState.AT_END)
            return
        self._set_state(SessionState.SPEAKING)
        self._log(EventType.TTS_START, prompt_id=prompt.id, detail=self._config.ttsVoice)
        self._tts.speak(
            prompt.text,
            on_started=lambda: None,
            on_finished=self._on_tts_finished,
            on_error=self._on_tts_error,
        )

    def _on_tts_finished(self) -> None:
        if self._finished or self._paused:
            return
        prompt = self.current_prompt
        pid = prompt.id if prompt else None
        self._log(EventType.TTS_FINISH, prompt_id=pid)

        if self._config.mode is Mode.WRITTEN:
            self._set_state(SessionState.TYPING)
            return

        if self._config.spokenProgression is SpokenProgression.FIXED_TIMER:
            self._timer_remaining = float(self._config.configuredDelaySeconds)
            self._timer_started_at = self._clock.now()
            self._set_state(SessionState.WAITING)
            self._log(
                EventType.TIMER_START,
                prompt_id=pid,
                detail=str(self._config.configuredDelaySeconds),
            )
            self._clock.schedule(self._config.configuredDelaySeconds, self._on_timer_expired)
        else:
            self._set_state(SessionState.WAITING)

    def _on_timer_expired(self) -> None:
        if self._finished or self._paused:
            return
        self._timer_started_at = None
        self._timer_remaining = 0.0
        self._log(EventType.TIMER_EXPIRE, prompt_id=self._current_id())
        self.next()

    def _on_tts_error(self, message: str) -> None:
        self._log(EventType.TTS_ERROR, prompt_id=self._current_id(), detail=message)
        self._set_state(SessionState.WAITING)

    # ---- recording ------------------------------------------------------

    def _start_recording(self) -> None:
        if self._recorder is None:
            return
        try:
            self._recorder.start()
        except Exception as exc:  # recording failure must not kill the session
            self._log(EventType.RECORDING_ERROR, detail=str(exc))
            self._session.config = SessionConfig(**{**self._config.__dict__, "recordingEnabled": False})
            self._config = self._session.config
            return
        self._log(EventType.RECORDING_START)

    def recording_path(self) -> Optional[Path]:
        return Path(self._session.recordingPath) if self._session.recordingPath else None

    def set_transcription_status(self, status: TranscriptionStatus) -> None:
        self._session.transcriptionStatus = status
