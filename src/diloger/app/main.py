"""Diloger entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

QML_DIR = Path(__file__).resolve().parent.parent / "qml"

USAGE = """Diloger — local spoken and written dialogue trainer.

Usage:
  diloger            start the application
  diloger --help     show this message
  diloger --check    verify QML, audio input, TTS and Whisper, then exit

Start the app with Diloger.command; it sets up the project environment and
keeps diagnostics visible.
"""


def _build_engine() -> tuple[QQmlApplicationEngine, AppController, list[str]]:
    """Create the QML engine with `app` and `theme` bound to its root context.

    Both objects are parented to the engine so their lifetime follows the engine
    rather than this function's local variables. Without a parent, QML bindings
    can still hold a stale pointer after Python releases the last reference.
    """
    from diloger.app.controller import AppController

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR.parent))

    controller = AppController()
    # The controller owns the theme because the theme is a setting; QML and the
    # Qt Basic palette both read from that one object.
    theme = controller.theme
    controller.setParent(engine)
    theme.setParent(engine)
    controller.player.setParent(engine)

    warnings: list[str] = []
    engine.warnings.connect(
        lambda errors: warnings.extend(str(e.toString()) for e in errors)
    )

    ctx = engine.rootContext()
    ctx.setContextProperty("app", controller)
    ctx.setContextProperty("theme", theme)
    ctx.setContextProperty("player", controller.player)
    return engine, controller, warnings


def _install_palette(theme, engine: QQmlApplicationEngine) -> None:
    """Push the theme's palette onto the app and onto the QML window.

    A QQuickWindow keeps its own palette, so the application palette alone would
    leave the window on the macOS system colours. Re-pushed on every theme change
    so a runtime switch repaints the controls too, not only the QML bindings.
    """

    def repaint() -> None:
        theme.apply()
        for window in engine.rootObjects():
            theme.applyToWindow(window)

    repaint()
    theme.paletteChanged.connect(repaint)


def _run_checks() -> int:
    """Report the parts of the runtime a user can fix without a debugger."""
    problems: list[str] = []

    # QApplication, not QGuiApplication: `--check` touches the same folder
    # dialogs as the app, and a QWidget cannot be built without it.
    app = QApplication(sys.argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")

    qml = QML_DIR / "Main.qml"
    if not qml.is_file():
        problems.append(f"QML entry point is missing: {qml}")
        print(f"QML entry point is missing: {qml}", file=sys.stderr)
        return 1

    engine, controller, warnings = _build_engine()
    controller.refresh_orphan_count()
    engine.load(QUrl.fromLocalFile(str(qml)))
    _install_palette(controller.theme, engine)

    if not engine.rootObjects():
        problems.append(f"QML failed to load: {qml}")
    elif warnings:
        problems.append("QML warnings:\n    " + "\n    ".join(sorted(set(warnings))))

    if controller._tts.is_available():
        voices = [v for v in controller.voices() if "Kokoro" in v]
        print(f"TTS: available (Kokoro voices: {', '.join(voices) or 'none'})")
    else:
        problems.append("TTS is unavailable: /usr/bin/say did not respond.")

    recorder = controller._recorder
    if recorder.is_available():
        print(f"Default audio input: {recorder.default_device_name()}")
        plan = recorder.capture_plan()
        print(f"  device prefers:  {plan['devicePreferred'] or 'unknown'}")
        print(f"  recorded as:     {plan['requested']}")
        print(f"  converted to:    {plan['canonical']}")
    else:
        problems.append(
            "No audio input device is available. Training still works; recording is disabled."
        )

    print(f"Whisper: {controller._whisper_status()}")
    print(f"Library folder: {controller._config.libraryPath or 'not set'}")
    print(f"Sessions folder: {controller._storage.sessions_dir}")
    print(f"Orphan temp recordings: {len(controller._storage.orphan_temp_audio())}")

    if problems:
        print("\nProblems found:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--check" in argv:
        return _run_checks()

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    # QApplication (not QGuiApplication) is required: the Library and Settings
    # screens use QFileDialog, which is a QWidget. With only a QGuiApplication
    # every Choose… button prints
    # "QWidget: Cannot create a QWidget without QApplication" and shows nothing.
    app = QApplication(argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")

    font = QFont()
    font.setPointSize(15)
    app.setFont(font)

    main_qml = QML_DIR / "Main.qml"
    if not main_qml.is_file():
        print(f"QML entry point is missing: {main_qml}", file=sys.stderr)
        return 1

    engine, controller, load_errors = _build_engine()
    controller.refresh_orphan_count()

    engine.load(QUrl.fromLocalFile(str(main_qml)))
    if not engine.rootObjects():
        print(f"Failed to load QML from {main_qml}", file=sys.stderr)
        for error in load_errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    # After the window exists: the palette has to reach the window itself, not
    # only the application, or the window keeps the system colours.
    _install_palette(controller.theme, engine)

    for error in load_errors:
        print(f"QML warning: {error}", file=sys.stderr)

    app.aboutToQuit.connect(controller.quitApp)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())