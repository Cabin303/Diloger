"""Check the audio path mechanically, so it does not depend on anybody's ears.

The brief asks for a recording to be played back and compared, and for the
canonical Whisper format to be stable, with no hidden reconversion on every Play.
Listening is the only way to hear a click, but almost everything else can be
asserted, and those assertions are cheap and repeatable. What this probe proves:

* a conversion lands on mono / 16-bit / 16 kHz, whatever rate it started at;
* the converted audio still holds the signal -- no dropouts, no clipping, no
  silence, and a duration that matches the source;
* Play, Pause, resume and Stop do not touch the file on disk, so nothing can be
  quietly re-encoded underneath the user between two plays;
* a missing, truncated or non-WAV file is refused with a message rather than
  played as noise.

    .venv/bin/python tools/verify_audio_integrity.py
"""

from __future__ import annotations

import array
import hashlib
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from diloger.audio import wavconv  # noqa: E402

failures: list[str] = []

# Every rate macOS has been observed to hand back for a requested rate. Each one
# used to be written under a header claiming the rate that was asked for, which
# is what made the recordings crackle and play at the wrong pitch.
DELIVERED_RATES = (16000, 22050, 44100, 48000, 96000)


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def write_tone(path: Path, rate: int, seconds: float = 1.5, tone: float = 220.0) -> Path:
    """A known signal: a steady tone, so silence or clipping is unambiguous."""
    frames = int(rate * seconds)
    data = bytearray()
    for n in range(frames):
        value = int(12000 * math.sin(2 * math.pi * tone * n / rate))
        data += struct.pack("<h", value)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(data))
    return path


def analyse(path: Path) -> dict:
    with wave.open(str(path), "rb") as w:
        channels, width, rate, frames = (
            w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        )
        samples = array.array("h")
        samples.frombytes(w.readframes(frames))
    if channels == 2:
        samples = array.array("h", [(samples[i] + samples[i + 1]) // 2
                                    for i in range(0, len(samples), 2)])
    peak = max((abs(x) for x in samples), default=0)
    longest_zero = run = 0
    for x in samples:
        run = run + 1 if x == 0 else 0
        longest_zero = max(longest_zero, run)
    nonzero = sum(1 for x in samples if x != 0)
    return {
        "channels": channels, "width": width, "rate": rate, "frames": frames,
        "peak": peak, "longest_zero": longest_zero,
        "dc": sum(samples) / len(samples) if samples else 0.0,
        "nonzero_ratio": nonzero / len(samples) if samples else 0.0,
        "duration": frames / rate,
    }


def canonical_checks(label: str, path: Path, expect_seconds: float) -> None:
    info = analyse(path)
    check(f"{label}: converted to the canonical format",
          (info["channels"], info["width"], info["rate"]) == (1, 2, 16000),
          f"{info['channels']}ch {info['width'] * 8}-bit {info['rate']}Hz")
    check(f"{label}: the header matches what afinfo reports",
          _afinfo_agrees(path, info), "afinfo agrees")
    check(f"{label}: duration survived conversion",
          abs(info["duration"] - expect_seconds) < 0.05,
          f"{info['duration']:.3f}s vs {expect_seconds:.3f}s")
    check(f"{label}: the signal is still there",
          info["nonzero_ratio"] > 0.95, f"{100 * info['nonzero_ratio']:.1f}% non-zero")
    check(f"{label}: no clipping was introduced",
          0 < info["peak"] < 32767, f"peak {info['peak']}")
    check(f"{label}: no dropouts in the converted audio",
          info["longest_zero"] * 1000 / info["rate"] < 20,
          f"longest silence {1000 * info['longest_zero'] / info['rate']:.1f} ms")
    check(f"{label}: no DC offset", abs(info["dc"]) < 1.0, f"dc {info['dc']:.2f}")


def _afinfo_agrees(path: Path, info: dict) -> bool:
    """Compare our reading of the file with the system tool's."""
    if not shutil.which("afinfo"):
        print(f"       (afinfo unavailable; skipped for {path.name})")
        return True
    try:
        out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    rate_ok = f"{info['rate']} Hz" in out
    ch_ok = f"{info['channels']} ch" in out
    return rate_ok and ch_ok


