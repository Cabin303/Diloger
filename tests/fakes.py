"""Fakes for integration tests. No real microphone, model, or audio device."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Callable, Optional


class FakeClock:
    def __init__(self) -> None:
        self._t = 1000.0
        self._callbacks: list[Callable[[], None]] = []
        self._cancelled: list[Callable[[], None]] = []

    def now(self) -> float:
        return self._t

    def schedule(self, delay: float, callback: Callable[[], None]) -> None:
        self._cancelled.append(callback)
        self._callbacks.append((self._t + delay, callback))

    def cancel(self) -> None:
        self._callbacks.clear()

    def advance(self, dt: float) -> None:
        self._t += dt
        due = [c for (when, c) in self._callbacks if when <= self._t]
        self._callbacks = [(w, c) for (w, c) in self._callbacks if w > self._t]
        for cb in due:
            cb()


class FakeTts:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.voice = "Kokoro Heart"
        self.rate = 175
        self.spoken: list[str] = []
        self._fin: Optional[Callable[[], None]] = None
        self._err: Optional[Callable[[str], None]] = None
        self.auto_finish = True
        self.error_message = "fake TTS failure"

    def available_voices(self) -> list[str]:
        return ["Kokoro Heart", "Kokoro Michael"] if self.available else []

    def is_voice_available(self, voice: str) -> bool:
        return self.available and voice in ("Kokoro Heart", "Kokoro Michael")

    def speak(self, text, on_started, on_finished, on_error) -> None:
        self.spoken.append(text)
        self._fin = on_finished
        self._err = on_error
        on_started()
        if self.available and self.auto_finish:
            self.finish()

    def finish(self) -> None:
        if self._fin is not None:
            cb, self._fin = self._fin, None
            cb()

    def fail(self) -> None:
        if self._err is not None:
            cb, self._err = self._err, None
            cb(self.error_message)

    def stop(self) -> None:
        self._fin = None
        self._err = None

    def set_voice(self, voice: str) -> None:
        self.voice = voice

    def set_rate(self, rate: int) -> None:
        self.rate = rate


class FakeRecorder:
    def __init__(
        self,
        available: bool = True,
        fail_on_start: bool = False,
        target_dir: Optional[Path] = None,
    ) -> None:
        self.available = available
        self.fail_on_start = fail_on_start
        self.started = False
        self.path: Optional[Path] = None
        self.error: Optional[str] = None
        # Recording into the real system temp directory left a WAV in /tmp for
        # every test run, and made the fake depend on a writable /tmp. Tests
        # that need a real file pass their own directory.
        self.target_dir = Path(target_dir) if target_dir else None

    def is_available(self) -> bool:
        return self.available

    def list_devices(self) -> list[str]:
        return ["Fake Mic"] if self.available else []

    def start(self, path: Optional[Path] = None) -> None:
        if self.fail_on_start:
            raise RuntimeError("fake recorder failure")
        if not self.available:
            raise RuntimeError("no input device")
        self.started = True
        directory = self.target_dir or Path(tempfile.gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "diloger-fake.wav"
        self.path.write_bytes(b"RIFF" + b"\0" * 60)

    def stop(self) -> Optional[Path]:
        self.started = False
        return self.path

    def abort(self) -> Optional[Path]:
        return self.stop()


class FakeTranscriber:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[Path, str]] = []
        self.cancelled_check = 0

    def is_available(self) -> tuple[bool, str]:
        return (self.ok, "ok" if self.ok else "missing")

    def transcribe(self, audio_path, model_path, on_output=None, should_cancel=None) -> tuple[bool, str]:
        self.calls.append((Path(audio_path), model_path))
        if should_cancel is not None and should_cancel():
            self.cancelled_check += 1
            return False, "cancelled"
        return self.ok, "ok"
