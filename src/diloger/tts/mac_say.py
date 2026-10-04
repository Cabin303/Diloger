"""macOS `say` TTS provider. Subprocess with argument array, never shell string."""

from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

from PySide6.QtCore import QObject, QProcess, QTimer

FALLBACK_VOICES = ("Kokoro Heart", "Kokoro Michael", "Samantha", "Alex", "Daniel")


class MacSayTts(QObject):
    """Text-to-speech via /usr/bin/say.

    Uses QProcess so the Qt event loop is never blocked. A call to `speak()`
    while another utterance is active stops the previous one first.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._say_path = "/usr/bin/say"
        self._voice = "Kokoro Heart"
        self._rate = 175
        self._proc: Optional[QProcess] = None
        self._on_finished: Optional[Callable[[], None]] = None
        self._on_started: Optional[Callable[[], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._emit_finished)

    # ---- configuration -------------------------------------------------

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def rate(self) -> int:
        return self._rate

    def set_voice(self, voice: str) -> None:
        self._voice = voice

    def set_rate(self, rate: int) -> None:
        self._rate = int(rate)

    def is_available(self) -> bool:
        return Path_is_file(self._say_path)

    def available_voices(self) -> list[str]:
        if not self.is_available():
            return []
        try:
            out = subprocess.run(
                [self._say_path, "-v", "?"], capture_output=True, timeout=15
            ).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            return []
        voices = []
        for line in out.splitlines():
            if "en_" in line and line.strip():
                name = line.split("  ")[0].strip()
                if name:
                    voices.append(name)
        return voices

    def is_voice_available(self, voice: str) -> bool:
        return voice in self.available_voices()

    # ---- playback ------------------------------------------------------

    def speak(
        self,
        text: str,
        on_started: Callable[[], None],
        on_finished: Callable[[], None],
        on_error: Callable[[str], str],
    ) -> None:
        self.stop()
        self._on_started = on_started
        self._on_finished = on_finished
        self._on_error = on_error

        if not self.is_available():
            self._fail("/usr/bin/say not found on this system")
            return
        if not text.strip():
            self._fail("Empty prompt text")
            return

        proc = QProcess(self)
        proc.setProgram(self._say_path)
        proc.setArguments(["-v", self._voice, "-r", str(self._rate), "--", text])
        proc.finished.connect(self._on_process_finished)
        proc.errorOccurred.connect(self._on_process_error)
        self._proc = proc
        proc.start()
        if on_started is not None:
            QTimer.singleShot(0, on_started)

    def stop(self) -> None:
        self._settle.stop()
        self._on_finished = None
        self._on_started = None
        if self._proc is not None:
            proc = self._proc
            self._proc = None
            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(800):
                    proc.kill()
                    proc.waitForFinished(500)
            proc.deleteLater()

    # ---- internals -----------------------------------------------------

    def _on_process_finished(self, exit_code: int, exit_status) -> None:
        if self._proc is None:
            return
        if exit_code != 0:
            self._fail(f"say exited with code {exit_code}")
            return
        # `say` returns a moment before Core Audio finishes draining; a short
        # settle keeps the Fixed Timer anchored after real playback ends.
        self._settle.start(120)

    def _on_process_error(self, _error) -> None:
        if self._proc is None:
            return
        self._fail("say process failed to start")

    def _emit_finished(self) -> None:
        cb = self._on_finished
        self._on_finished = None
        if cb is not None:
            cb()

    def _fail(self, message: str) -> None:
        cb = self._on_error
        self._on_finished = None
        if cb is not None:
            cb(message)


def Path_is_file(path: str) -> bool:
    import os

    return os.path.isfile(path) and os.access(path, os.X_OK)
