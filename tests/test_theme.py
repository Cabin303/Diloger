from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPalette

from diloger.app.theme import (
    COLOR_TOKENS,
    DARK_TOKENS,
    DEFAULT_THEME,
    INT_TOKENS,
    LIGHT_TOKENS,
    THEME_DARK,
    THEME_LIGHT,
    THEME_MODES,
    THEME_SYSTEM,
    TOKENS_BY_THEME,
    Theme,
    build_palette,
    normalise_mode,
    system_theme,
)

# Text pairs the UI actually renders. Every one has to stay readable, or the
# theme swap trades a dark-on-dark problem for a light-on-light one.
TEXT_PAIRS = (
    ("text", "bg"),
    ("text", "panel"),
    ("text", "panelAlt"),
    ("text", "sunk"),
    ("textDim", "bg"),
    ("textDim", "panel"),
    ("textFaint", "panel"),
    ("onAccent", "accent"),
    # The selected history row paints on accentDim, which is a mid-tone in both
    # themes, so it needs its own foregrounds. These three pairs are why the
    # tokens exist; without them the row quietly drops to ~3.4:1.
    ("onSelection", "accentDim"),
    ("onSelectionDim", "accentDim"),
    ("onSelectionAccent", "accentDim"),
    ("text", "dangerSurface"),
    ("text", "warnSurface"),
    ("text", "okSurface"),
)

STATUS_PAIRS = (
    ("record", "dangerSurface"),
    ("warn", "warnSurface"),
    ("ok", "okSurface"),
    ("record", "bg"),
    ("warn", "bg"),
    ("ok", "bg"),
)


def relative_luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        srgb = value / 255.0
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(foreground: str, background: str) -> float:
    a, b = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


# ------------------------------------------------------------------ tokens


@pytest.mark.parametrize("table", [DARK_TOKENS, LIGHT_TOKENS])
def test_every_token_name_has_a_value_in_both_tables(table):
    for name in COLOR_TOKENS + INT_TOKENS:
        assert name in table, f"{name} missing from theme table"
        assert table[name] != ""


@pytest.mark.parametrize("table", [DARK_TOKENS, LIGHT_TOKENS])
def test_colour_tokens_are_hex(table):
    for name in COLOR_TOKENS:
        value = table[name]
        assert value.startswith("#") and len(value) == 7, f"{name}={value!r}"
        int(value[1:], 16)


def test_the_two_themes_actually_differ():
    for name in COLOR_TOKENS:
        assert DARK_TOKENS[name] != LIGHT_TOKENS[name], f"{name} is the same in both themes"


def test_metric_tokens_are_shared_by_both_themes():
    for name in INT_TOKENS:
        assert DARK_TOKENS[name] == LIGHT_TOKENS[name]


# ---------------------------------------------------------------- contrast


@pytest.mark.parametrize("table", [DARK_TOKENS, LIGHT_TOKENS])
def test_text_pairs_stay_readable_in_every_theme(table):
    label = "dark" if table is DARK_TOKENS else "light"
    for foreground, background in TEXT_PAIRS:
        ratio = contrast(table[foreground], table[background])
        assert ratio >= 4.5, f"{label}: {foreground} on {background} is {ratio:.2f}:1"


@pytest.mark.parametrize("table", [DARK_TOKENS, LIGHT_TOKENS])
def test_status_colours_stay_readable_on_their_own_surfaces(table):
    label = "dark" if table is DARK_TOKENS else "light"
    for foreground, background in STATUS_PAIRS:
        ratio = contrast(table[foreground], table[background])
        assert ratio >= 4.5, f"{label}: {foreground} on {background} is {ratio:.2f}:1"


# ------------------------------------------------------------------ palette


