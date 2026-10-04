"""Semantic colour tokens and the Qt Basic palette QML renders with.

One token table per resolved theme, and one palette built from those same
tokens. Every colour a screen or a control can ask for comes from here: QML
binds to the `theme.*` properties and the Qt Basic controls read the palette
built from the identical values, so a Button, CheckBox, ComboBox, Slider,
TextField, Dialog or ScrollBar cannot fall back to the macOS system colours
behind a dark panel.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Property, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPalette

THEME_SYSTEM = "system"
THEME_DARK = "dark"
THEME_LIGHT = "light"

THEME_MODES = (THEME_SYSTEM, THEME_DARK, THEME_LIGHT)
DEFAULT_THEME = THEME_DARK

COLOR_TOKENS = (
    "bg",
    "panel",
    "panelAlt",
    "sunk",
    "border",
    "borderStrong",
    "text",
    "textDim",
    "textFaint",
    "accent",
    "accentDim",
    "accentSoft",
    "onSelection",
    "onSelectionDim",
    "onSelectionAccent",
    "onAccent",
    "record",
    "warn",
    "ok",
    "hover",
    "focus",
    "dangerSurface",
    "warnSurface",
    "okSurface",
)

INT_TOKENS = ("radius", "radiusSmall", "gap")

DARK_TOKENS = {
    "bg": "#14161c",
    "panel": "#1d2029",
    "panelAlt": "#252936",
    "sunk": "#101219",
    "border": "#333846",
    "borderStrong": "#4d5568",
    "text": "#eef1f7",
    "textDim": "#98a0b3",
    "textFaint": "#868da3",
    "accent": "#6ea8fe",
    "accentDim": "#2f4a7a",
    "accentSoft": "#3d5a91",
    # Foregrounds for whatever sits on `accentDim`. The selection tint is a
    # mid-tone, so the normal dim and faint greys drop to 3.4:1 and 2.7:1 on it
    # and the selected row becomes the least readable row in the list. These
    # three are the lightest / darkest values that still clear 4.5:1 there.
    "onSelection": "#eef1f7",
    "onSelectionDim": "#c8cee0",
    "onSelectionAccent": "#9cc6ff",
    "onAccent": "#0b1220",
    "record": "#ff5f57",
    "warn": "#f5b544",
    "ok": "#4ade80",
    "hover": "#262b38",
    "focus": "#6ea8fe",
    "dangerSurface": "#3a1f1e",
    "warnSurface": "#3a3320",
    "okSurface": "#1d2620",
    "radius": 10,
    "radiusSmall": 8,
    "gap": 14,
}

LIGHT_TOKENS = {
    "bg": "#eef0f4",
    "panel": "#ffffff",
    "panelAlt": "#f5f6f9",
    "sunk": "#e8eaf0",
    "border": "#d4d8e0",
    "borderStrong": "#aab1c0",
    "text": "#191c22",
    "textDim": "#565d6b",
    "textFaint": "#6f7686",
    "accent": "#2560bd",
    "accentDim": "#cfe0f7",
    "accentSoft": "#dce8fa",
    # See the dark set: on `accentDim` the normal accent lands at 4.49:1 and
    # textFaint at 3.40:1, so a selected row has its own foregrounds here.
    "onSelection": "#191c22",
    "onSelectionDim": "#565d6b",
    "onSelectionAccent": "#2058b0",
    "onAccent": "#ffffff",
    "record": "#c53028",
    "warn": "#8a5d00",
    "ok": "#14683a",
    "hover": "#eef1f6",
    "focus": "#2560bd",
    "dangerSurface": "#fdeceb",
    "warnSurface": "#fdf4e3",
    "okSurface": "#e8f5ec",
    "radius": 10,
    "radiusSmall": 8,
    "gap": 14,
}

TOKENS_BY_THEME = {THEME_DARK: DARK_TOKENS, THEME_LIGHT: LIGHT_TOKENS}


def _gui_app():
    """The running QGuiApplication, or None under a bare QCoreApplication.

    Headless tests and diagnostics create a QCoreApplication for its event loop.
    It has no palette and no style hints, so both are reported as "no GUI here"
    rather than raising.
    """
    app = QGuiApplication.instance()
    return app if isinstance(app, QGuiApplication) else None


def normalise_mode(mode: str) -> str:
    """Any input to a supported mode, falling back to the default theme."""
    candidate = str(mode or "").strip().lower()
    return candidate if candidate in THEME_MODES else DEFAULT_THEME


def system_theme() -> str:
    """The theme the operating system is currently asking for.

    Qt 6.5+ reports it directly. Without a usable answer the system palette's own
    window lightness decides, so `system` still tracks macOS instead of silently
    pinning one of the two themes.
    """
    app = _gui_app()
    if app is None:
        return THEME_DARK
    hints = app.styleHints()
    read_scheme = getattr(hints, "colorScheme", None)
    if callable(read_scheme):
        try:
            value = read_scheme()
        except (TypeError, RuntimeError):
            value = Qt.ColorScheme.Unknown
        if value == Qt.ColorScheme.Light:
            return THEME_LIGHT
        if value == Qt.ColorScheme.Dark:
            return THEME_DARK
    window = QGuiApplication.palette().color(QPalette.ColorRole.Window)
    return THEME_LIGHT if window.lightness() > 128 else THEME_DARK


def build_palette(tokens: dict) -> QPalette:
    """The Qt Basic palette for one token table.

    Every role a Qt Quick Control reads is filled from the tokens the QML side
    uses, so the two cannot drift apart.
    """
    c = {name: QColor(tokens[name]) for name in COLOR_TOKENS}
    palette = QPalette()
    active = QPalette.ColorGroup.Active
    disabled = QPalette.ColorGroup.Disabled

    def put(role: QPalette.ColorRole, colour: QColor, group: QPalette.ColorGroup = active) -> None:
        palette.setColor(group, role, colour)

    put(QPalette.ColorRole.Window, c["bg"])
    put(QPalette.ColorRole.WindowText, c["text"])
    put(QPalette.ColorRole.Base, c["panel"])
    put(QPalette.ColorRole.AlternateBase, c["sunk"])
    put(QPalette.ColorRole.Text, c["text"])
    put(QPalette.ColorRole.PlaceholderText, c["textFaint"])
    put(QPalette.ColorRole.Button, c["panelAlt"])
    put(QPalette.ColorRole.ButtonText, c["text"])
    put(QPalette.ColorRole.BrightText, c["onAccent"])
    put(QPalette.ColorRole.Light, c["border"])
    put(QPalette.ColorRole.Midlight, c["border"])
    put(QPalette.ColorRole.Mid, c["borderStrong"])
    put(QPalette.ColorRole.Dark, c["borderStrong"])
    put(QPalette.ColorRole.Shadow, c["sunk"])
    put(QPalette.ColorRole.Highlight, c["accent"])
    put(QPalette.ColorRole.HighlightedText, c["onAccent"])
    put(QPalette.ColorRole.Link, c["accent"])
    put(QPalette.ColorRole.LinkVisited, c["accent"])
    put(QPalette.ColorRole.ToolTipBase, c["panelAlt"])
    put(QPalette.ColorRole.ToolTipText, c["text"])
    if hasattr(QPalette.ColorRole, "Accent"):
        put(QPalette.ColorRole.Accent, c["accent"])

    # A disabled control keeps a surface and steps the label down to the faint
    # token. Grey-on-grey is what made a disabled Select button read as a
    # different kind of control rather than an unavailable one.
    put(QPalette.ColorRole.Window, c["bg"], disabled)
    put(QPalette.ColorRole.WindowText, c["textFaint"], disabled)
    put(QPalette.ColorRole.Base, c["sunk"], disabled)
    put(QPalette.ColorRole.AlternateBase, c["sunk"], disabled)
    put(QPalette.ColorRole.Text, c["textFaint"], disabled)
    put(QPalette.ColorRole.Button, c["sunk"], disabled)
    put(QPalette.ColorRole.ButtonText, c["textFaint"], disabled)
    put(QPalette.ColorRole.Highlight, c["border"], disabled)
    put(QPalette.ColorRole.HighlightedText, c["textFaint"], disabled)
    return palette


class _ThemeImpl(QObject):
    """Behaviour of the theme object. Token properties are attached below."""

    def __init__(self, parent: Optional[QObject] = None, mode: str = DEFAULT_THEME) -> None:
        # Not super(): the class is rebuilt from this namespace with the token
        # properties added, so the implicit __class__ cell no longer applies.
        QObject.__init__(self, parent)
        self._mode = normalise_mode(mode)
        self._resolved = THEME_DARK
        self._tokens: dict = dict(DARK_TOKENS)
        self._system_watch = QTimer(self)
        self._system_watch.setInterval(2000)
        self._system_watch.timeout.connect(self._poll_system_theme)
        self._resolve(initial=True)

    # ---- tokens ---------------------------------------------------------

    def _hex(self, key: str) -> str:
        """The token as a `#rrggbb` string rather than a QColor.

        A QML colour binding accepts either, but `palette.disabled.<role>` only
        reaches the Disabled group when the value arrives as a string: handed a
        QColor it resolves against the base palette instead, which silently
        repaints disabled controls with the active colours.
        """
        return QColor(self._tokens.get(key, "#000000")).name(QColor.HexRgb)

    def _int(self, key: str) -> int:
        try:
            return int(self._tokens.get(key, 0))
        except (TypeError, ValueError):
            return 0

    def _resolve(self, initial: bool = False) -> None:
        """Recompute the token table for the current mode and republish it."""
        target = system_theme() if self._mode == THEME_SYSTEM else self._mode
        if target == self._resolved and not initial:
            return
        self._resolved = target
        self._tokens = dict(TOKENS_BY_THEME[target])
        # Only `system` needs watching; a fixed choice cannot drift. The watch
        # needs a live event dispatcher, so it only runs under a real app.
        if self._mode == THEME_SYSTEM and _gui_app() is not None:
            if not self._system_watch.isActive():
                self._system_watch.start()
        else:
            self._system_watch.stop()
        self.apply()
        self.paletteChanged.emit()

    def _poll_system_theme(self) -> None:
        if self._mode == THEME_SYSTEM:
            self._resolve()

    # ---- mode -----------------------------------------------------------
    # The properties that expose these values are attached in
    # `_with_token_properties`, which owns the single notify signal.

    def _get_mode(self) -> str:
        return self._mode

    def _get_resolved(self) -> str:
        return self._resolved

    def _get_is_dark(self) -> bool:
        return self._resolved == THEME_DARK

    def _get_is_light(self) -> bool:
        return self._resolved == THEME_LIGHT

    @Slot(result="QVariantList")
    def modes(self) -> list:
        return list(THEME_MODES)

    @Slot(str)
    def setMode(self, mode: str) -> None:
        """Switch themes. An unknown name falls back to the default theme."""
        normalised = normalise_mode(mode)
        self._mode = normalised
        # Re-resolve unconditionally: returning to the same mode after a system
        # change must still republish what the system asks for now.
        self._resolved = ""
        self._resolve()

    # ---- palette --------------------------------------------------------

    def palette(self) -> QPalette:
        """The palette for the active tokens. Rebuilt per call, never shared."""
        return build_palette(self._tokens)

    def apply(self) -> QPalette:
        """Install the palette on the application.

        Callers also push it onto each QQuickWindow: a window keeps its own
        palette, so the application palette alone leaves open windows untouched.
        """
        palette = build_palette(self._tokens)
        app = _gui_app()
        if app is not None:
            app.setPalette(palette)
        return palette

    @Slot()
    def applyToAllWindows(self) -> None:
        """Push the active palette onto every open window.

        A QQuickWindow keeps its own palette, so installing the application
        palette is not enough for windows that already exist.
        """
        app = _gui_app()
        if app is None:
            return
        for candidate in app.topLevelWindows():
            self.applyToWindow(candidate)

    def applyToWindow(self, window) -> None:
        """Push the active palette onto one window."""
        if window is None or not hasattr(window, "setPalette"):
            return
        window.setPalette(build_palette(self._tokens))

    def tokens(self) -> dict:
        """The active token table, for tests and diagnostics."""
        return dict(self._tokens)


def _with_token_properties(impl: type) -> type:
    """Attach one notifyable property per token to the theme class.

    The token tables above stay the single source of truth: the properties are
    generated from their names, so a token cannot exist for the palette and be
    missing in QML. The properties have to be in the namespace handed to
    `type()`, because PySide6 fixes the metaobject when the class is created and
    a later `setattr` would be invisible to QML.
    """
    skip = {"__dict__", "__weakref__", "__module__", "__qualname__", "__doc__"}
    namespace = {key: value for key, value in vars(impl).items() if key not in skip}
    changed = Signal()
    namespace["paletteChanged"] = changed
    for name in COLOR_TOKENS:
        namespace[name] = Property(
            str,
            (lambda key: (lambda self: self._hex(key)))(name),
            notify=changed,
        )
    for name in INT_TOKENS:
        namespace[name] = Property(
            int,
            (lambda key: (lambda self: self._int(key)))(name),
            notify=changed,
        )
    namespace["mode"] = Property(str, namespace.pop("_get_mode"), notify=changed)
    namespace["resolvedTheme"] = Property(str, namespace.pop("_get_resolved"), notify=changed)
    namespace["isDark"] = Property(bool, namespace.pop("_get_is_dark"), notify=changed)
    namespace["isLight"] = Property(bool, namespace.pop("_get_is_light"), notify=changed)
    return type("Theme", (QObject,), namespace)


Theme = _with_token_properties(_ThemeImpl)