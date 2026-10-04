"""Microphone recording via Qt Multimedia. Independent from TTS and Whisper.

The backend cannot be trusted to honour a requested sample rate: on the macOS
default input, requesting 16 kHz delivered ~21.9 kHz and requesting 44.1 kHz
delivered ~50 kHz, while only 48 kHz came back exact (docs §20). Writing such
a stream under a header claiming the requested rate produced the crackled,
wrongly pitched recordings this app used to save.

So the recorder never claims a rate it has not seen:

* it captures the delivered bytes as they arrive, into a raw `.pcm` file;
* it measures the delivered rate from that stream;
* at `stop()` it builds the WAV header from the measured rate, so the file
  describes the audio it actually holds;
* it then converts the recording into PCM / mono / 16-bit / 16 kHz with a real
  resampler, which is the format Whisper wants.

A conversion failure keeps the honest native-rate WAV and reports the problem:
a recording is never deleted or mislabelled because of it.
"""

from __future__ import annotations

import os
import statistics
import time
import wave
from pathlib import Path
from typing import IO, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from diloger.audio import wavconv
from diloger.storage.session_store import temp_audio_path

# The rate the macOS default input honours exactly. It is a preference, not a
# promise: the measured rate always wins in the header.
CAPTURE_SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2

# Rates a device may legitimately prefer and this recorder can record.
KNOWN_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000, 96000)

# How far the measured rate may sit from the requested one before the mismatch
# is reported instead of silently absorbed.
RATE_TOLERANCE = 0.02

# Streamed in blocks so a long session never has to fit in memory.
COPY_BLOCK = 1 << 20

# Rate estimation: how many per-chunk samples to keep, how many are needed, and
# the chunk gaps that count as steady delivery.
RATE_SAMPLES = 48
MIN_RATE_SAMPLES = 8
MIN_CHUNK_GAP = 0.001
MAX_CHUNK_GAP = 1.0


class RecordingError(Exception):
    pass


