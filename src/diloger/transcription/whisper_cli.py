"""Local whisper-cli (whisper.cpp) adapter. Runs only after a session ends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

CANDIDATE_RUNTIMES = ("whisper-cli", "whisper-cpp", "main")
# Model names to look for, in preference order: turbo first because it is the
# smaller and faster of the two. These are only a fallback for when Settings has
# no model path yet; a model the user configured is always the one that is used.
CANDIDATE_MODEL_NAMES = ("ggml-large-v3-turbo.bin", "ggml-large-v3.bin")
# Conventional local model directories, relative to the user's home so that no
# absolute path is baked into the source.
CANDIDATE_MODEL_DIRS = (
    "~/whisper/models",
    "~/.cache/whisper.cpp",
    "~/models/whisper",
)


def candidate_models() -> tuple[str, ...]:
    """Model paths to probe, without assuming whose home directory this is."""
    override = os.environ.get("DILOGER_WHISPER_MODELS")
    dirs = [override] if override else list(CANDIDATE_MODEL_DIRS)
    return tuple(
        str(path)
        for directory in dirs
        for name in CANDIDATE_MODEL_NAMES
        for path in (Path(directory).expanduser() / name,)
    )
# Whisper on a long recording can legitimately take minutes, but a wedged
# process must not keep the UI in a transcribing state forever.
TIMEOUT_MS = 30 * 60 * 1000


class WhisperTranscriber(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._out_path: Path | None = None
        self._out_dir: Path | None = None
        self._cancelled = False
        self._timed_out = False
        self._settled = False
        self._timeout: QTimer | None = None

    # ---- discovery ------------------------------------------------------

    def discover_runtime(self) -> str | None:
        for name in CANDIDATE_RUNTIMES:
            found = shutil.which(name)
            if found:
                return found
        return None

    def discover_model(self) -> str | None:
        for candidate in candidate_models():
            if Path(candidate).is_file():
                return candidate
        return None

    def is_available(self) -> tuple[bool, str]:
        runtime = self.discover_runtime()
        if runtime is None:
            return False, "whisper-cli not found. Set its path in Settings."
        model = self.discover_model()
        if model is None:
            return False, "No local Whisper model found. Set a model path in Settings."
        return True, f"{runtime} + {Path(model).name}"

    def check_paths(self, runtime: str, model_path: str) -> tuple[bool, str]:
        if not runtime or not Path(runtime).is_file():
            return False, f"Whisper CLI not found at: {runtime or '(empty)'}"
        if not model_path or not Path(model_path).is_file():
            return False, f"Whisper model not found at: {model_path or '(empty)'}"
        return True, "ok"

    # ---- transcription --------------------------------------------------

    def transcribe(
        self,
        audio_path: Path,
        runtime: str,
        model_path: str,
        on_output: Callable[[str], None] | None = None,
    ) -> None:
        audio_path = Path(audio_path)
        # Reset the one-shot guard before any validation, otherwise an early
        # return would be swallowed by the previous run's settled flag and the
        # caller would wait forever with no callback.
        self._settled = False
        self._cancelled = False
        self._timed_out = False
        self._cleanup_output_dir()
        self._stop_timer()
        self._proc = None

        if not audio_path.is_file():
            self._settle(False, f"Recording not found: {audio_path}")
            return
        if audio_path.stat().st_size <= 44:
            self._settle(False, "Recording is empty.")
            return

        ok, reason = self.check_paths(runtime, model_path)
        if not ok:
            self._settle(False, reason)
            return

        self.cancel()  # never leave a previous run hanging
        self._cancelled = False
        self._timed_out = False
        self._settled = False
        self._out_dir = Path(tempfile.mkdtemp(prefix="diloger-whisper-"))
        self._out_path = self._out_dir / "out"

        proc = QProcess(self)
        proc.setProgram(runtime)
        proc.setArguments(
            ["-m", model_path, "-l", "en", "-oj", "-of", str(self._out_path), str(audio_path)]
        )
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc

        # A real wall-clock guard: without it a wedged whisper-cli would keep
        # the transcription state stuck forever.
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(TIMEOUT_MS)
        timer.timeout.connect(self._on_timeout)
        self._timeout = timer

        proc.start()
        timer.start()
        self.progress.emit(f"Transcribing with {Path(model_path).name}...")

    def cancel(self) -> None:
        if self._proc is None and self._timeout is None:
            # Nothing is running; cancelling still discards any scratch output.
            self._cleanup_output_dir()
            return
        self._cancelled = True
        self._stop_timer()
        proc = self._proc
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()
        # Let the process notifications finish the run exactly once.
        if proc is not None:
            proc.waitForFinished(1000)
        self._cleanup_output_dir()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and not self._settled

    def cleanup_output(self) -> None:
        """Drop the scratch directory once the consumer has read the JSON."""
        self._cleanup_output_dir()

    # ---- internal -------------------------------------------------------

    def _stop_timer(self) -> None:
        if self._timeout is not None:
            self._timeout.stop()
            self._timeout.deleteLater()
            self._timeout = None

    def _settle(self, ok: bool, detail: str) -> None:
        """Emit `finished` at most once per run.

        Qt can deliver both errorOccurred() and finished() for a process that
        dies during startup; the controller must not see two callbacks.
        """
        if self._settled:
            return
        self._settled = True
        self._stop_timer()
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.deleteLater()
        if not ok:
            self._cleanup_output_dir()
        self.finished.emit(ok, detail)

    def _cleanup_output_dir(self) -> None:
        if self._out_dir is not None:
            shutil.rmtree(self._out_dir, ignore_errors=True)
            self._out_dir = None

    def _on_timeout(self) -> None:
        if self._settled:
            return
        self._timed_out = True
        proc = self._proc
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()
        self._settle(False, "whisper-cli timed out. The recording was kept.")

    def _on_finished(self, exit_code: int, _status) -> None:
        if self._settled:
            return
        proc = self._proc
        output = ""
        if proc is not None:
            try:
                output = bytes(proc.readAll()).decode("utf-8", "replace")
            except Exception:
                output = ""
        self._proc = None

        if self._cancelled:
            self._settle(False, "Transcription cancelled. The recording was kept.")
            return
        if self._timed_out:
            self._settle(False, "whisper-cli timed out. The recording was kept.")
            return
        if exit_code != 0:
            detail = self._last_error_line(output)
            self._settle(False, f"whisper-cli failed (exit {exit_code}). {detail}")
            return

        json_path = Path(f"{self._out_path}.json")
        if not json_path.is_file():
            self._settle(False, "whisper-cli produced no JSON output. The recording was kept.")
            return
        # Kept: the output directory holds the JSON the controller reads next.
        self._settled = True
        self._stop_timer()
        if proc is not None:
            proc.deleteLater()
        self.finished.emit(True, str(json_path))

    def _on_error(self, err) -> None:
        if self._settled:
            return
        # A killed or crashed process also raises errorOccurred, so the reason
        # has to come from the run's own state, not from the process state.
        if self._cancelled:
            self._settle(False, "Transcription cancelled. The recording was kept.")
            return
        if self._timed_out:
            self._settle(False, "whisper-cli timed out. The recording was kept.")
            return
        if err == QProcess.ProcessError.FailedToStart:
            self._settle(False, "whisper-cli could not be started. Check the runtime path.")
        # Crashed/WriteError/ReadError are followed by finished() with the real
        # exit code and stderr; settling here would hide that message.

    @staticmethod
    def _last_error_line(output: str) -> str:
        for line in reversed(output.splitlines()):
            low = line.lower()
            if "error" in low or "failed" in low or "invalid" in low:
                return line.strip()[:300]
        return output.strip().splitlines()[-1][:300] if output.strip() else ""


def _offset_ms(source: dict, field: str) -> int:
    """Milliseconds for one end of a segment, from offsets or timestamps.

    whisper.cpp nests both under the segment: `offsets` are integers in
    milliseconds and `timestamps` are "HH:MM:SS,mmm" strings. A payload that
    carries only the strings still has to produce real times, so the numeric
    form is preferred and the string form is the fallback. Flat `from`/`to`
    keys are accepted too, because a hand-written or trimmed payload may use
    them instead of nesting.
    """
    from diloger.transcription.formatter import as_ms, timestamp_ms

    offsets = source.get("offsets")
    if isinstance(offsets, dict):
        value = as_ms(offsets.get(field))
        if value is not None:
            return value

    value = as_ms(source.get(field))
    if value is not None:
        return value

    timestamps = source.get("timestamps")
    if isinstance(timestamps, dict):
        value = timestamp_ms(timestamps.get(field))
        if value is not None:
            return value
    return 0


def parse_whisper_json(payload: str) -> list[dict]:
    """whisper.cpp 1.9 JSON: transcription[] with timestamps/offsets/text.

    The returned segments keep the times exactly as Whisper reported them and
    the text exactly as it was written, apart from the leading padding space.
    The caller stores these alongside the untouched raw JSON, so nothing that
    Whisper produced is lost.
    """
    data = json.loads(payload)
    entries = data.get("transcription") or data.get("segments") or []
    segments = []
    for seg in entries:
        if not isinstance(seg, dict):
            continue
        segments.append(
            {
                "start_ms": _offset_ms(seg, "from"),
                "end_ms": _offset_ms(seg, "to"),
                "text": (seg.get("text") or "").strip(),
            }
        )
    return segments
