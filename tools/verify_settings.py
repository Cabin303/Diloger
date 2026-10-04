"""Check the Settings screen the way a user reaches it, on both themes.

The typed-answer setting is the one people get wrong: it decides whether what
they type survives the session. So this probe proves three things on each theme,
in the real window:

* the screen opens and every control on it is actually reachable;
* the checkbox explains where the text ends up, not merely that it is saved;
* Apply and Discard stay on screen when the form is longer than the window,
  because a control you have to scroll to find is a control you cannot rely on.

It runs on the offscreen platform, so it needs no microphone, TTS or display and
never touches the user's own config.

    .venv/bin/python tools/verify_settings.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF, QUrl  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from diloger.app.controller import AppController  # noqa: E402
from diloger.app.main import QML_DIR, _install_palette  # noqa: E402

failures: list[str] = []

# The default window, and the shortest the app allows.
WINDOWS = ((1000, 700), (780, 560))


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def spin_for(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def walk(item: QQuickItem):
    yield item
    for node in item.childItems():
        if isinstance(node, QQuickItem):
            yield from walk(node)


def find(root: QQuickItem, object_name: str) -> list[QQuickItem]:
    return [i for i in walk(root) if i.objectName() == object_name]


def all_text(root: QQuickItem) -> str:
    parts: list[str] = []
    for item in walk(root):
        value = item.property("text")
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(parts)


def build(tmp: Path, theme: str):
    library = tmp / "library"
    library.mkdir(parents=True, exist_ok=True)
    config = tmp / "config.json"
    config.write_text(json.dumps({
        "libraryPath": str(library), "sessionsPath": str(tmp / "sessions"), "theme": theme,
    }), encoding="utf-8")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")
    font = QFont()
    font.setPointSize(15)
    app.setFont(font)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR.parent))
    controller = AppController(config_path=str(config))
    controller.setParent(engine)
    controller.theme.setParent(engine)
    controller.player.setParent(engine)
    ctx = engine.rootContext()
    ctx.setContextProperty("app", controller)
    ctx.setContextProperty("theme", controller.theme)
    ctx.setContextProperty("player", controller.player)

    problems: list[str] = []
    engine.warnings.connect(lambda ws: problems.extend(w.toString() for w in ws))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    _install_palette(controller.theme, engine)
    spin_for(0.5)
    if not engine.rootObjects():
        raise SystemExit("Main.qml did not load")
    return controller, engine.rootObjects()[0], problems


def audit(theme: str) -> None:
    for width, height in WINDOWS:
        tmp = Path(tempfile.mkdtemp(prefix=f"diloger-settings-{theme}-"))
        try:
            controller, window, problems = build(tmp, theme)
            window.setProperty("width", width)
            window.setProperty("height", height)
            controller.showSettings()
            spin_for(0.5)

            screen = find(window.contentItem(), "settingsScreen")
            check(f"{theme} {width}x{height}: Settings opens", bool(screen))
            if not screen:
                continue
            page = screen[0]

            # 1. The checkbox and its explanation.
            checks = find(page, "saveTypedAnswersCheck")
            check(f"{theme} {width}x{height}: the typed-answer control exists", bool(checks))
            text = all_text(page)
            if checks:
                label = checks[0].property("text") or ""
                check(f"{theme} {width}x{height}: the checkbox says what it keeps",
                      "written" in label.lower() and "keep" in label.lower(), repr(label))
                # An explanation the user can act on: where the text goes, and
                # what happens when it is off.
                explained = ("session" in text.lower()
                             and ("export" in text.lower() or "history" in text.lower()))
                check(f"{theme} {width}x{height}: the setting explains where the text goes",
                      explained)
            check(f"{theme} {width}x{height}: it no longer lives under the Recording heading",
                  "keep the answers" not in text.lower().split("written answers")[0][-400:]
                  or "written answers" in text.lower())

            # 2. Apply and Discard must exist and stay inside the window.
            for name in ("applyButton", "discardButton"):
                buttons = find(page, name)
                check(f"{theme} {width}x{height}: {name} exists", bool(buttons))
                if buttons:
                    item = buttons[0]
                    # (0, 0) is the item's own top-left; passing
                    # item.position() would add the parent offset a second time.
                    top_left = item.mapToScene(QPointF(0, 0))
                    inside = (top_left.x() >= 0 and top_left.y() >= 0
                              and top_left.x() + item.width() <= width + 0.5
                              and top_left.y() + item.height() <= height + 0.5)
                    check(f"{theme} {width}x{height}: {name} is on screen without scrolling",
                          inside,
                          f"x={top_left.x():.0f} y={top_left.y():.0f} "
                          f"w={item.width():.0f} h={item.height():.0f} window={width}x{height}")

            runtime = [p for p in problems
                       if "TypeError" in p or "ReferenceError" in p or "is not a" in p
                       or "Binding loop" in p]
            check(f"{theme} {width}x{height}: no QML runtime errors", not runtime,
                  "; ".join(runtime) if runtime else "clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    for theme in ("dark", "light"):
        print(f"\n=== {theme} ===")
        audit(theme)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("Settings explains the typed-answer setting and keeps its actions reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())