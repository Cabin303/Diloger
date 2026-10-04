"""Protocols (ports) for every replaceable adapter. No implementations here."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol


class TtsProvider(Protocol):
    """Speaks text. Must report completion, failure, and support stop."""

    def available_voices(self) -> list[str]: ...

    def is_voice_available(self, voice: str) -> bool: ...

    def speak(
        self,
        text: str,
        on_started: Callable[[], None],
        on_finished: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None: ...

    def stop(self) -> None: ...


class Recorder(Protocol):
    def is_available(self) -> bool: ...

    def list_devices(self) -> list[str]: ...

    def start(self, path: Path) -> None: ...

    def stop(self) -> Optional[Path]: ...

    def abort(self) -> Optional[Path]: ...


class Transcriber(Protocol):
    def is_available(self) -> tuple[bool, str]: ...

    def transcribe(
        self,
        audio_path: Path,
        model_path: str,
        on_output: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> tuple[bool, str]: ...


class Clock(Protocol):
    def now(self) -> float: ...

    def schedule(self, delay: float, callback: Callable[[], None]) -> None: ...

    def cancel(self) -> None: ...


class Storage(Protocol):
    def create_session_dir(self, started_at: float) -> Path: ...

    def temp_audio_path(self) -> Path: ...

    def write_session_json(self, session_dir: Path, payload: dict) -> Path: ...

    def write_transcript_json(self, session_dir: Path, payload: dict) -> Path: ...

    def write_transcript_markdown(self, session_dir: Path, content: str) -> Path: ...

    def orphan_temp_audio(self) -> list[Path]: ...

    def delete(self, path: Path) -> bool: ...
