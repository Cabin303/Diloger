"""WAV header inspection and conversion into the canonical recording format.

Pure Python plus `/usr/bin/afconvert`; no Qt, so it can be tested on its own.

Why this module exists
----------------------
A microphone backend may hand over a stream whose real sample rate differs from
the one that was requested (measured on the macOS default input: asking for
16 kHz returned ~21.9 kHz, asking for 44.1 kHz returned ~50 kHz, only 48 kHz
came back exact). Writing such a stream under a header that claims 16 kHz
produces a structurally wrong WAV: the player resamples it, the speech sounds
crackled and pitched, and Whisper is fed a broken stream.

The fix has two halves and both are here:

* the recorder measures what the device actually delivered, so the WAV header
  can be written to match reality (`recorder.QtRecorder._collect_rate`);
* `convert_to_canonical` turns an honest native-rate WAV into PCM / mono /
  16-bit / 16 kHz with a real resampler, which is the format Whisper wants.

Relabelling a header is never a conversion: `convert_to_canonical` always goes
through `afconvert`, which resamples.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

CANONICAL_SAMPLE_RATE = 16000
CANONICAL_CHANNELS = 1
CANONICAL_SAMPLE_WIDTH = 2

# Plausible bounds for a speech recording. Anything outside them means the
# measurement is wrong, not that the device is exotic.
MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 192000

# The rate has to be measured over enough audio for the estimate to be stable.
# At the observed ~10 ms chunk cadence this is a few dozen chunks.
MEASURE_MIN_BYTES = 16384


class ConversionError(Exception):
    """Raised when a recording could not be converted or verified."""


@dataclass(frozen=True)
class WavFormat:
    channels: int
    sample_width: int
    sample_rate: int
    frames: int

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def describe(self) -> str:
        return (
            f"{self.sample_rate} Hz / {self.channels} ch / "
            f"{self.sample_width * 8}-bit PCM"
        )


def describe_format(rate: int, channels: int, sample_width: int) -> str:
    return f"{rate} Hz / {channels} ch / {sample_width * 8}-bit PCM"


def is_canonical(fmt: WavFormat) -> bool:
    return (
        fmt.sample_rate == CANONICAL_SAMPLE_RATE
        and fmt.channels == CANONICAL_CHANNELS
        and fmt.sample_width == CANONICAL_SAMPLE_WIDTH
    )


def read_wav_format(path: Path | str) -> WavFormat:
    """Header of an existing WAV file."""
    target = Path(path)
    try:
        with wave.open(str(target), "rb") as w:
            return WavFormat(
                channels=w.getnchannels(),
                sample_width=w.getsampwidth(),
                sample_rate=w.getframerate(),
                frames=w.getnframes(),
            )
    except (OSError, wave.Error) as exc:
        raise ConversionError(f"{target.name} is not a readable WAV file: {exc}") from exc


def plausible_rate(rate: float | None) -> bool:
    """True when a measured rate can be trusted as a real stream rate."""
    return (
        rate is not None
        and MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE
    )


def afconvert_path() -> str:
    """Locate the macOS converter, or "" when it is unavailable."""
    return shutil.which("afconvert") or ""


def convert_to_canonical(src: Path | str, dst: Path | str) -> WavFormat:
    """Resample a WAV into PCM / mono / 16-bit / 16 kHz at `dst`.

    Uses `/usr/bin/afconvert`, which performs a real sample-rate conversion.
    The result is verified against its own header before it is accepted, and
    the destination is only trusted once it parses and is not empty.
    """
    source = Path(src)
    target = Path(dst)
    if not source.is_file():
        raise ConversionError(f"No audio to convert: {source}")

    fmt = read_wav_format(source)
    if is_canonical(fmt):
        # Already canonical: copy verbatim so no second resample is applied.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return fmt

    tool = afconvert_path()
    if not tool:
        raise ConversionError(
            "/usr/bin/afconvert is not available, so the recording cannot be "
            f"converted to {CANONICAL_SAMPLE_RATE} Hz. The unconverted "
            "recording was kept."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    command = [
        tool,
        "-f", "WAVE",
        "-d", f"LEI16@{CANONICAL_SAMPLE_RATE}",
        "-c", str(CANONICAL_CHANNELS),
        str(source),
        str(target),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConversionError(f"Audio conversion could not be started: {exc}") from exc

    if result.returncode != 0 or not target.is_file():
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else f"exit code {result.returncode}"
        target.unlink(missing_ok=True)
        raise ConversionError(f"Audio conversion failed: {reason}")

    converted = read_wav_format(target)
    if converted.frames == 0 or converted.sample_width == 0:
        target.unlink(missing_ok=True)
        raise ConversionError("Audio conversion produced an empty file.")
    if not is_canonical(converted):
        target.unlink(missing_ok=True)
        raise ConversionError(
            "Audio conversion produced "
            f"{converted.describe()} instead of the expected canonical format."
        )
    return converted


def wav_duration(path: Path | str) -> float:
    """Duration in seconds, or 0.0 when the file cannot be read."""
    try:
        return read_wav_format(path).duration
    except ConversionError:
        return 0.0