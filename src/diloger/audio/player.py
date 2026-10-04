"""Local playback of an existing recording.wav. Read-only, no copying."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal, Slot

# Starting playback level for the user's own recording: audible without being
# loud enough to distort on a recording that was captured near full scale.
SAFE_VOLUME = 0.8


class RecordingPlayer(QObject):
    """Plays a WAV that already exists on disk.

    Deliberately separate from the recorder and the TTS: playback is a review
    aid for the user's own audio, not part of session progression.
    """

    error = Signal(str)
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(str)
    sourceChanged = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._player = None
        self._audio = None
        self._source: Optional[Path] = None
        self._build()

    def _build(self) -> None:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        # A safe starting level, clamped so a bad value can never be pushed
        # past full scale. No normalisation, compression or voice processing:
        # the user's own recording is played as it was captured.
        self.setVolume(SAFE_VOLUME)
        self._player.setAudioOutput(self._audio)
        self._player.errorOccurred.connect(self._on_error)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)

    @Slot(float)
    def setVolume(self, value: float) -> None:
        from PySide6.QtMultimedia import QAudioOutput

        if self._audio is None:
            return
        try:
            level = float(value)
        except (TypeError, ValueError):
            return
        self._audio.setVolume(min(1.0, max(0.0, level)))

    @Slot(result=float)
    def volume(self) -> float:
        from PySide6.QtMultimedia import QAudioOutput

        if self._audio is None:
            return 0.0
        return float(self._audio.volume())

    # ---- source ---------------------------------------------------------

    @Slot(result=str)
    def sourcePath(self) -> str:
        return str(self._source) if self._source else ""

    @Slot(result=int)
    def durationMs(self) -> int:
        try:
            return int(self._player.duration())
        except Exception:
            return 0

    @Slot(result=int)
    def positionMs(self) -> int:
        try:
            return int(self._player.position())
        except Exception:
            return 0

    @Slot(result=bool)
    def isPlaying(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @Slot(result=bool)
    def hasSource(self) -> bool:
        return self._source is not None and self._source.is_file()

    # Declared with its argument: a bare `@Slot()` exposes a zero-argument method,
    # which is the same trap `seekTo` had. QML currently reaches playback through
    # the controller, but the signature should describe the method either way.
    @Slot(str)
    def load(self, path: str) -> None:
        """Point at an existing local recording. Nothing is copied."""
        self.stop()
        if not path:
            self._source = None
            self.sourceChanged.emit("")
            self.durationChanged.emit(0)
            return
        target = Path(path)
        if not target.is_file():
            self._source = None
            self.sourceChanged.emit("")
            self.error.emit(f"No recording to play: {target}")
            return
        self._source = target
        self._player.setSource(QUrl.fromLocalFile(str(target)))
        self.sourceChanged.emit(str(target))

    @Slot()
    def clearSource(self) -> None:
        self.stop()
        self._source = None
        self._player.setSource(QUrl())
        self.sourceChanged.emit("")
        self.durationChanged.emit(0)

    # ---- transport ------------------------------------------------------

    @Slot()
    def play(self) -> None:
        if self._source is None or not self._source.is_file():
            self.error.emit("There is no recording to play.")
            return
        self._player.play()

    @Slot()
    def pause(self) -> None:
        self._player.pause()

    @Slot()
    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    # The argument must be declared in the Slot signature. With a bare `@Slot()`
    # PySide6 exposes the method as taking no arguments, so every `seekTo(ms)`
    # from QML raised "missing 1 required positional argument" and the slider and
    # the Stop rewind silently did nothing.
    @Slot(int)
    def seekTo(self, position_ms: int) -> None:
        try:
            self._player.setPosition(max(0, int(position_ms)))
        except Exception:
            pass

    # ---- internals ------------------------------------------------------

    def _on_error(self, _error, message: str = "") -> None:
        if message:
            self.error.emit(f"Playback failed: {message}")

    def _on_position(self, position: int) -> None:
        self.positionChanged.emit(int(position))

    def _on_duration(self, duration: int) -> None:
        self.durationChanged.emit(int(duration))

    def _on_state(self, _state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        names = {
            QMediaPlayer.PlaybackState.PlayingState: "playing",
            QMediaPlayer.PlaybackState.PausedState: "paused",
            QMediaPlayer.PlaybackState.StoppedState: "stopped",
        }
        self.playbackStateChanged.emit(names.get(self._player.playbackState(), "stopped"))

    def shutdown(self) -> None:
        self.stop()
        if self._player is not None:
            self._player.setSource(QUrl())