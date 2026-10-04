"""Assert that the real Qt Basic controls resolve to the theme tokens.

QQuickWindow does not expose its palette to Python, so the check runs inside QML:
the probe window holds one control of each kind and publishes the colours those
controls actually paint with, which Python then compares against the token table.

Run it with:

    .venv/bin/python tools/verify_theme.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QUICK_BACKEND", "software")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from diloger.app.theme import (  # noqa: E402
    DARK_TOKENS,
    LIGHT_TOKENS,
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    build_palette,
    system_theme,
)
from diloger.app.controller import AppController  # noqa: E402

QML_DIR = ROOT / "src" / "diloger" / "qml"

failures: list[str] = []

# Palette role -> token, mirroring the bindings in Main.qml and build_palette().
WINDOW_ROLES = {
    "window": "bg",
    "windowText": "text",
    "base": "panel",
    "alternateBase": "sunk",
    "text": "text",
    "placeholderText": "textFaint",
    "button": "panelAlt",
    "buttonText": "text",
    "brightText": "onAccent",
    "light": "border",
    "midlight": "border",
    "mid": "borderStrong",
    "dark": "borderStrong",
    "shadow": "sunk",
    "highlight": "accent",
    "highlightedText": "onAccent",
    "link": "accent",
    "linkVisited": "accent",
    "toolTipBase": "panelAlt",
    "toolTipText": "text",
}

# Control -> the role that control paints its surface/text with.
CONTROLS = {
    "button": ("button", "buttonText"),
    "disabledButton": ("button", "buttonText"),
    "highlightedButton": ("button", "buttonText"),
    "focusedButton": ("button", "buttonText"),
    "checkBox": ("button", "buttonText"),
    "radioButton": ("button", "buttonText"),
    "switch": ("button", "buttonText"),
    "combo": ("button", "text"),
    "textField": ("base", "text"),
    "spinBox": ("base", "text"),
    "slider": ("button", "highlight"),
    "progressBar": ("highlight", "text"),
    "scrollBar": ("button", "buttonText"),
    "dialog": ("window", "windowText"),
    "toolTip": ("toolTipBase", "toolTipText"),
}

PROBE_QML = """
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Mirrors the palette bindings in Main.qml so the probe measures the same
// mechanism the app uses, with the same tokens.
ApplicationWindow {
    id: probe
    width: 700
    height: 620
    visible: true
    color: theme.bg

    palette.window: theme.bg
    palette.windowText: theme.text
    palette.base: theme.panel
    palette.alternateBase: theme.sunk
    palette.text: theme.text
    palette.placeholderText: theme.textFaint
    palette.button: theme.panelAlt
    palette.buttonText: theme.text
    palette.brightText: theme.onAccent
    palette.light: theme.border
    palette.midlight: theme.border
    palette.mid: theme.borderStrong
    palette.dark: theme.borderStrong
    palette.shadow: theme.sunk
    palette.highlight: theme.accent
    palette.highlightedText: theme.onAccent
    palette.link: theme.accent
    palette.linkVisited: theme.accent
    palette.toolTipBase: theme.panelAlt
    palette.toolTipText: theme.text

    // Mirrors Main.qml: the groups are assigned after the bindings settle,
    // because re-evaluating a bound base role resets the other groups.
    function applyColourGroups() {
        palette.inactive.window = theme.bg;
        palette.inactive.windowText = theme.text;
        palette.inactive.base = theme.panel;
        palette.inactive.alternateBase = theme.sunk;
        palette.inactive.text = theme.text;
        palette.inactive.placeholderText = theme.textFaint;
        palette.inactive.button = theme.panelAlt;
        palette.inactive.buttonText = theme.text;
        palette.inactive.brightText = theme.onAccent;
        palette.inactive.light = theme.border;
        palette.inactive.midlight = theme.border;
        palette.inactive.mid = theme.borderStrong;
        palette.inactive.dark = theme.borderStrong;
        palette.inactive.shadow = theme.sunk;
        palette.inactive.highlight = theme.accent;
        palette.inactive.highlightedText = theme.onAccent;
        palette.inactive.link = theme.accent;
        palette.inactive.linkVisited = theme.accent;
        palette.inactive.toolTipBase = theme.panelAlt;
        palette.inactive.toolTipText = theme.text;

        palette.disabled.window = theme.bg;
        palette.disabled.windowText = theme.textFaint;
        palette.disabled.base = theme.sunk;
        palette.disabled.alternateBase = theme.sunk;
        palette.disabled.text = theme.textFaint;
        palette.disabled.placeholderText = theme.textFaint;
        palette.disabled.button = theme.sunk;
        palette.disabled.buttonText = theme.textFaint;
        palette.disabled.brightText = theme.textFaint;
        palette.disabled.light = theme.border;
        palette.disabled.midlight = theme.border;
        palette.disabled.mid = theme.border;
        palette.disabled.dark = theme.border;
        palette.disabled.shadow = theme.sunk;
        palette.disabled.highlight = theme.border;
        palette.disabled.highlightedText = theme.textFaint;
        palette.disabled.link = theme.textFaint;
        palette.disabled.linkVisited = theme.textFaint;
        palette.disabled.toolTipBase = theme.sunk;
        palette.disabled.toolTipText = theme.textFaint;
    }

    Connections {
        target: theme
        function onPaletteChanged() { probe.applyColourGroups() }
    }
    // Every palette role the window resolved to.
    property var windowPalette: ({
        "window": String(palette.window),
        "windowText": String(palette.windowText),
        "base": String(palette.base),
        "alternateBase": String(palette.alternateBase),
        "text": String(palette.text),
        "placeholderText": String(palette.placeholderText),
        "button": String(palette.button),
        "buttonText": String(palette.buttonText),
        "brightText": String(palette.brightText),
        "light": String(palette.light),
        "midlight": String(palette.midlight),
        "mid": String(palette.mid),
        "dark": String(palette.dark),
        "shadow": String(palette.shadow),
        "highlight": String(palette.highlight),
        "highlightedText": String(palette.highlightedText),
        "link": String(palette.link),
        "linkVisited": String(palette.linkVisited),
        "toolTipBase": String(palette.toolTipBase),
        "toolTipText": String(palette.toolTipText)
    })

    // What each control actually resolved to.
    property var resolved: ({
        "button": String(button.palette.button) + "|" + String(button.palette.buttonText),
        "disabledButton": String(disabledButton.palette.button) + "|" + String(disabledButton.palette.buttonText),
        "highlightedButton": String(highlightedButton.palette.button) + "|" + String(highlightedButton.palette.buttonText),
        "focusedButton": String(focusedButton.palette.button) + "|" + String(focusedButton.palette.buttonText),
        "checkBox": String(checkBox.palette.button) + "|" + String(checkBox.palette.buttonText),
        "radioButton": String(radioButton.palette.button) + "|" + String(radioButton.palette.buttonText),
        "switch": String(sw.palette.button) + "|" + String(sw.palette.buttonText),
        "combo": String(combo.palette.button) + "|" + String(combo.palette.text),
        "textField": String(textField.palette.base) + "|" + String(textField.palette.text),
        "spinBox": String(spinBox.palette.base) + "|" + String(spinBox.palette.text),
        "slider": String(slider.palette.button) + "|" + String(slider.palette.highlight),
        "progressBar": String(progressBar.palette.highlight) + "|" + String(progressBar.palette.text),
        "scrollBar": String(scrollBar.palette.button) + "|" + String(scrollBar.palette.buttonText),
        "dialog": String(dialog.palette.window) + "|" + String(dialog.palette.windowText),
        "toolTip": String(tip.palette.toolTipBase) + "|" + String(tip.palette.toolTipText)
    })

    property var diagnostics: ({
        "win.button": String(palette.button),
        "win.disabled.button": String(palette.disabled.button),
        "win.inactive.button": String(palette.inactive.button),
        "win.active.button": String(palette.active.button),
        "disabledButton.enabled": String(disabledButton.enabled),
        "disabledButton.palette.button": String(disabledButton.palette.button),
        "disabledButton.palette.disabled.button": String(disabledButton.palette.disabled.button)
    })

    ColumnLayout {
        anchors.fill: parent
        Button { id: button }
        Button { id: disabledButton; enabled: false }
        Button { id: highlightedButton; highlighted: true }
        Button { id: focusedButton }
        CheckBox { id: checkBox }
        RadioButton { id: radioButton }
        Switch { id: sw }
        ComboBox { id: combo; model: ["a"] }
        TextField { id: textField }
        SpinBox { id: spinBox; from: 0; to: 10 }
        Slider { id: slider; from: 0; to: 10; value: 5 }
        ProgressBar { id: progressBar; from: 0; to: 10; value: 5 }
        ScrollBar { id: scrollBar }
    }

    Dialog { id: dialog; title: "t"; standardButtons: Dialog.Ok }

    ToolTip {
        id: tip
        parent: button
        visible: true
        text: "tip"
    }

    Component.onCompleted: {
        probe.applyColourGroups()
        focusedButton.forceActiveFocus()
    }
}
"""


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)
        print(f"  FAIL  {message}")


def values_of(root, name: str) -> dict:
    got = root.property(name)
    if not hasattr(got, "toVariant"):
        check(False, f"{name} unavailable on the probe window")
        return {}
    return {k: str(v).lower() for k, v in got.toVariant().items()}


def verify_window(label: str, roles: dict, tokens: dict) -> None:
    print(f"\n[{label}] window palette roles")
    for role, token in WINDOW_ROLES.items():
        actual = roles.get(role, "")
        expected = tokens[token].lower()
        check(actual == expected, f"{label}: palette.{role} is {actual or '<none>'}, want {expected} ({token})")
        flag = "ok " if actual == expected else "FAIL"
        print(f"  {flag} {role:<16} {actual or '<none>':<9} <- {token}")


def verify_controls(label: str, resolved: dict, tokens: dict) -> None:
    print(f"\n[{label}] what each control resolved to")
    for control, (surface_role, text_role) in CONTROLS.items():
        raw = resolved.get(control, "")
        surface, _, text = raw.partition("|")
        surface_token = WINDOW_ROLES[surface_role]
        text_token = WINDOW_ROLES[text_role]
        want_surface = tokens[surface_token].lower()
        want_text = tokens[text_token].lower()
        # A disabled control resolves against the Disabled colour group, so it
        # is checked against the faint/sunk tokens instead.
        if control == "disabledButton":
            want_surface = tokens["sunk"].lower()
            want_text = tokens["textFaint"].lower()
        ok = surface == want_surface and text == want_text
        check(ok, f"{label}: {control} resolved {surface}|{text}, want {want_surface}|{want_text}")
        flag = "ok " if ok else "FAIL"
        print(f"  {flag} {control:<17} surface={surface or '<none>':<9} text={text or '<none>':<9}")


def verify_python_palette(label: str, tokens: dict) -> None:
    """The palette handed to native QWidget dialogs must agree with QML."""
    from PySide6.QtGui import QPalette

    palette = build_palette(tokens)
    print(f"\n[{label}] python QPalette (native dialogs)")
    for role, token in (
        (QPalette.ColorRole.Window, "bg"),
        (QPalette.ColorRole.Base, "panel"),
        (QPalette.ColorRole.Button, "panelAlt"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.Highlight, "accent"),
        (QPalette.ColorRole.HighlightedText, "onAccent"),
        (QPalette.ColorRole.PlaceholderText, "textFaint"),
    ):
        actual = palette.color(role).name().lower()
        expected = tokens[token].lower()
        check(actual == expected, f"{label}: QPalette {role.name} is {actual}, want {expected}")
        flag = "ok " if actual == expected else "FAIL"
        print(f"  {flag} {role.name:<16} {actual:<9} <- {token}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR.parent))
    controller = AppController()
    controller.setParent(engine)
    theme = controller.theme
    theme.setParent(engine)

    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(str(e.toString()) for e in errors))

    ctx = engine.rootContext()
    ctx.setContextProperty("app", controller)
    ctx.setContextProperty("theme", theme)
    ctx.setContextProperty("player", controller.player)

    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        print("Main.qml failed to load:")
        for warning in warnings:
            print(f"  {warning}")
        return 1
    main_window = engine.rootObjects()[0]
    print(f"Main.qml loaded: {main_window.metaObject().className()}")

    probe_path = Path(tempfile.gettempdir()) / "diloger_theme_probe.qml"
    probe_path.write_text(PROBE_QML, encoding="utf-8")
    engine.load(QUrl.fromLocalFile(str(probe_path)))
    if len(engine.rootObjects()) < 2:
        print("probe window failed to load:")
        for warning in warnings:
            print(f"  {warning}")
        return 1
    probe = engine.rootObjects()[1]
    print(f"probe loaded: {probe.metaObject().className()}")

    for mode, tokens in ((THEME_DARK, DARK_TOKENS), (THEME_LIGHT, LIGHT_TOKENS)):
        theme.setMode(mode)
        theme.apply()
        print(f"\n=== theme.mode={theme.mode} resolved={theme.resolvedTheme} "
              f"isDark={theme.isDark} isLight={theme.isLight} ===")
        verify_window(mode, values_of(probe, "windowPalette"), tokens)
        verify_controls(mode, values_of(probe, "resolved"), tokens)
        verify_python_palette(mode, tokens)

        for token in ("bg", "panel", "text", "accent", "textFaint", "dangerSurface", "warnSurface"):
            actual = str(theme.property(token)).lower()
            check(actual == tokens[token].lower(),
                  f"{mode}: theme.{token} is {actual}, want {tokens[token]}")
        check(theme.gap == tokens["gap"], f"{mode}: theme.gap is {theme.gap}, want {tokens['gap']}")
        check(theme.radius == tokens["radius"], f"{mode}: theme.radius is {theme.radius}, want {tokens['radius']}")

    theme.setMode(THEME_SYSTEM)
    theme.apply()
    print(f"\n=== system mode ===\nresolved -> {theme.resolvedTheme} (system reports {system_theme()})")
    check(theme.resolvedTheme == system_theme(), "system mode did not follow the system")
    expected = {THEME_DARK: DARK_TOKENS, THEME_LIGHT: LIGHT_TOKENS}[theme.resolvedTheme]
    verify_window("system", values_of(probe, "windowPalette"), expected)
    verify_controls("system", values_of(probe, "resolved"), expected)

    print("\n--- QML warnings ---")
    real = [w for w in warnings if "unrecognized channel" not in w and "ToolTip attached" not in w]
    if real:
        for warning in real:
            print(f"  {warning}")
        check(False, f"{len(real)} QML warning(s)")
    else:
        print("  none")

    print("\n--- result ---")
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All theme checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())