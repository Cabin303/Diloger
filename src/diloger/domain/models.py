"""Domain models. No Qt, no audio, no filesystem dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def iso_utc(epoch: Optional[float]) -> Optional[str]:
    """Wall-clock timestamp as ISO 8601 UTC.

    Durable files must never carry a bare monotonic number where another
    process could read it as a date in 1970.
    """
    if epoch is None:
        return None
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class Mode(str, Enum):
    SPOKEN = "spoken"
    WRITTEN = "written"


class SpokenProgression(str, Enum):
    FIXED_TIMER = "fixedTimer"
    MANUAL = "manual"


class WrittenPromptVariant(str, Enum):
    AUDIO = "audio"
    TEXT = "text"


class Order(str, Enum):
    SEQUENTIAL = "sequential"
    RANDOM_WITHOUT_REPETITION = "randomWithoutRepetition"


class SessionState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    SPEAKING = "speaking"
    WAITING = "waiting"
    TYPING = "typing"
    PAUSED = "paused"
    AT_END = "atEnd"
    FINISHED = "finished"


class EventType(str, Enum):
    SESSION_START = "sessionStart"
    PROMPT_START = "promptStart"
    TTS_START = "ttsStart"
    TTS_FINISH = "ttsFinish"
    TTS_ERROR = "ttsError"
    REPEAT = "repeat"
    REVEAL = "reveal"
    SKIP = "skip"
    PAUSE = "pause"
    RESUME = "resume"
    NEXT = "next"
    TIMER_START = "timerStart"
    TIMER_EXPIRE = "timerExpire"
    ANSWER_TYPED = "answerTyped"
    CONTENT_EXHAUSTED = "contentExhausted"
    RECORDING_START = "recordingStart"
    RECORDING_STOP = "recordingStop"
    RECORDING_ERROR = "recordingError"
    SESSION_STOP = "sessionStop"
    TRANSCRIPTION_START = "transcriptionStart"
    TRANSCRIPTION_FINISH = "transcriptionFinish"
    TRANSCRIPTION_ERROR = "transcriptionError"
    TRANSCRIPTION_CANCELLED = "transcriptionCancelled"
    ERROR = "error"


class SessionStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    EXHAUSTED = "exhausted"


class TranscriptionStatus(str, Enum):
    NOT_REQUESTED = "notRequested"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Prompt:
    id: str
    ordinal: int
    text: str


@dataclass(frozen=True)
class ContentSet:
    id: str
    sourcePath: str
    displayName: str
    format: str
    prompts: tuple[Prompt, ...]

    def __len__(self) -> int:
        return len(self.prompts)


@dataclass(frozen=True)
class SessionConfig:
    mode: Mode = Mode.SPOKEN
    spokenProgression: Optional[SpokenProgression] = None
    writtenPrompt: Optional[WrittenPromptVariant] = None
    order: Order = Order.SEQUENTIAL
    configuredDelaySeconds: float = 5.0
    recordingEnabled: bool = False
    keepRecording: bool = False
    saveTypedAnswers: bool = False
    ttsProvider: str = "macos_say"
    ttsVoice: str = "Kokoro Heart"
    ttsRate: int = 175
    whisperModelPath: str = ""

    def with_whisper_model(self, path: str) -> "SessionConfig":
        if path == self.whisperModelPath:
            return self
        from dataclasses import replace

        return replace(self, whisperModelPath=path)


@dataclass
class Event:
    type: EventType
    atOffset: float
    promptId: Optional[str] = None
    detail: Optional[str] = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict = {"type": self.type.value, "atOffset": round(self.atOffset, 3)}
        if self.promptId is not None:
            out["promptId"] = self.promptId
        if self.detail is not None:
            out["detail"] = self.detail
        if self.data:
            out["data"] = self.data
        return out


@dataclass
class Session:
    sessionId: str
    contentSourcePath: str
    contentFingerprint: str
    config: SessionConfig
    promptOrder: list[str]
    wallStartedAt: float
    startedAt: float = 0.0
    promptsReached: int = 0
    status: SessionStatus = SessionStatus.RUNNING
    wallEndedAt: Optional[float] = None
    endedAt: Optional[float] = None
    recordingPath: Optional[str] = None
    transcriptJsonPath: Optional[str] = None
    transcriptMarkdownPath: Optional[str] = None
    transcriptionStatus: TranscriptionStatus = TranscriptionStatus.NOT_REQUESTED
    recordingRetained: Optional[bool] = None
    typedAnswers: dict = field(default_factory=dict)
    eventLog: list[Event] = field(default_factory=list)

    @property
    def durationSeconds(self) -> float:
        if self.endedAt is None or self.startedAt is None:
            return round(max(0.0, (self.endedAt or self.startedAt) - self.startedAt), 3)
        return round(self.endedAt - self.startedAt, 3)

    def apply_whisper_model(self, path: str) -> None:
        """Record the model actually handed to whisper-cli for this session."""
        self.config = self.config.with_whisper_model(path)

    def to_dict(self) -> dict:
        return {
            "sessionId": self.sessionId,
            "startedAt": iso_utc(self.wallStartedAt),
            "endedAt": iso_utc(self.wallEndedAt),
            "startedAtEpoch": round(self.wallStartedAt, 3),
            "endedAtEpoch": (
                round(self.wallEndedAt, 3) if self.wallEndedAt is not None else None
            ),
            "durationSeconds": self.durationSeconds,
            "contentSourcePath": self.contentSourcePath,
            "contentFingerprint": self.contentFingerprint,
            "mode": self.config.mode.value,
            "spokenProgression": (
                self.config.spokenProgression.value if self.config.spokenProgression else None
            ),
            "writtenPrompt": (
                self.config.writtenPrompt.value if self.config.writtenPrompt else None
            ),
            "order": self.config.order.value,
            "configuredDelaySeconds": self.config.configuredDelaySeconds,
            "promptOrder": self.promptOrder,
            "promptsReached": self.promptsReached,
            # History and the Markdown export show "8 / 8"; the order carries
            # every prompt the session was built from, so the total is derived
            # rather than remembered separately and drifting.
            "totalPrompts": len(self.promptOrder),
            "recordingEnabled": self.config.recordingEnabled,
            "keepRecording": self.config.keepRecording,
            "saveTypedAnswers": self.config.saveTypedAnswers,
            "ttsProvider": self.config.ttsProvider,
            "ttsVoice": self.config.ttsVoice,
            "ttsRate": self.config.ttsRate,
            "whisperModelPath": self.config.whisperModelPath,
            "recordingPath": self.recordingPath,
            "transcriptJsonPath": self.transcriptJsonPath,
            "transcriptMarkdownPath": self.transcriptMarkdownPath,
            "status": self.status.value,
            "transcriptionStatus": self.transcriptionStatus.value,
            "recordingRetained": self.recordingRetained,
            "typedAnswers": self.typedAnswers,
            "eventLog": [e.to_dict() for e in self.eventLog],
        }
