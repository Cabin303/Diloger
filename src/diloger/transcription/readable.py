"""Readable presentation derived from a raw Whisper transcript.

Three representations exist and never overwrite each other:

1. `raw transcript` -- the segments exactly as whisper-cli produced them, kept in
   ``transcript.json`` under ``segments`` and in ``whisper.json`` byte for byte;
2. `readable transcript` -- what this module builds: the same words in the same
   order, given capital letters, sentence boundaries and timestamps;
3. `Markdown export`` -- the standalone document written to a file the user
   chooses.

What this module may and may not do
-----------------------------------
It may add a capital, a full stop, and a line break, because those are
presentation. It must not touch the words themselves: no spelling fix, no
grammar fix, no vocabulary change, no summary, no judgement of the answer.
``"in plant dry dock"`` stays exactly that way, and a wrong word is a fact about
the recording, not something the app gets to correct.

The evidence it is allowed to use, strongest first:

1. real segment timestamps Whisper reported;
2. the length of the pause between two segments;
3. the session event log -- ``ttsStart``/``ttsFinish`` say when the trainer's
   own voice was in the room, and the content file says what was spoken then;
4. the punctuation the user themselves wrote in the prompt.

There is deliberately no fallback that guesses. A boundary it cannot justify is
left as a boundary it cannot claim, and the block says ``Recorded speech``
instead of pretending the split is known.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

# Bumped whenever the rules below change, so a stored readable view can be
# recognised as out of date and rebuilt from the raw segments without Whisper.
READABLE_VERSION = 1

# A pause long enough that two segments are very unlikely to be one sentence.
# Below this, joining is the safer error: a missing full stop is cosmetic, a
# full stop invented inside a clause is a sentence the speaker did not say.
SENTENCE_GAP_MS = 650

# A longer pause than this starts a new block: a question and the answer to
# it are two blocks, not one paragraph.
GROUP_GAP_MS = 1500

# A segment fully inside a TTS window was spoken by the trainer, not the user.
# The recording is a microphone signal, so the trainer's playback leaks into it.
TRAINER_OVERLAP = 0.75
# Below this it is the user. Between the two it is honestly undecidable.
USER_OVERLAP = 0.25

LABEL_QUESTION = "Question"
LABEL_ANSWER = "Recorded answer"
LABEL_SPEECH = "Recorded speech"

NOTICE = (
    "Local Whisper output with timestamps. Text was formatted for readability; "
    "words were not evaluated."
)

_CLOSERS = ".;?!"
_WORD = re.compile(r"[^\s]+")
_WHITESPACE = re.compile(r"\s+")


# ---- small helpers, kept independent of the rest of the app ---------------


def _as_ms(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(round(value)))


def _segment_bounds(segment: Any) -> tuple[int, int]:
    from diloger.transcription.formatter import end_ms, start_ms

    return start_ms(segment), end_ms(segment)


def _text_of(segment: Any) -> str:
    if not isinstance(segment, dict):
        return ""
    raw = segment.get("text")
    if not isinstance(raw, str):
        return ""
    return _WHITESPACE.sub(" ", raw).strip()


# ---- speaker attribution --------------------------------------------------


def tts_windows(event_log: Optional[Iterable[Any]]) -> list[tuple[int, int, str]]:
    """When the trainer's voice was audible, from the session event log.

    ``ttsStart`` without a matching ``ttsFinish`` still yields a window up to
    the next event: a session that was stopped mid-prompt should not have the
    whole rest of the recording treated as the user talking.
    """
    events = [e for e in (event_log or []) if isinstance(e, dict)]
    starts: dict[str, int] = {}
    windows: list[tuple[int, int, str]] = []
    previous_offset = 0.0
    for event in events:
        offset = _as_ms(event.get("atOffsetMs"))
        if offset is None:
            seconds = event.get("atOffset")
            offset = _as_ms(float(seconds) * 1000) if isinstance(seconds, (int, float)) else None
        if offset is None:
            offset = int(previous_offset)
        previous_offset = offset
        prompt_id = str(event.get("promptId") or "")
        kind = str(event.get("type") or "")
        if kind == "ttsStart":
            starts[prompt_id] = offset
        elif kind == "ttsFinish":
            begin = starts.pop(prompt_id, None)
            if begin is None:
                continue
            if offset > begin:
                windows.append((begin, offset, prompt_id))
    for prompt_id, begin in starts.items():
        if previous_offset > begin:
            windows.append((begin, previous_offset, prompt_id))
    return windows


def _overlap_ms(start: int, end: int, window: tuple[int, int]) -> int:
    low, high = window
    return max(0, min(end, high) - max(start, low))


def trainer_share(start: int, end: int, windows: list[tuple[int, int, str]]) -> float:
    """Fraction of a segment that fell inside trainer playback."""
    span = end - start
    if span <= 0 or not windows:
        return 0.0
    covered = sum(_overlap_ms(start, end, (w[0], w[1])) for w in windows)
    return min(1.0, covered / span)


# ---- punctuation and capitalisation ---------------------------------------


def _ends_with_punctuation(text: str) -> bool:
    return bool(text) and text.rstrip()[-1] in _CLOSERS


def _ensure_full_stop(text: str) -> str:
    """One full stop, and never a second one."""
    body = text.rstrip()
    if not body:
        return body
    if _ends_with_punctuation(body):
        return body
    return body + "."


def capitalise(text: str) -> str:
    """The first letter upper-cased, every other character untouched.

    Deliberately not ``str.capitalize``: that lower-cases the rest of the word
    and would silently rewrite "I", "OK" and acronyms.
    """
    body = text.lstrip()
    if not body:
        return ""
    pad = text[: len(text) - len(body)]
    for index, char in enumerate(body):
        if char.isalpha():
            return pad + body[:index] + char.upper() + body[index + 1 :]
        if not char.isspace() and char not in "\"'([{":
            # A leading symbol or digit: nothing sensible to upper-case.
            return text
    return pad + body


def _prompt_punctuation(prompt_text: str) -> str:
    """The mark the user themselves put at the end of their own prompt.

    This is the only evidence used for `?` and `!`. It is not a guess about
    English: the punctuation already exists, in text the user typed, and the
    job here is only to keep it.
    """
    body = (prompt_text or "").strip()
    if not body:
        return ""
    last = body[-1]
    return last if last in "?!" else ""


def readable_sentences(
    texts: list[tuple[int, int, str]],
    closing_mark: str = "",
) -> list[str]:
    """Join timed fragments into sentences.

    A new sentence starts after a pause of at least `SENTENCE_GAP_MS`, or at the
    start of the list. Every fragment keeps its own words and their order; only
    the mark at the end of a sentence is added, and `closing_mark` is used for
    the final sentence of the list when the caller has evidence for it.
    """
    sentences: list[list[str]] = []
    current: list[str] = []
    previous_end: Optional[int] = None
    for start, end, text in texts:
        body = text.strip()
        if not body:
            continue
        gap = None if previous_end is None else start - previous_end
        if current and gap is not None and gap >= SENTENCE_GAP_MS:
            sentences.append(current)
            current = []
        current.append(body)
        previous_end = end
    if current:
        sentences.append(current)

    out: list[str] = []
    for index, words in enumerate(sentences):
        mark = closing_mark if index == len(sentences) - 1 else ""
        joined = " ".join(words)
        if not mark and _ends_with_punctuation(joined):
            out.append(joined.rstrip())
            continue
        out.append(_ensure_full_stop(joined) if not mark else joined.rstrip() + mark)
    return out


# ---- the view model --------------------------------------------------------


def _prompt_ordinal(prompt_id: str) -> int:
    try:
        return int(str(prompt_id).split(":", 1)[0]) + 1
    except (TypeError, ValueError):
        return 1


def prompt_texts_for_session(session: dict, search_dirs: Optional[Iterable[Any]] = None) -> dict:
    """Recover the question text from the user's own content file.

    `session.json` records which prompt ids were used but not their text, and a
    prompt id is ``<line ordinal>:<sha1 of the line>``. The content file is
    therefore the only authority on what was asked: Whisper only heard the
    trainer's playback leaking into the microphone, so its text is evidence of
    what was said, not a copy of what the user wrote.

    An unreadable or changed file yields no text, and the caller then says so
    honestly instead of substituting a Whisper guess for the question.
    """
    from diloger.content.importer import FileContentImporter

    wanted = {str(pid) for pid in (session.get("promptOrder") or []) if pid}
    if not wanted:
        return {}
    source = Path(str(session.get("contentSourcePath") or ""))
    candidates = [source] if source.is_file() else []
    for extra in search_dirs or []:
        path = Path(str(extra or ""))
        if path.is_file() and path.name == source.name:
            candidates.append(path)

    importer = FileContentImporter()
    for candidate in candidates:
        try:
            loaded = importer.load_file(candidate)
        except Exception:
            continue
        found = {p.id: p.text for p in loaded.prompts if p.id in wanted}
        if found:
            return found
    return {}


def _speaker(start: int, end: int, windows: list[tuple[int, int, str]]) -> tuple[str, str]:
    """Who spoke, and which prompt it belongs to.

    Returns ``(label, prompt_id)``. "mixed" is a real answer here: a segment
    that begins under the trainer's playback and ends after it stopped contains
    both voices, and no timestamp in the file says where one became the other.
    """
    if not windows:
        return LABEL_ANSWER, ""
    share = trainer_share(start, end, windows)
    best = max(windows, key=lambda w: _overlap_ms(start, end, (w[0], w[1])))
    if share >= TRAINER_OVERLAP:
        return LABEL_QUESTION, best[2]
    if share <= USER_OVERLAP:
        return LABEL_ANSWER, ""
    return LABEL_SPEECH, best[2]


def build_readable_blocks(
    segments: Optional[Iterable[Any]],
    *,
    event_log: Optional[Iterable[Any]] = None,
    prompt_texts: Optional[dict] = None,
    prompt_order: Optional[Iterable[Any]] = None,
) -> list[dict]:
    """Blocks for Finished and History, in the order they happened.

    Each block is ``{label, ordinal, promptId, start_ms, end_ms, text}``. The
    words come from the raw segments or from the user's content file; only a
    capital, a sentence mark and a timestamp label are ever added.

    Two sources, merged by time:

    * ``Question`` blocks come from the event log's TTS windows. The window says
      when the trainer spoke and the content file says what was spoken, so this
      is the question with no inference in it at all.
    * ``Recorded answer`` and ``Recorded speech`` blocks come from the Whisper
      segments. A segment that sits entirely outside every TTS window is the
      user. A segment that straddles one contains both voices and is reported as
      ``Recorded speech``, because nothing in the data says where one became the
      other -- calling it an answer would be the app guessing.
    """
    prompts = prompt_texts or {}
    windows = tts_windows(event_log)
    # The label counts questions as the user met them, not as lines in the
    # content file. The session is served in a random order, so the first
    # question spoken is very often not line 0.
    spoken = {str(pid): index + 1 for index, pid in enumerate(prompt_order or []) if pid}
    ordinal_for = lambda pid: spoken.get(pid) or _prompt_ordinal(pid)

    rows: list[tuple[int, int, str]] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        body = _text_of(segment)
        if not body:
            continue
        start_ms, end_ms = _segment_bounds(segment)
        rows.append((start_ms, end_ms, body))
    rows.sort(key=lambda row: (row[0], row[1]))

    blocks: list[dict] = []

    # The trainer's own questions, from the event log plus the content file.
    for window_start, window_end, prompt_id in sorted(windows):
        source = str(prompts.get(prompt_id) or "").strip()
        ordinal = ordinal_for(prompt_id)
        if source:
            # The user's own punctuation is kept as written: re-deciding it
            # would be editing text they wrote.
            text = capitalise(source) if _prompt_punctuation(source) else _ensure_full_stop(capitalise(source))
        else:
            # No content file to read: the question cannot be quoted, so it is
            # not invented from Whisper output either. The words Whisper heard
            # still appear below, as the speech they were.
            continue
        blocks.append(
            {
                "label": f"{LABEL_QUESTION} {ordinal}",
                "ordinal": ordinal,
                "promptId": prompt_id,
                "start_ms": window_start,
                "end_ms": window_end,
                "text": text,
            }
        )

    # Adjacent fragments of one turn belong to one block; a longer pause means a
    # different turn. Both thresholds come from real timing only.
    groups: list[list[tuple[int, int, str, str]]] = []
    for start_ms, end_ms, body in rows:
        share = trainer_share(start_ms, end_ms, windows)
        if share >= TRAINER_OVERLAP and windows:
            # The Question block above already carries this one, with the text
            # from the content file instead of the microphone's echo.
            continue
        label = LABEL_ANSWER if share <= USER_OVERLAP else LABEL_SPEECH
        if groups and groups[-1][-1][3] == label and start_ms - groups[-1][-1][1] < GROUP_GAP_MS:
            groups[-1].append((start_ms, end_ms, body, label))
        else:
            groups.append([(start_ms, end_ms, body, label)])

    for group in groups:
        sentences = readable_sentences([(r[0], r[1], r[2]) for r in group])
        # A sentence needs its capital back after the joins; the words and their
        # order are untouched, only the first letter changes.
        sentences[0] = capitalise(sentences[0]) if sentences else ""
        text = " ".join(s for s in sentences if s)
        if not text:
            continue
        blocks.append(
            {
                "label": group[0][3],
                "ordinal": 0,
                "promptId": "",
                "start_ms": min(r[0] for r in group),
                "end_ms": max(r[1] for r in group),
                "text": text,
            }
        )

    blocks.sort(key=lambda block: (block["start_ms"], block["end_ms"], block["label"]))
    return blocks


def readable_text(blocks: Optional[Iterable[dict]]) -> str:
    """The same blocks as plain text, for a screen or a file.

    This is a presentation string. It is never written back over the raw
    segments, and `Open transcript` shows this instead of `transcript.md`.
    """
    from diloger.transcription.formatter import format_range

    out: list[str] = []
    for block in blocks or []:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        out.append(str(block.get("label") or ""))
        out.append(format_range(int(block.get("start_ms") or 0), int(block.get("end_ms") or 0)))
        out.append(text)
        out.append("")
    return "\n".join(out).strip()


def rebuild_readable(
    transcript: dict,
    prompt_texts: Optional[dict] = None,
    prompt_order: Optional[Iterable[Any]] = None,
) -> list[dict]:
    """Recompute the readable view from stored raw data only.

    This is what lets an old session pick up a new formatter: the raw segments
    and the event log are both already on disk, so Whisper never has to run a
    second time to improve how the words read.
    """
    return build_readable_blocks(
        transcript.get("segments") or [],
        event_log=transcript.get("eventLog") or [],
        prompt_texts=prompt_texts or {},
        prompt_order=prompt_order or transcript.get("promptOrder") or [],
    )