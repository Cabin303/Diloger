"""Measure the real geometry of the playback controls in the real QML.

The unit tests can only read the QML source. This probe builds the actual
ApplicationWindow against a throwaway config and sessions folder, lets the real
layouts resolve, and then asks Qt where every control ended up on screen.

What it proves, per screen:

* Play, Pause and Stop exist as real items with a non-empty size;
* each of them is fully inside its PlaybackBar card;
* each of them is fully inside the window;
* they do not overlap each other;
* the session-action Flow below does not overlap them;
* the PlaybackBar card itself is tall enough to hold its own three rows.

It runs on the offscreen platform, so it needs no microphone, no TTS and no
display, and it never touches the user's own config or sessions.

    .venv/bin/python tools/verify_playback_geometry.py
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QObject, QPointF, QRectF, QUrl  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from diloger.app.controller import AppController  # noqa: E402
from diloger.app.main import QML_DIR, _install_palette  # noqa: E402
from diloger.audio.clock import QtClock  # noqa: E402
from diloger.content.importer import FileContentImporter  # noqa: E402
from diloger.domain.models import (  # noqa: E402
    Mode,
    Order,
    SessionConfig,
    SpokenProgression,
)
from diloger.session.engine import SessionEngine  # noqa: E402

TRANSPORT = ("Play", "Pause", "Stop")

# The caption of a control is user-facing text and can change ("Play" becomes
# "Resume" while playing), so address the buttons by their objectName first.
OBJECT_NAMES = {
    "Play": "playbackPlayButton",
    "Pause": "playbackPauseButton",
    "Stop": "playbackStopButton",
}

# Roughly the default window and the reduced size the brief asks about.
WINDOW_SIZES = ((1000, 700), (820, 600))

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def spin_for(seconds: float) -> None:
    """Let the real layouts, bindings and polish passes settle."""
    app = QCoreApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def make_tone_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> Path:
    """A known-good 16 kHz mono Int16 WAV: the canonical recording format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    step = int(rate * 0.18)
    data = bytearray()
    for n in range(int(rate * seconds)):
        value = 9000 if (n % (rate * 2)) < step else 0
        data += struct.pack("<h", value)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(data))
    return path


def scene_rect(item: QQuickItem) -> QRectF:
    """The item's rectangle in window coordinates."""
    origin = item.mapToScene(QPointF(0, 0))
    return QRectF(origin.x(), origin.y(), item.width(), item.height())


def contains(outer: QRectF, inner: QRectF, slack: float = 0.5) -> bool:
    return (
        inner.left() >= outer.left() - slack
        and inner.top() >= outer.top() - slack
        and inner.right() <= outer.right() + slack
        and inner.bottom() <= outer.bottom() + slack
    )


def overlaps(a: QRectF, b: QRectF) -> bool:
    return a.intersects(b) and not (
        a.right() <= b.left() + 0.5
        or b.right() <= a.left() + 0.5
        or a.bottom() <= b.top() + 0.5
        or b.bottom() <= a.top() + 0.5
    )


def walk(item: QQuickItem):
    yield item
    child = item.childItems()
    for node in child:
        if isinstance(node, QQuickItem):
            yield from walk(node)


def find_items(root: QQuickItem, object_name: str) -> list[QQuickItem]:
    return [i for i in walk(root) if i.objectName() == object_name]


def find_by_text(root: QQuickItem, text: str) -> list[QQuickItem]:
    hits = []
    for item in walk(root):
        value = item.property("text")
        if isinstance(value, str) and value == text and item.isVisible():
            hits.append(item)
    return hits


def action_row(root: QQuickItem) -> QQuickItem | None:
    """The Flow holding Transcribe / Export / Delete on either screen.

    Identified by what it contains rather than by its C++ class name: Qt's
    layout classes are not exposed to PySide6, so `QQuickFlow` cannot be
    imported and the runtime type string is not a contract. The smallest
    container holding those captions is the action row itself; every ancestor
    also holds them and would report a useless rectangle.
    """
    wanted = {"Transcribe now", "Transcribe", "Transcribe again", "Export Markdown"}
    best: QQuickItem | None = None
    best_area = float("inf")
    for item in walk(root):
        if not item.isVisible():
            continue
        captions = set()
        for child in walk(item):
            value = child.property("text")
            if isinstance(value, str):
                captions.add(value)
        if not (wanted & captions):
            continue
        area = item.width() * item.height()
        if area < best_area:
            best, best_area = item, area
    return best


