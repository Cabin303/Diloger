"""Content import. Independent from UI and Session Engine."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from diloger.domain.models import ContentSet, Prompt

SUPPORTED_SUFFIXES = (".txt", ".md")


class ContentImportError(Exception):
    """Recoverable import problem with a user-facing reason."""


class ContentImporter(Protocol):
    def load_file(self, path: Path) -> ContentSet: ...
    def list_files(self, directory: Path) -> list[Path]: ...


def parse_lines(raw: str, path: Path) -> list[Prompt]:
    prompts: list[Prompt] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        ordinal = len(prompts)
        prompt_id = f"{ordinal}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"
        prompts.append(Prompt(id=prompt_id, ordinal=ordinal, text=text))
    return prompts


def read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ContentImportError(f"Cannot read {path.name}: {exc.strerror or exc}") from exc
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContentImportError(
            f"{path.name} is not valid UTF-8 (byte {exc.start}). Convert the file to UTF-8."
        ) from exc


class FileContentImporter:
    def load_file(self, path: Path) -> ContentSet:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ContentImportError(
                f"Unsupported file type '{suffix or path.name}'. Use .txt or .md."
            )
        if not path.is_file():
            raise ContentImportError(f"File not found: {path}")

        prompts = parse_lines(read_text(path), path)
        if not prompts:
            raise ContentImportError(f"{path.name} contains no prompts (all lines are empty).")

        content_id = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
        return ContentSet(
            id=content_id,
            sourcePath=str(path),
            displayName=path.stem,
            format=suffix.lstrip("."),
            prompts=tuple(prompts),
        )

    def list_files(self, directory: Path) -> list[Path]:
        directory = Path(directory)
        if not directory.is_dir():
            raise ContentImportError(f"Library folder not found: {directory}")
        found = [
            p
            for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        ]
        return found
