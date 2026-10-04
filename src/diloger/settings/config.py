"""Application configuration. Local JSON, no secrets, no cloud."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from diloger.app.theme import DEFAULT_THEME, THEME_MODES

MIN_DELAY_SECONDS = 0.2

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "Diloger"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    libraryPath: str = ""
    sessionsPath: str = ""
    # Only the folder an export last went to is remembered, never a file name:
    # the next Save dialog opens there with a fresh suggested name. A folder
    # that no longer exists is not a validation problem; the export falls back
    # to a directory that does.
    exportDirectory: str = ""
    whisperCLIPath: str = ""
    whisperModelPath: str = ""
    ttsProvider: str = "macos_say"
    ttsVoice: str = "Kokoro Heart"
    ttsRate: int = 175
    defaultDelaySeconds: float = 5.0
    recordingEnabledByDefault: bool = False
    keepRecordingByDefault: bool = False
    saveTypedAnswersByDefault: bool = False
    theme: str = DEFAULT_THEME
    shortcuts: dict = field(default_factory=dict)

    def validate(self) -> list[str]:
        problems = []
        if self.libraryPath and not Path(self.libraryPath).is_dir():
            problems.append(f"Library folder not found: {self.libraryPath}")
        if self.sessionsPath and not Path(self.sessionsPath).is_dir():
            problems.append(f"Sessions folder not found: {self.sessionsPath}")
        if self.ttsRate < 100 or self.ttsRate > 400:
            problems.append("TTS rate must be between 100 and 400 words per minute.")
        if self.defaultDelaySeconds < MIN_DELAY_SECONDS or self.defaultDelaySeconds > 300:
            problems.append(
                f"Fixed Timer delay must be between {MIN_DELAY_SECONDS:g} and 300 seconds.")
        if self.theme not in THEME_MODES:
            problems.append(f"Theme must be one of: {', '.join(THEME_MODES)}.")
        return problems


DEFAULT_SHORTCUTS = {
    "pauseResume": "Space",
    "next": "Return",
    "repeat": "R",
    "reveal": "V",
    "skip": "S",
    "stop": "Escape",
}


def load_config(path: Path | str | None = None) -> AppConfig:
    """Read the config. `path` overrides the default location.

    The override exists so tests and diagnostics never have to touch the real
    user configuration.
    """
    target = Path(path) if path else CONFIG_PATH
    if not target.is_file():
        cfg = AppConfig(shortcuts=dict(DEFAULT_SHORTCUTS))
        return cfg
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppConfig(shortcuts=dict(DEFAULT_SHORTCUTS))
    known = {f for f in AppConfig.__dataclass_fields__}
    data = {k: v for k, v in data.items() if k in known}
    if not data.get("shortcuts"):
        data["shortcuts"] = dict(DEFAULT_SHORTCUTS)
    return AppConfig(**data)


def save_config(cfg: AppConfig, path: Path | str | None = None) -> None:
    target = Path(path) if path else CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(cfg)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
