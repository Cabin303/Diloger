"""External Markdown export.

The export is a file the user chose, outside the session folder. Writing the
internal `transcript.md` is not an export, so this module only ever reports
success for a file it verified on disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from diloger.storage.session_store import _epoch_from_session_field

MARKDOWN_SUFFIX = ".md"
_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


@dataclass
class ExportResult:
    """Outcome of one export attempt.

    `status` distinguishes the three cases a caller has to tell apart: the user
    saved a file, the user cancelled the dialog, or the write failed. A silent
    failure would look identical to a cancel in the UI.
    """

    status: str
    path: str = ""
    directory: str = ""
    error: str = ""

    @property
    def saved(self) -> bool:
        return self.status == "saved"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "path": self.path,
            "directory": self.directory,
            "error": self.error,
        }


def normalise_path(target: Path | str) -> Path:
    """The path that will actually be written: Markdown, not `.md.txt`."""
    path = Path(str(target)).expanduser()
    if path.suffix.lower() != MARKDOWN_SUFFIX:
        path = path.with_name(path.name + MARKDOWN_SUFFIX)
    return path


def suggested_file_name(session: dict, session_id: str = "") -> str:
    """A safe default name from the session timestamp and content source."""
    started = _epoch_from_session_field(session.get("startedAtEpoch"))
    if started is None:
        started = _epoch_from_session_field(session.get("startedAt"))
    stamp = (
        datetime.fromtimestamp(started).strftime("%Y-%m-%d_%H-%M-%S")
        if started
        else (str(session_id).split("_")[0] or "session")
    )
    source = Path(str(session.get("contentSourcePath") or "")).stem
    source = _WHITESPACE.sub("-", source).strip("-")
    source = _UNSAFE.sub("", source)[:48]
    return f"diloger-{stamp}-{source}{MARKDOWN_SUFFIX}" if source else f"diloger-{stamp}{MARKDOWN_SUFFIX}"


def export_markdown(target: Path | str, markdown: str) -> ExportResult:
    """Write `markdown` as UTF-8 and confirm the file is really there.

    A report of success is only returned after the file exists, is a file, and
    is not empty: those are exactly the states in which the user believes they
    have a transcript and does not.
    """
    path = normalise_path(target)
    if not markdown.strip():
        return ExportResult(
            status="error",
            directory=str(path.parent),
            error="There is nothing to export yet.",
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        return ExportResult(
            status="error",
            directory=str(path.parent),
            error=f"Could not write {path.name}: {exc.strerror or exc}",
        )

    if not path.is_file() or path.stat().st_size == 0:
        return ExportResult(
            status="error",
            path=str(path),
            directory=str(path.parent),
            error=f"{path.name} was not created.",
        )
    return ExportResult(status="saved", path=str(path), directory=str(path.parent))