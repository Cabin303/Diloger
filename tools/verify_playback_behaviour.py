"""Drive the real PlaybackBar buttons and check what the user actually gets.

The geometry probe proves the transport is reachable; the backend probe proves
RecordingPlayer plays, pauses and seeks. Neither proves the two are wired
together: a Stop that leaves the playhead where it was, or a Play after Pause
that restarts instead of continuing, pass both of them and still feel broken.

So this probe clicks the real QML buttons of the real window and reads the
status sentence the user sees:

* Play  -> status Playing, and the position advances;
* Pause -> status Paused, and the position freezes where it was;
* Play  -> the position continues from there, it is not reset to zero;
* Stop  -> status Stopped, and the position is back at the start.

It runs on the offscreen platform, so it needs no display, no microphone and no
TTS, and it never touches the user's own config or sessions.

    .venv/bin/python tools/verify_playback_behaviour.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from verify_playback_geometry import build, find_items, make_tone_wav, spin_for, walk  # noqa: E402

PLAY = "playbackPlayButton"
PAUSE = "playbackPauseButton"
STOP = "playbackStopButton"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def button(screen: QQuickItem, object_name: str) -> QQuickItem | None:
    hits = find_items(screen, object_name)
    return hits[0] if hits else None


def click(item: QQuickItem) -> None:
    """Press a control the way a user does, through its own signal."""
    item.metaObject().invokeMethod(item, "click")


def is_enabled(item: QQuickItem) -> bool:
    return bool(item.property("enabled"))


def status_of(bar: QQuickItem) -> str:
    """The sentence the user reads, not an internal property."""
    for item in walk(bar):
        value = item.property("text")
        if isinstance(value, str) and value in {
            "Playing",
            "Paused",
            "Stopped",
            "No recording for this session",
        }:
            return value
    return "<no status text found>"


def position_ms(controller) -> int:
    return controller.player.positionMs()


def wait_for(app, predicate, timeout=6.0, label="condition") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    print(f"       timeout waiting for {label}")
    return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")
    font = QFont()
    font.setPointSize(15)
    app.setFont(font)

    tmp = Path(tempfile.mkdtemp(prefix="diloger-behaviour-"))
    try:
        controller, window, session_id = build(tmp)
        controller.showHistory()
        spin_for(0.3)
        screens = find_items(window.contentItem(), "historyScreen")
        screen = screens[0]
        screen.setProperty("selectedId", session_id)
        spin_for(0.4)

        bars = [i for i in walk(screen) if i.objectName() == "playbackBar" and i.isVisible()]
        if not bars:
            check("a visible PlaybackBar exists", False)
            return 1
        bar = bars[0]

        play, pause, stop = (button(screen, n) for n in (PLAY, PAUSE, STOP))
        for name, item in ((PLAY, play), (PAUSE, pause), (STOP, stop)):
            check(f"{name} exists", item is not None)
        if not (play and pause and stop):
            return 1

        check("the card offers a recording", status_of(bar) != "No recording for this session",
              status_of(bar))

        # --- Play ---------------------------------------------------------
        check("Play is enabled while stopped", is_enabled(play))
        click(play)
        check("Play reports Playing", wait_for(app, lambda: status_of(bar) == "Playing",
                                               label="Playing"), status_of(bar))
        check("Pause becomes enabled while playing", is_enabled(pause))
        check("Stop becomes enabled while playing", is_enabled(stop))
        check(
            "the position advances during playback",
            wait_for(app, lambda: position_ms(controller) > 250, label="position > 250ms"),
            f"{position_ms(controller)} ms",
        )
        playing_at = position_ms(controller)

        # --- Pause --------------------------------------------------------
        click(pause)
        check("Pause reports Paused",
              wait_for(app, lambda: status_of(bar) == "Paused", label="Paused"),
              status_of(bar))
        paused_at = position_ms(controller)
        spin_for(0.5)
        check("the position is frozen while paused",
              abs(position_ms(controller) - paused_at) <= 40,
              f"{paused_at} -> {position_ms(controller)} ms")
        check("the paused position is not the start", paused_at > 0, f"{paused_at} ms")
        check("Play is enabled again after Pause", is_enabled(play))

        # --- Play after Pause --------------------------------------------
        click(play)
        check("Play after Pause reports Playing",
              wait_for(app, lambda: status_of(bar) == "Playing", label="Playing again"),
              status_of(bar))
        check(
            "Play after Pause continues instead of restarting",
            wait_for(app, lambda: position_ms(controller) > paused_at + 100,
                     label="position past the pause point"),
            f"{paused_at} -> {position_ms(controller)} ms (was playing at {playing_at} ms)",
        )

        # --- Stop ---------------------------------------------------------
        click(stop)
        check("Stop reports Stopped",
              wait_for(app, lambda: status_of(bar) == "Stopped", label="Stopped"),
              status_of(bar))
        check("Stop returns the position to the start",
              wait_for(app, lambda: position_ms(controller) == 0, label="position 0"),
              f"{position_ms(controller)} ms")

        # --- a session with no recording says so --------------------------
        empty = tmp / "sessions" / "2026-10-02_09-00-00_silent"
        empty.mkdir(parents=True, exist_ok=True)
        (empty / "session.json").write_text(
            '{"startedAtEpoch": 1791000000.0, "endedAtEpoch": 1791000060.0,'
            ' "contentSourcePath": "Probe Day.txt", "durationSeconds": 60.0,'
            ' "promptsReached": 1, "totalPrompts": 3, "mode": "spoken",'
            ' "transcriptionStatus": "notRequested"}',
            encoding="utf-8",
        )
        screen.metaObject().invokeMethod(screen, "refresh")
        spin_for(0.3)
        screen.setProperty("selectedId", empty.name)
        spin_for(0.5)
        silent = [i for i in walk(screen) if i.objectName() == "playbackBar" and i.isVisible()]
        check("a session without a recording still shows the card", bool(silent))
        if silent:
            check("a session without a recording says so",
                  status_of(silent[0]) == "No recording for this session",
                  status_of(silent[0]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("Playback behaviour verified: Play, Pause, resume and Stop rewind.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