def build(tmp: Path) -> tuple[QObject, QQuickItem, str]:
    sessions = tmp / "sessions"
    library = tmp / "library"
    library.mkdir(parents=True, exist_ok=True)
    sessions.mkdir(parents=True, exist_ok=True)

    source = library / "Probe Day.txt"
    source.write_text(
        "\n".join(
            [
                "What do you do as a second officer?",
                "How long have you been at sea?",
                "What is your daily routine on watch?",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # One stored session with a kept recording and a transcript, so History has
    # the same facts a real finished session would have.
    folder = sessions / "2026-10-03_09-00-00_probe"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "session.json").write_text(
        json.dumps(
            {
                "startedAtEpoch": 1792000000.0,
                "endedAtEpoch": 1792000060.0,
                "contentSourcePath": str(source),
                "durationSeconds": 60.0,
                "promptsReached": 3,
                "totalPrompts": 3,
                "mode": "spoken",
                "transcriptionStatus": "notRequested",
            }
        ),
        encoding="utf-8",
    )
    make_tone_wav(folder / "recording.wav")
    (folder / "transcript.md").write_text(
        "# Dialogue Trainer Session\n\n[00:00.000 - 00:02.000] What do you do "
        "as a second officer?\n",
        encoding="utf-8",
    )

    config = tmp / "config.json"
    config.write_text(
        json.dumps(
            {
                "sessionsPath": str(sessions),
                "libraryPath": str(library),
                "theme": "dark",
            }
        ),
        encoding="utf-8",
    )

    controller = AppController(config_path=str(config))
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR.parent))
    controller.setParent(engine)
    controller.theme.setParent(engine)
    controller.player.setParent(engine)

    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(str(e.toString()) for e in errors))

    ctx = engine.rootContext()
    ctx.setContextProperty("app", controller)
    ctx.setContextProperty("theme", controller.theme)
    ctx.setContextProperty("player", controller.player)

    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    _install_palette(controller.theme, engine)
    roots = engine.rootObjects()
    if not roots:
        raise SystemExit("Main.qml did not load")

    window = roots[0]
    controller.refresh_orphan_count()
    spin_for(0.4)
    return controller, window, folder.name


def prime_finished(controller, source: Path, recording: Path, session_dir: Path) -> None:
    """Put the controller into a real finished state, without any audio."""
    content = FileContentImporter().load_file(source)
    config = SessionConfig(
        mode=Mode.SPOKEN,
        spokenProgression=SpokenProgression("fixedTimer"),
        order=Order.SEQUENTIAL,
        configuredDelaySeconds=5.0,
        recordingEnabled=True,
        keepRecording=True,
        ttsVoice="Kokoro Heart",
        ttsRate=175,
        whisperModelPath="",
    )

    class SilentTts:
        def available_voices(self):
            return ["Kokoro Heart"]

        def is_voice_available(self, voice: str) -> bool:
            return True

        def speak(self, text, on_started, on_finished, on_error) -> None:
            pass

        def stop(self) -> None:
            pass

    engine = SessionEngine(
        tts=SilentTts(),
        recorder=None,
        clock=QtClock(),
        content=content,
        config=config,
    )
    session = engine.session
    session.recordingPath = str(recording)
    session.recordingRetained = True
    session.promptsReached = 3
    controller._engine = engine
    controller._session_dir = session_dir
    controller._setup["keepRecording"] = True
    controller._transcript_segments = []
    controller.viewStateChanged.emit()
    controller.refreshRequested.emit()
    controller.showFinished()


