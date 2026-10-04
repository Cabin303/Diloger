"""Prompt fingerprint for detecting content change between sessions."""

from __future__ import annotations

import hashlib
from pathlib import Path


def fingerprint(path: Path) -> str:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return "unavailable"
    return hashlib.sha256(data).hexdigest()[:16]
