"""Check that every rendered word is actually readable, in both themes.

A screenshot review catches this by eye, but only for the words that happen to
be on screen in the theme that happens to be active. This probe walks the real
item tree instead: for every visible text item it takes the colour Qt resolved
for that label and the colour of the nearest painted ancestor behind it, and
computes the WCAG contrast ratio. A `theme.foo` typo, a missing dark token or a
hardcoded colour then fails here instead of showing up as invisible text.

Both themes and both detail screens are checked, because the reported defect was
that the two themes did not agree.

    .venv/bin/python tools/verify_theme_render.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPalette  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from diloger.app.theme import THEME_DARK, THEME_LIGHT  # noqa: E402
from verify_playback_geometry import (  # noqa: E402
    build,
    find_items,
    prime_finished,
    spin_for,
    walk,
)

# WCAG 2.1 AA for body text. Anything dimmer is a legibility problem, not a
# style choice.
MIN_RATIO = 4.5

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def to_colour(value) -> QColor | None:
    if isinstance(value, QColor) and value.isValid():
        return value
    if isinstance(value, str) and value.startswith("#"):
        return QColor(value)
    return None


def as_rgb(item: QQuickItem, prop: str) -> QColor | None:
    return to_colour(item.property(prop))


def backdrop(item: QQuickItem) -> QColor | None:
    """The colour Qt actually paints behind an item.

    A QML `Rectangle` paints its own `color`, but a Qt Quick Control paints
    through the style and the application palette, so the nearest Rectangle
    ancestor of a button is the page behind it, not the button itself. Treating
    the page as the backdrop made every highlighted button look like near-black
    text on near-black, which it is not.
    """
    palette = QApplication.palette()
    node = item.parentItem()
    while node is not None:
        class_name = node.metaObject().className()
        if "Rectangle" in class_name:
            colour = as_rgb(node, "color")
            if colour is not None and colour.alpha() > 0:
                return colour
        elif "Button" in class_name or "CheckBox" in class_name or "ComboBox" in class_name:
            if bool(node.property("highlighted")) or bool(node.property("checked")):
                return palette.color(QPalette.ColorRole.Highlight)
            return palette.color(QPalette.ColorRole.Button)
        node = node.parentItem()
    window = item.window()
    return as_rgb(window, "color") if window is not None else None


def relative_luminance(colour: QColor) -> float:
    def channel(value: float) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.red())
        + 0.7152 * channel(colour.green())
        + 0.0722 * channel(colour.blue())
    )


def contrast(fg: QColor, bg: QColor) -> float:
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def is_text_item(item: QQuickItem) -> bool:
    value = item.property("text")
    return isinstance(value, str) and value.strip() != ""


def in_disabled_control(item: QQuickItem) -> bool:
    """WCAG 1.4.3 exempts inactive components, so a greyed-out Pause or Stop
    is not a contrast bug. Skipping them keeps the audit about text the user is
    expected to read."""
    node = item.parentItem()
    while node is not None:
        if "Button" in node.metaObject().className() or "CheckBox" in node.metaObject().className():
            return not bool(node.property("enabled"))
        node = node.parentItem()
    return False


def audit_screen(screen: QQuickItem, label: str) -> None:
    checked = 0
    skipped = 0
    before = len(failures)
    worst = ("", 99.0, "", "")
    for item in walk(screen):
        if not (item.isVisible() and is_text_item(item)):
            continue
        if item.width() <= 0 or item.height() <= 0:
            continue
        if in_disabled_control(item):
            skipped += 1
            continue
        fg = as_rgb(item, "color")
        bg = backdrop(item)
        if fg is None or bg is None:
            continue
        checked += 1
        ratio = contrast(fg, bg)
        text = str(item.property("text"))[:40]
        if ratio < worst[1]:
            worst = (text, ratio, fg.name(), bg.name())
        if ratio < MIN_RATIO:
            check(
                f"{label}: readable text {text!r}",
                False,
                f"{ratio:.2f}:1 (fg {fg.name()} on bg {bg.name()})",
            )
    # The aggregate must be judged on the failures collected here, not on
    # "some labels were seen" -- otherwise a run that just reported a wall of
    # FAILs still printed PASS for the screen it happened to.
    check(
        f"{label}: every enabled word passes WCAG AA",
        checked > 0 and len(failures) == before,
        f"{checked} labels, {skipped} disabled, worst {worst[1]:.2f}:1 on {worst[0]!r}",
    )


def audit(mode: str, screen_name: str) -> None:
    """Check one theme on one screen in its own process.

    Navigating History -> Finished inside a single engine tears the window
    down partway through, which is why the earlier two-screens-one-process run
    died on the second screen instead of reporting anything about it.
    """
    tmp = Path(tempfile.mkdtemp(prefix=f"diloger-theme-{mode}-{screen_name}-"))
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Diloger")
        app.setOrganizationName("Diloger")
        QQuickStyle.setStyle("Basic")
        font = QFont()
        font.setPointSize(15)
        app.setFont(font)

        controller, window, session_id = build(tmp)
        controller.theme.setMode(mode)
        spin_for(0.4)

        print(f"\n=== theme {mode} / {screen_name} ===")
        check(f"{mode}: the theme actually switched",
              str(controller.theme.resolvedTheme) == mode,
              str(controller.theme.resolvedTheme))

        if screen_name == "history":
            screen = find_items(window.contentItem(), "historyScreen")[0]
            controller.showHistory()
            spin_for(0.3)
            screen.setProperty("selectedId", session_id)
            spin_for(0.4)
        else:
            source = tmp / "library" / "Probe Day.txt"
            recording = tmp / "sessions" / session_id / "recording.wav"
            prime_finished(controller, source, recording, tmp / "sessions" / session_id)
            spin_for(0.4)
            screen = find_items(window.contentItem(), "finishedScreen")[0]

        audit_screen(screen, f"{mode} {screen_name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    argv = sys.argv[1:]
    if argv:
        audit(argv[0], argv[1] if len(argv) > 1 else "history")
    else:
        # A process per (theme, screen): the window does not survive a theme
        # swap or a screen change, so sharing an engine only hides screens.
        for mode in (THEME_DARK, THEME_LIGHT):
            for screen_name in ("history", "finished"):
                result = subprocess.run(
                    [sys.executable, __file__, mode, screen_name], cwd=str(ROOT)
                )
                if result.returncode != 0:
                    failures.append(f"{mode} {screen_name}")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    if not argv:
        print("Both themes render every enabled word above WCAG AA on both screens.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