@pytest.mark.parametrize("name,mode", [("dark", THEME_DARK), ("light", THEME_LIGHT)])
def test_palette_uses_the_tokens_for_every_control_role(name, mode):
    tokens = TOKENS_BY_THEME[mode]
    palette = build_palette(tokens)
    active = QPalette.ColorGroup.Active
    disabled = QPalette.ColorGroup.Disabled
    role = QPalette.ColorRole
    assert palette.color(active, role.Window).name() == tokens["bg"]
    assert palette.color(active, role.Base).name() == tokens["panel"]
    assert palette.color(active, role.Button).name() == tokens["panelAlt"]
    assert palette.color(active, role.ButtonText).name() == tokens["text"]
    assert palette.color(active, role.Text).name() == tokens["text"]
    assert palette.color(active, role.PlaceholderText).name() == tokens["textFaint"]
    assert palette.color(active, role.Highlight).name() == tokens["accent"]
    assert palette.color(active, role.HighlightedText).name() == tokens["onAccent"]
    assert palette.color(active, role.AlternateBase).name() == tokens["sunk"]
    assert palette.color(active, role.ToolTipBase).name() == tokens["panelAlt"]
    assert palette.color(disabled, role.ButtonText).name() == tokens["textFaint"]
    assert palette.color(disabled, role.Text).name() == tokens["textFaint"]


def test_disabled_button_text_is_not_invisible_against_its_own_button():
    for tokens in (DARK_TOKENS, LIGHT_TOKENS):
        palette = build_palette(tokens)
        disabled = QPalette.ColorGroup.Disabled
        role = QPalette.ColorRole
        ratio = contrast(
            palette.color(disabled, role.ButtonText).name(),
            palette.color(disabled, role.Button).name(),
        )
        label = "dark" if tokens is DARK_TOKENS else "light"
        assert ratio >= 3.0, f"{label}: disabled button text is {ratio:.2f}:1"


def test_highlighted_text_is_readable_on_the_highlight_colour():
    for tokens in (DARK_TOKENS, LIGHT_TOKENS):
        palette = build_palette(tokens)
        ratio = contrast(
            palette.color(QPalette.ColorRole.HighlightedText).name(),
            palette.color(QPalette.ColorRole.Highlight).name(),
        )
        label = "dark" if tokens is DARK_TOKENS else "light"
        assert ratio >= 4.5, f"{label}: selection text is {ratio:.2f}:1"


# --------------------------------------------------------------------- mode


def test_default_theme_is_dark():
    assert DEFAULT_THEME == THEME_DARK


@pytest.mark.parametrize("value", THEME_MODES)
def test_supported_modes_round_trip(value):
    assert normalise_mode(value) == value


@pytest.mark.parametrize("value", ["", "   ", "sepia", None, "0", "true"])
def test_unknown_mode_falls_back_to_the_default(value):
    assert normalise_mode(value) == DEFAULT_THEME


def test_mode_is_case_and_space_insensitive():
    assert normalise_mode("  Dark ") == THEME_DARK
    assert normalise_mode("LIGHT") == THEME_LIGHT


def test_system_theme_reports_one_of_the_two_palettes():
    assert system_theme() in (THEME_DARK, THEME_LIGHT)


# ------------------------------------------------------------- theme object


def test_a_new_theme_defaults_to_dark():
    theme = Theme()
    assert theme.mode == DEFAULT_THEME
    assert theme.resolvedTheme == THEME_DARK
    assert theme.isDark is True
    assert theme.isLight is False


def test_switching_mode_swaps_every_token():
    theme = Theme(mode=THEME_DARK)
    theme.setMode(THEME_LIGHT)
    assert theme.resolvedTheme == THEME_LIGHT
    assert theme.tokens()["bg"] == LIGHT_TOKENS["bg"]
    assert theme.tokens()["text"] == LIGHT_TOKENS["text"]
    assert theme.isLight is True
    assert theme.isDark is False


def test_an_unknown_mode_does_not_break_the_theme():
    theme = Theme(mode=THEME_DARK)
    theme.setMode("chartreuse")
    assert theme.mode == THEME_DARK
    assert theme.resolvedTheme == THEME_DARK
    assert theme.bg == DARK_TOKENS["bg"]


def test_system_mode_resolves_to_a_concrete_theme():
    theme = Theme(mode=THEME_SYSTEM)
    assert theme.mode == THEME_SYSTEM
    assert theme.resolvedTheme == system_theme()
    assert theme.tokens()["bg"] == TOKENS_BY_THEME[theme.resolvedTheme]["bg"]


def test_every_token_is_readable_as_a_qt_property():
    theme = Theme(mode=THEME_DARK)
    names = {
        Theme.staticMetaObject.property(i).name()
        for i in range(Theme.staticMetaObject.propertyCount())
    }
    for name in COLOR_TOKENS:
        assert name in names, f"{name} is missing from the Theme metaobject"
    for name in INT_TOKENS:
        assert name in names, f"{name} is missing from the Theme metaobject"
        assert getattr(theme, name) == DARK_TOKENS[name]


