"""Readable layout for Whisper segments. Structure only, never content.

Whisper gives a list of segments with millisecond offsets. Every screen and
every export formats them through this one module so the timing format cannot
drift between the Finished screen, History and the Markdown file.

Hard limits, from the project's content rules:

* never invent a timestamp or a boundary;
* never add, drop or reorder words;
* never correct spelling, grammar or punctuation;
* only trim, collapse repeated whitespace, and lay the text out as lines and
  paragraphs.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# One format everywhere, always. `HH:MM:SS.mmm` is what the task requires for
# long recordings, and using it for short ones too is what keeps the Finished
# screen, History and the export from disagreeing about how a time reads.
#
# The notice lives in `readable`, the module that actually does the formatting.
# It used to say "Text is not corrected", which stopped being true the moment
# sentences were split and capitalised for readability. What the app does not do
# is evaluate the words, so that is what it says.
from diloger.transcription.readable import NOTICE  # noqa: F401  (re-export)
# Plain sentences, no Markdown emphasis: the same body is rendered as QML
# text, where `_` and `*` would appear as literal characters.
NO_TRANSCRIPT = "Transcript has not been generated yet."
NO_SPEECH = "No speech was recognised in the recording."
RANGE_SEPARATOR = " – "

_WHITESPACE = re.compile(r"\s+")
_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$")


def as_ms(value: Any) -> Optional[int]:
    """Milliseconds from a number, or None when it is not a time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(round(value)))


def timestamp_ms(value: Any) -> Optional[int]:
    """Milliseconds from whisper.cpp's "HH:MM:SS,mmm" string form."""
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP.match(value.strip())
    if match is None:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = (match.group(4) or "0").ljust(3, "0")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(fraction)


def start_ms(segment: Any) -> int:
    """Segment start in milliseconds, tolerating the older camelCase keys."""
    if not isinstance(segment, dict):
        return 0
    for key in ("start_ms", "startMs", "from_ms"):
        found = as_ms(segment.get(key))
        if found is not None:
            return found
    timestamps = segment.get("timestamps")
    if isinstance(timestamps, dict):
        return timestamp_ms(timestamps.get("from")) or 0
    return 0


def end_ms(segment: Any) -> int:
    """Segment end in milliseconds, tolerating the older camelCase keys."""
    if not isinstance(segment, dict):
        return 0
    for key in ("end_ms", "endMs", "to_ms"):
        found = as_ms(segment.get(key))
        if found is not None:
            return found
    timestamps = segment.get("timestamps")
    if isinstance(timestamps, dict):
        return timestamp_ms(timestamps.get("to")) or 0
    return 0


def format_timestamp(milliseconds: int) -> str:
    """`HH:MM:SS.mmm`. Hours are not wrapped, so a long session stays readable."""
    total = max(0, int(milliseconds))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def format_range(start: int, end: int) -> str:
    """`[00:00:00.000 – 00:00:04.520]`, the range prefix of one block."""
    return (
        f"[{format_timestamp(start)}{RANGE_SEPARATOR}{format_timestamp(max(end, start))}]"
    )


def normalize_text(text: Any) -> str:
    """Trim the padding and collapse runs of whitespace; keep every word."""
    if not isinstance(text, str):
        return ""
    return _WHITESPACE.sub(" ", text).strip()


def segment_block(segment: Any) -> str:
    """One timestamped block, or "" when the segment carries no words.

    A segment with no text is left out rather than shown as an empty line with
    a timestamp: an empty range would be structure with nothing behind it.
    """
    text = normalize_text(segment.get("text") if isinstance(segment, dict) else None)
    if not text:
        return ""
    return f"{format_range(start_ms(segment), end_ms(segment))} {text}"


def segment_blocks(segments: Optional[Iterable[Any]]) -> list[str]:
    return [block for block in (segment_block(s) for s in segments or []) if block]


def transcript_body(segments: Optional[Iterable[Any]], generated: bool = False) -> str:
    """The body under `## Transcript`, blocks separated by a blank line.

    The default says "not generated" rather than "no speech": claiming a
    silent recording for a session Whisper never looked at would hide a failed
    or skipped transcription behind an empty transcript.
    """
    blocks = segment_blocks(segments)
    if blocks:
        return "\n\n".join(blocks)
    return NO_SPEECH if generated else NO_TRANSCRIPT