class QtRecorder(QObject):
    """Captures the default input and saves a canonical WAV.

    `stop()` builds the WAV from the captured stream and converts it. If the
    session is interrupted before `stop()`, the raw `.pcm` file is left behind
    as a recoverable orphan that Settings can delete.
    """

    error = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._source: Optional[QAudioSource] = None
        self._device_io = None
        self._raw: Optional[IO[bytes]] = None
        self._raw_path: Optional[Path] = None
        self._path: Optional[Path] = None
        self._frames = 0
        self._active = False
        self._session_id: str = ""
        self._temp_path: Optional[Path] = None
        self._temp_session: Optional[str] = None

        # Format bookkeeping for the diagnostic report.
        self._requested_rate = 0
        self._rates: list[float] = []
        self._last_chunk_at: Optional[float] = None
        self._written: Optional[wavconv.WavFormat] = None

    # ---- availability ---------------------------------------------------

    def is_available(self) -> bool:
        return bool(QMediaDevices.audioInputs())

    def list_devices(self) -> list[str]:
        return [d.description() for d in QMediaDevices.audioInputs()]

    def default_device_name(self) -> str:
        dev = QMediaDevices.defaultAudioInput()
        return dev.description() if dev is not None else ""

    def capture_plan(self) -> dict:
        """What a recording would use, without opening the microphone.

        For `--check`: the device's own preferred format, the rate that is
        actually requested, and the canonical format the recording is converted
        to. The rate the device really delivers is only knowable while
        recording, and is reported by `format_report()` afterwards.
        """
        device = QMediaDevices.defaultAudioInput()
        rate = CAPTURE_SAMPLE_RATE
        preferred = ""
        if device is not None:
            rate = self._preferred_capture_rate(device)
            fmt = device.preferredFormat()
            preferred = wavconv.describe_format(
                int(fmt.sampleRate()),
                int(fmt.channelCount()),
                4 if fmt.sampleFormat() == QAudioFormat.SampleFormat.Float else 2,
            )
        return {
            "device": device.description() if device is not None else "",
            "devicePreferred": preferred,
            "requested": wavconv.describe_format(rate, CHANNELS, SAMPLE_WIDTH),
            "canonical": wavconv.describe_format(
                wavconv.CANONICAL_SAMPLE_RATE, wavconv.CANONICAL_CHANNELS,
                wavconv.CANONICAL_SAMPLE_WIDTH,
            ),
        }

    # ---- recording ------------------------------------------------------

    def start(self) -> None:
        if self._active:
            return
        device = QMediaDevices.defaultAudioInput()
        if device is None:
            raise RecordingError("No microphone input device available.")

        rate = self._preferred_capture_rate(device)
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        self._path = Path(self.temp_target())
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._path.unlink()
        self._raw_path = self._path.with_suffix(".pcm")
        if self._raw_path.exists():
            self._raw_path.unlink()

        self._requested_rate = rate
        self._rates = []
        self._last_chunk_at = None
        self._written = None
        self._frames = 0

        self._source = QAudioSource(device, fmt)
        self._device_io = self._source.start()
        if self._source.isNull():
            self._source.deleteLater()
            self._source = None
            self._device_io = None
            raise RecordingError("Could not open the microphone. Check input permissions.")

        self._raw = open(self._raw_path, "wb")
        self._device_io.readyRead.connect(self._on_data)
        self._active = True

    @staticmethod
    def _preferred_capture_rate(device) -> int:
        """Rate to ask for.

        The device's own preferred rate when it is one this recorder knows,
        otherwise the rate that is known to come back exact.
        """
        try:
            preferred = int(device.preferredFormat().sampleRate())
        except (AttributeError, RuntimeError):
            preferred = 0
        if preferred in KNOWN_RATES:
            return preferred
        return CAPTURE_SAMPLE_RATE

    def temp_target(self) -> str:
        """Per-session temporary path.

        Cached per session id: two sessions must never share a temp file,
        otherwise a later start would truncate a recording that has not been
        transcribed yet, and the cleanup step would delete it.
        """
        if self._temp_path is None or self._temp_session != self._session_id:
            self._temp_session = self._session_id
            self._temp_path = temp_audio_path(self._session_id)
        return str(self._temp_path)

    def prepare_for_session(self, session_id: str) -> None:
        self._session_id = session_id
        self._temp_path = None
        self._temp_session = None

    # ---- stream ---------------------------------------------------------

    def _on_data(self) -> None:
        if self._source is None or self._device_io is None or self._raw is None:
            return
        available = self._source.bytesAvailable()
        if available <= 0:
            return
        buf = self._device_io.read(available)
        if not buf:
            return
        data = bytes(buf)
        self._raw.write(data)
        self._frames += len(data) // SAMPLE_WIDTH
        self._collect_rate(len(data) // SAMPLE_WIDTH, time.monotonic())

    def _collect_rate(self, samples: int, now: float) -> None:
        """Keep a per-chunk rate estimate for every chunk that arrives steadily.

        A single chunk divided by the gap since the previous one is noisy, and a
        burst of queued chunks delivered back to back would make the gap almost
        zero, so only evenly spaced chunks are allowed into the sample set.
        """
        if self._last_chunk_at is not None:
            gap = now - self._last_chunk_at
            if MIN_CHUNK_GAP <= gap <= MAX_CHUNK_GAP:
                self._rates.append(samples / gap)
                del self._rates[:-RATE_SAMPLES]
        self._last_chunk_at = now

    def _measure(self) -> Optional[float]:
        """The delivered sample rate, or None while there is not enough audio.

        The median of the per-chunk estimates is used rather than one long
        window: it stays correct when the event loop is busy and the queued
        chunks are delivered in bursts.
        """
        if self._frames * SAMPLE_WIDTH < wavconv.MEASURE_MIN_BYTES:
            return None
        if len(self._rates) < MIN_RATE_SAMPLES:
            return None
        rate = statistics.median(self._rates)
        return rate if wavconv.plausible_rate(rate) else None

    def _effective_rate(self) -> int:
        """Rate to write into the header.

        The measured rate when the stream proved the request wrong, otherwise
        the requested one: with too little audio there is nothing to disprove.
        """
        measured = self._measure()
        if measured is None:
            return self._requested_rate
        return int(round(measured))

    # ---- finishing ------------------------------------------------------

    def stop(self) -> Optional[Path]:
        if not self._active:
            return self._path if self._path and self._path.exists() else None
        self._active = False
        if self._source is not None:
            self._source.stop()
            self._source.deleteLater()
        self._source = None
        self._device_io = None

        if self._raw is not None:
            try:
                self._raw.close()
            except OSError:
                pass
            self._raw = None

        if self._frames == 0:
            self.error.emit("Recording captured no audio.")
            return None
        if self._path is None or self._raw_path is None:
            return None
        if not self._raw_path.is_file():
            self.error.emit("The captured audio stream went missing.")
            return None

        self._build_wav()
        if not self._path.is_file():
            return None
        self._discard_raw()
        self._finalise_format()
        return self._path

    def _build_wav(self) -> None:
        """Wrap the captured stream in a WAV header matching reality."""
        assert self._path is not None and self._raw_path is not None
        rate = self._effective_rate()
        try:
            with wave.open(str(self._path), "wb") as w:
                w.setnchannels(CHANNELS)
                w.setsampwidth(SAMPLE_WIDTH)
                w.setframerate(rate)
                with open(self._raw_path, "rb") as raw:
                    while True:
                        block = raw.read(COPY_BLOCK)
                        if not block:
                            break
                        w.writeframesraw(block)
        except (OSError, wave.Error) as exc:
            self.error.emit(f"The recording could not be saved: {exc}")
            self._delete(self._path)

    def _discard_raw(self) -> None:
        """The intermediate stream is no longer needed once the WAV exists."""
        if self._raw_path is not None:
            self._delete(self._raw_path)

    def _finalise_format(self) -> None:
        """Record the written format, then convert to the canonical one.

        A conversion failure keeps the honest native-rate WAV and reports the
        problem: the recording is the user's data and must survive it.
        """
        assert self._path is not None
        try:
            written = wavconv.read_wav_format(self._path)
        except wavconv.ConversionError as exc:
            self.error.emit(f"Recording header could not be read: {exc}")
            return
        self._written = written

        if self._requested_rate and not self._within_tolerance(written.sample_rate):
            self.error.emit(
                f"The microphone delivered {written.describe()} although "
                f"{self._requested_rate} Hz was requested. The recording was "
                "labelled with the rate that was actually received."
            )

        staged = self._path.with_suffix(".canonical.wav")
        try:
            canonical = wavconv.convert_to_canonical(self._path, staged)
        except wavconv.ConversionError as exc:
            self._delete(staged)
            self.error.emit(str(exc))
            return
        os.replace(staged, self._path)
        self._written = canonical

    def _within_tolerance(self, rate: int) -> bool:
        """True when a delivered rate still matches what was asked for."""
        if not self._requested_rate:
            return True
        return (
            abs(rate - self._requested_rate) / self._requested_rate
            <= RATE_TOLERANCE
        )

    def abort(self) -> Optional[Path]:
        return self.stop()

    def duration_seconds(self) -> float:
        """How much audio has been captured so far.

        Read from the written WAV when there is one. Before that, the rate comes
        from `_measure()`, the same measurement the header is built from: there
        is no separate `_measured_rate` field to read, and inventing one raised
        AttributeError on exactly the short or interrupted recordings that need
        this the most.
        """
        if self._written is not None:
            return self._written.duration
        measured = self._measure()
        rate = (
            int(round(measured))
            if measured is not None and wavconv.plausible_rate(measured)
            else (self._requested_rate or CAPTURE_SAMPLE_RATE)
        )
        if rate <= 0:
            return 0.0
        return self._frames / rate

    # ---- diagnostics ----------------------------------------------------

    def format_report(self) -> dict:
        """requested / actual / written formats, for `--check` and reports."""
        measured = self._measure()
        actual = (
            wavconv.describe_format(int(round(measured)), CHANNELS, SAMPLE_WIDTH)
            if wavconv.plausible_rate(measured)
            else ""
        )
        return {
            "requested": (
                wavconv.describe_format(self._requested_rate, CHANNELS, SAMPLE_WIDTH)
                if self._requested_rate
                else ""
            ),
            "actual": actual,
            "written": self._written.describe() if self._written else "",
            "mismatch": bool(actual and not self._within_tolerance(int(round(measured)))),
        }

    # ---- disk / interruption handling -----------------------------------

    @staticmethod
    def _delete(path: Optional[Path]) -> None:
        if path is None:
            return
        try:
            os.unlink(path)
        except OSError:
            pass