def test_colour_tokens_are_strings_not_qcolours():
    """A QML palette binding needs the string form.

    Binding `palette.disabled.<role>` to a QColor resolves against the base
    palette instead of the Disabled group, so every colour token has to reach QML
    as a string or a disabled control silently repaints with active colours.
    """
    theme = Theme(mode=THEME_DARK)
    for name in COLOR_TOKENS:
        meta = Theme.staticMetaObject.property(
            Theme.staticMetaObject.indexOfProperty(name)
        )
        assert meta.typeName() == "QString", f"{name} is {meta.typeName()}"
        value = theme.property(name)
        assert isinstance(value, str), f"{name} reads back as {type(value).__name__}"
        assert QColor(value).isValid(), f"{name} is not a usable colour: {value!r}"


def test_metric_tokens_read_back_as_integers():
    theme = Theme(mode=THEME_LIGHT)
    for name in INT_TOKENS:
        assert isinstance(getattr(theme, name), int)


def test_token_values_track_the_mode():
    theme = Theme(mode=THEME_LIGHT)
    assert theme.bg == LIGHT_TOKENS["bg"]
    theme.setMode(THEME_DARK)
    assert theme.bg == DARK_TOKENS["bg"]


def test_palette_matches_the_current_tokens():
    theme = Theme(mode=THEME_LIGHT)
    palette = theme.palette()
    assert palette.color(QPalette.ColorRole.Window).name() == LIGHT_TOKENS["bg"]


def test_modes_lists_every_supported_mode():
    assert Theme().modes() == list(THEME_MODES)


def test_a_theme_can_be_parented_and_deleted_cleanly():
    owner = Theme()
    child = Theme(mode=THEME_LIGHT, parent=owner)
    assert child.parent() is owner

# ---------------------------------------------------------------------------
# Theme consistency across the QML layer.
# ---------------------------------------------------------------------------

QML_DIR = Path(__file__).parent.parent / "src" / "diloger" / "qml"

# Theme properties that are not colour tokens but are read as `theme.<name>`.
THEME_PROPERTIES = {"mode", "resolvedTheme", "isDark", "isLight"}


def test_both_themes_define_the_same_tokens():
    """A token that exists in only one theme disappears when the user switches."""
    assert set(DARK_TOKENS) == set(LIGHT_TOKENS), (
        "only dark: "
        + ", ".join(sorted(set(DARK_TOKENS) - set(LIGHT_TOKENS)))
        + "; only light: "
        + ", ".join(sorted(set(LIGHT_TOKENS) - set(DARK_TOKENS)))
    )
    assert set(COLOR_TOKENS) | set(INT_TOKENS) == set(DARK_TOKENS)


def test_every_theme_reference_in_qml_resolves():
    """A typo like `theme.textMuted` renders nothing, and only in one place.

    There is no compile-time check for a property that does not exist, so the
    typo shows up as an invisible or wrongly coloured label with no error.
    """
    import re

    known = set(DARK_TOKENS) | THEME_PROPERTIES
    missing = {}
    for path in sorted(QML_DIR.glob("*.qml")):
        # Comments are stripped first: they name modules and files, not properties.
        body = re.sub(r"//[^\n]*", "", path.read_text())
        for name in re.findall(r"\btheme\.([A-Za-z_]\w*)", body):
            if name not in known:
                missing.setdefault(path.name, set()).add(name)
    assert not missing, (
        "QML reads theme properties that do not exist: "
        + "; ".join(f"{f}: {', '.join(sorted(v))}" for f, v in missing.items())
    )


def test_no_qml_file_hardcodes_a_colour():
    """Colours come from the tokens, so both themes apply to every screen."""
    import re

    offenders = {}
    pattern = re.compile(
        r"\bcolor\s*:\s*[\"'](#[0-9a-fA-F]{3,8}|white|black|red|green|blue|gray|grey)"
        r"|\bborder\.color\s*:\s*[\"'](#[0-9a-fA-F]{3,8})"
    )
    for path in sorted(QML_DIR.glob("*.qml")):
        body = re.sub(r"//[^\n]*", "", path.read_text())
        hits = pattern.findall(body)
        if any(any(part for part in hit) for hit in hits):
            offenders[path.name] = hits
    assert not offenders, f"QML hardcodes colours instead of using theme tokens: {offenders}"