def measure(screen_name: str, screen_object: str, width: int, height: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="diloger-geometry-"))
    try:
        controller, window, session_id = build(tmp)
        window.setWidth(width)
        window.setHeight(height)

        if screen_name == "finished":
            source = tmp / "library" / "Probe Day.txt"
            recording = tmp / "sessions" / session_id / "recording.wav"
            prime_finished(controller, source, recording, tmp / "sessions" / session_id)
        else:
            controller.showHistory()

        spin_for(0.3)

        screens = find_items(window.contentItem(), screen_object)
        if not screens:
            check(f"{screen_name}: screen is present in the item tree", False)
            return
        screen = screens[0]
        check(f"{screen_name}: screen is present in the item tree", True)

        if screen_name == "history":
            # A History PlaybackBar only exists for a selected session, which is
            # what a user does by clicking a row.
            screen.setProperty("selectedId", session_id)
            spin_for(0.4)

        bars = [i for i in walk(screen) if i.objectName() == "playbackBar" and i.isVisible()]
        if not bars:
            check(f"{screen_name}: a visible PlaybackBar exists", False)
            return
        bar = bars[0]
        check(f"{screen_name}: a visible PlaybackBar exists", True)

        card = scene_rect(bar)
        window_rect = QRectF(0, 0, window.property("width"), window.property("height"))
        check(
            f"{screen_name}: PlaybackBar card is inside the window",
            contains(window_rect, card),
            f"card={_fmt(card)} window={_fmt(window_rect)}",
        )

        buttons: dict[str, QQuickItem | None] = {}
        for label in TRANSPORT:
            named = find_items(screen, OBJECT_NAMES[label])
            hit = named[0] if named else None
            if hit is None:  # pre-objectName builds: fall back to the caption
                hits = find_by_text(screen, label)
                hit = hits[0] if hits else None
            buttons[label] = hit

        for label, item in buttons.items():
            if item is None:
                check(f"{screen_name}: {label} button exists", False)
                continue
            rect = scene_rect(item)
            check(
                f"{screen_name}: {label} button exists with a real size",
                rect.width() > 8 and rect.height() > 8,
                _fmt(rect),
            )
            check(
                f"{screen_name}: {label} is fully inside the PlaybackBar",
                contains(card, rect),
                f"{label}={_fmt(rect)} card={_fmt(card)}",
            )
            check(
                f"{screen_name}: {label} is inside the window",
                contains(window_rect, rect),
                f"{label}={_fmt(rect)}",
            )

        present = [buttons[label] for label in TRANSPORT if buttons[label] is not None]
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                check(
                    f"{screen_name}: {TRANSPORT[i]} and {TRANSPORT[j]} do not overlap",
                    not overlaps(scene_rect(present[i]), scene_rect(present[j])),
                    f"{_fmt(scene_rect(present[i]))} vs {_fmt(scene_rect(present[j]))}",
                )

        flow = action_row(screen)
        check(f"{screen_name}: a session-action row exists", flow is not None)
        if flow is not None:
            flow_rect = scene_rect(flow)
            for label in TRANSPORT:
                if buttons[label] is None:
                    continue
                check(
                    f"{screen_name}: session actions do not cover {label}",
                    not overlaps(flow_rect, scene_rect(buttons[label])),
                    f"flow={_fmt(flow_rect)} {label}={_fmt(scene_rect(buttons[label]))}",
                )

        # The card has to be able to hold its own three rows, or the bottom one
        # is being drawn outside it whatever the buttons report.
        check(
            f"{screen_name}: PlaybackBar is taller than a single row",
            card.height() >= 100,
            f"height={card.height():.0f}",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt(rect: QRectF) -> str:
    return (
        f"x={rect.x():.0f} y={rect.y():.0f} "
        f"w={rect.width():.0f} h={rect.height():.0f} bottom={rect.bottom():.0f}"
    )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")
    font = QFont()
    font.setPointSize(15)
    app.setFont(font)

    for width, height in WINDOW_SIZES:
        print(f"\n=== window {width}x{height} ===")
        for screen, object_name in (("history", "historyScreen"), ("finished", "finishedScreen")):
            print(f"\n--- {screen} @ {width}x{height} ---")
            measure(screen, object_name, width, height)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("Playback geometry verified on both screens at both window sizes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())