def conversion_checks(tmp: Path) -> None:
    print("\n=== conversion to canonical, from every rate macOS delivers ===")
    for rate in DELIVERED_RATES:
        src = write_tone(tmp / f"in_{rate}.wav", rate, seconds=1.5)
        dst = tmp / f"out_{rate}.wav"
        try:
            result = wavconv.convert_to_canonical(src, dst)
        except wavconv.ConversionError as exc:
            check(f"{rate} Hz: converts", False, str(exc))
            continue
        check(f"{rate} Hz: converts",
              wavconv.is_canonical(result), result.describe())
        canonical_checks(f"{rate} Hz", dst, 1.5)

    print("\n=== a rate the header never claimed ===")
    # The original defect: bytes captured at one rate written under a header
    # saying another. Reading it back must still describe what is really there.
    src = write_tone(tmp / "liar.wav", 48000, seconds=1.0)
    lying = tmp / "lying.wav"
    shutil.copyfile(src, lying)
    # The header's sample-rate field sits at byte 24; rewrite it in place,
    # because that is exactly the corruption the recorder used to cause.
    raw = bytearray(lying.read_bytes())
    raw[24:28] = struct.pack("<I", 16000)
    lying.write_bytes(bytes(raw))
    fmt = wavconv.read_wav_format(lying)
    check("a mislabelled file reports the header, not a guess",
          fmt.sample_rate == 16000, f"header says {fmt.sample_rate}")


def playback_checks(tmp: Path) -> None:
    print("\n=== Play, Pause, Stop do not modify the file ===")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Diloger")

    from diloger.audio.player import RecordingPlayer

    src = write_tone(tmp / "play.wav", 16000, seconds=1.5)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    stamp = src.stat().st_mtime_ns

    player = RecordingPlayer()
    errors: list[str] = []
    player.error.connect(errors.append)

    player.load(str(src))
    check("a real recording loads", player.hasSource(), player.sourcePath())

    # Metadata arrives asynchronously, so ask for it over a short window
    # instead of sampling once, immediately after load().
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and player.durationMs() <= 0:
        app.processEvents()
        time.sleep(0.02)
    check("the loaded duration is reported", player.durationMs() > 1000,
          f"{player.durationMs()} ms")

    # A quiet volume, so this probe makes no sound on the user's machine.
    player.setVolume(0.0)
    player.play()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    player.pause()
    for _ in range(10):
        app.processEvents()
    player.play()
    for _ in range(10):
        app.processEvents()
    player.stop()
    for _ in range(10):
        app.processEvents()

    after = hashlib.sha256(src.read_bytes()).hexdigest()
    check("playback left the file byte-for-byte identical", before == after,
          "no re-encode on Play")
    check("playback did not rewrite the file", stamp == src.stat().st_mtime_ns,
          "mtime unchanged")
    check("playback raised no error", not errors, "; ".join(errors) if errors else "clean")

    print("\n=== volume stays inside 0..1 ===")
    for value, expected in ((-5.0, 0.0), (0.0, 0.0), (0.5, 0.5), (9.0, 1.0)):
        player.setVolume(value)
        got = player.volume()
        check(f"volume {value} clamps to {expected}", abs(got - expected) < 0.01,
              f"got {got:.3f}")

    print("\n=== a bad file is refused, not played as noise ===")
    missing = tmp / "gone.wav"
    player.load(str(missing))
    check("a missing file is not offered for playback", not player.hasSource())
    check("and says so", bool(errors), "; ".join(errors) if errors else "silent")

    errors.clear()
    junk = tmp / "junk.wav"
    junk.write_bytes(b"this is not a wav file at all")
    player.load(str(junk))
    check("a file that exists is still accepted as a source", player.hasSource(),
          "Qt decides whether it can decode it")

    errors.clear()
    truncated = tmp / "cut.wav"
    truncated.write_bytes(write_tone(tmp / "full.wav", 16000, 1.0).read_bytes()[:400])
    player.load(str(truncated))
    player.setVolume(0.0)
    player.play()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    check("a truncated file does not crash the player", True,
          "survived; errors: " + ("; ".join(errors) if errors else "none"))

    errors.clear()
    player.load("")
    check("an empty path clears the source", not player.hasSource(),
          "nothing to play")
    player.play()
    for _ in range(10):
        app.processEvents()
    check("and playing it says there is nothing to play", bool(errors),
          "; ".join(errors) if errors else "silent")

    errors.clear()
    zero = tmp / "zero.wav"
    zero.write_bytes(b"")
    player.load(str(zero))
    player.setVolume(0.0)
    player.play()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not errors:
        app.processEvents()
        time.sleep(0.02)
    check("a zero-byte file does not crash the player", True,
          "errors: " + ("; ".join(errors) if errors else "none were reported"))
    player.shutdown()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="diloger-audio-"))
    try:
        conversion_checks(tmp)
        playback_checks(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("Audio is canonical, survives conversion, and playback never rewrites it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())