"""Session storage, JSON payload, Markdown export, retention policy."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from diloger.transcription.formatter import NOTICE, transcript_body
from diloger.transcription.readable import (
    READABLE_VERSION,
    NOTICE as NOTICE_READABLE,
    build_readable_blocks,
    prompt_texts_for_session,
    readable_text,
    rebuild_readable,
)

TEMP_PREFIX = "diloger-recording"


def _epoch_from_session_field(value) -> Optional[float]:
    """Accept a numeric epoch or an ISO 8601 UTC string from session.json."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.endswith("Z"):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.timestamp()
        return parsed.replace(tzinfo=timezone.utc).timestamp()
    return None


def default_sessions_dir() -> Path:
    return Path.home() / "Documents" / "Diloger" / "sessions"


def temp_audio_path(session_id: str = "") -> Path:
    """Temporary WAV path for one session.

    A session id keeps two sessions from fighting over the same file, which
    would let a later session overwrite (and then delete) an earlier
    recording that has not been transcribed yet.
    """
    suffix = f"-{session_id}" if session_id else ""
    return Path(tempfile.gettempdir()) / f"{TEMP_PREFIX}{suffix}.wav"


@dataclass
class ClearAllResult:
    """Outcome of a bulk history wipe.

    `removed` alone would hide a folder that could not be deleted, so the
    failures travel with the count instead of being swallowed.
    """

    total: int = 0
    removed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def remaining(self) -> int:
        return self.total - self.removed


class SessionStorage:
    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self.sessions_dir = Path(sessions_dir) if sessions_dir else default_sessions_dir()

    def create_session_dir(self, started_at: float) -> Path:
        stamp = datetime.fromtimestamp(started_at).strftime("%Y-%m-%d_%H-%M-%S")
        target = self.sessions_dir / f"{stamp}_{os.urandom(2).hex()}"
        target.mkdir(parents=True, exist_ok=False)
        return target

    def temp_audio_path(self, session_id: str = "") -> Path:
        return temp_audio_path(session_id)

    def write_session_json(self, session_dir: Path, payload: dict) -> Path:
        return self._write_json(session_dir / "session.json", payload)

    def write_transcript_json(self, session_dir: Path, payload: dict) -> Path:
        return self._write_json(session_dir / "transcript.json", payload)

    def write_raw_whisper_json(self, session_dir: Path, payload: str) -> Path:
        """Store whisper-cli's own JSON exactly as it produced it.

        The bytes are copied verbatim: this file exists so the transcription can
        be re-processed or checked later, and re-serialising it would quietly
        change what the user asked to keep.
        """
        path = Path(session_dir) / "whisper.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return path

    def read_raw_whisper_json(self, session_dir: Path) -> str:
        path = Path(session_dir) / "whisper.json"
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def read_transcript_json(self, session_dir: Path) -> dict:
        """Stored segments for a session, or {} when there is none."""
        path = Path(session_dir) / "transcript.json"
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def read_session_json(self, session_dir: Path) -> dict:
        """The session payload as stored, or {} when it is missing or broken."""
        path = Path(session_dir) / "session.json"
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write_json(self, path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_transcript_markdown(self, session_dir: Path, content: str) -> Path:
        path = session_dir / "transcript.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def orphan_temp_audio(self) -> list[Path]:
        """Recording files left behind by a crash or app termination.

        Both the final WAV and the raw stream a session was still writing are
        reported: an interrupted recording leaves the raw file behind, and it
        has to be visible in Settings so the user can reclaim the space.
        """
        tmp = Path(tempfile.gettempdir())
        return sorted(tmp.glob(f"{TEMP_PREFIX}*.wav")) + sorted(
            tmp.glob(f"{TEMP_PREFIX}*.pcm")
        )

    def delete_orphan_temp_audio(self) -> tuple[int, list[str]]:
        """Delete orphan temporary WAVs, reporting every failure.

        Only the temp files this app names are touched. Retained recordings live
        inside their session folder and user content is never in the temp
        directory, so neither is reachable from here.
        """
        removed = 0
        failures: list[str] = []
        for path in self.orphan_temp_audio():
            if self.delete(path):
                removed += 1
            else:
                failures.append(f"{path.name}: could not be deleted")
        return removed, failures

    # ---- history --------------------------------------------------------

    RECORDING_NAMES = ("recording.wav",)

    def recording_path(self, session_dir: Path) -> Optional[Path]:
        for name in self.RECORDING_NAMES:
            candidate = Path(session_dir) / name
            if candidate.is_file():
                return candidate
        return None

    def list_sessions(self) -> list[dict]:
        """Metadata for every stored session, newest first.

        A session folder without a readable session.json is still listed, so a
        damaged folder never silently disappears from the user's history.
        """
        if not self.sessions_dir.is_dir():
            return []
        entries: list[dict] = []
        for folder in self.sessions_dir.iterdir():
            if not folder.is_dir():
                continue
            payload: dict = {}
            json_path = folder / "session.json"
            if json_path.is_file():
                try:
                    loaded = json.loads(json_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        payload = loaded
                except (OSError, ValueError):
                    payload = {}
            recording = self.recording_path(folder)
            transcript_md = folder / "transcript.md"
            transcript_json = folder / "transcript.json"
            started_epoch = _epoch_from_session_field(
                payload.get("startedAtEpoch")
            ) or _epoch_from_session_field(payload.get("startedAt"))
            duration = payload.get("durationSeconds")
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                duration = 0.0
            entries.append(
                {
                    "id": folder.name,
                    "path": str(folder),
                    "date": (
                        datetime.fromtimestamp(started_epoch).strftime("%Y-%m-%d %H:%M:%S")
                        if started_epoch
                        else folder.name.split("_")[0]
                    ),
                    "startedAtEpoch": started_epoch or 0.0,
                    "source": Path(str(payload.get("contentSourcePath") or "")).name
                    or "unknown source",
                    "sourcePath": str(payload.get("contentSourcePath") or ""),
                    "durationSeconds": duration,
                    "promptsReached": int(payload.get("promptsReached") or 0),
                    "totalPrompts": int(payload.get("totalPrompts") or 0),
                    "mode": str(payload.get("mode") or ""),
                    "transcriptionStatus": str(
                        payload.get("transcriptionStatus") or "notRequested"
                    ),
                    "hasTranscript": bool(
                        payload.get("transcriptJsonPath")
                        or (transcript_json.is_file() or transcript_md.is_file())
                    ),
                    "hasRecording": recording is not None,
                    "recordingPath": str(recording) if recording else "",
                    "hasSessionJson": json_path.is_file(),
                }
            )
        entries.sort(key=lambda e: (e["startedAtEpoch"], e["id"]), reverse=True)
        return entries

    def read_transcript_markdown(self, session_dir: Path, max_chars: int = 40000) -> str:
        """Objective transcript text for display. Never modifies the content."""
        path = Path(session_dir) / "transcript.md"
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")[:max_chars]
        except OSError:
            return ""

    def delete_session(self, session_id: str) -> bool:
        """Remove one session folder. `session_id` must be a folder name.

        Rejects anything that is not a direct child of the sessions directory,
        so a crafted id cannot delete files outside History.
        """
        name = str(session_id or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return False
        target = (self.sessions_dir / name).resolve()
        root = self.sessions_dir.resolve()
        if target.parent != root or not target.is_dir():
            return False
        return self.delete(target)

    def clear_all_sessions(self) -> ClearAllResult:
        """Remove every session folder in History, one folder at a time.

        The same containment rule as `delete_session` decides what may be
        touched: only real directories that are direct children of the sessions
        directory. The sessions directory itself, loose files inside it,
        symbolic links (which could point anywhere) and anything outside History
        are never removed. Each folder is deleted independently so one failure
        cannot hide the rest, and every failure is reported by name.
        """
        result = ClearAllResult()
        if not self.sessions_dir.is_dir():
            return result
        root = self.sessions_dir.resolve()
        for child in sorted(self.sessions_dir.iterdir()):
            if child.is_symlink():
                result.skipped += 1
                result.failures.append(f"{child.name}: symbolic link, not deleted")
                continue
            if not child.is_dir():
                # A loose file in the sessions folder is not a session; History
                # does not list it and this operation does not own it.
                continue
            if child.resolve().parent != root:
                result.skipped += 1
                result.failures.append(f"{child.name}: outside the sessions folder, not deleted")
                continue
            result.total += 1
            try:
                shutil.rmtree(child)
            except OSError as exc:
                result.failures.append(f"{child.name}: {exc.strerror or exc}")
                continue
            result.removed += 1
        return result

    def delete(self, path: Path) -> bool:
        path = Path(path)
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return True
        except OSError:
            return False

    def has_space(self, required_bytes: int = 50 * 1024 * 1024) -> bool:
        try:
            usage = shutil.disk_usage(self.sessions_dir if self.sessions_dir.exists() else Path(tempfile.gettempdir()))
            return usage.free > required_bytes
        except OSError:
            return False


def readable_for(
    session: dict,
    segments: list[dict],
    event_log=None,
    search_dirs=None,
) -> list[dict]:
    """The derived readable view for a session, built from raw data only."""
    return build_readable_blocks(
        segments or [],
        event_log=event_log if event_log is not None else (session.get("eventLog") or []),
        prompt_texts=prompt_texts_for_session(session, search_dirs),
        prompt_order=session.get("promptOrder") or [],
    )


def transcript_payload(
    session: dict,
    segments: list[dict],
    *,
    model: str = "",
    event_log=None,
    search_dirs=None,
) -> dict:
    """transcript.json with three representations kept apart.

    `segments` is Whisper's own output and is written exactly as received. The
    readable view sits in its own fields next to it, with the formatter version
    that produced it, so a later change to the formatting rules can be applied
    to every old session from this file alone -- no second Whisper run.
    """
    blocks = readable_for(session, segments, event_log, search_dirs)
    return {
        "model": model or session.get("whisperModelPath", ""),
        "segments": segments,
        "readableVersion": READABLE_VERSION,
        "readableSegments": blocks,
        "eventLog": list(event_log if event_log is not None else (session.get("eventLog") or [])),
    }


def _read_json(path: Path) -> dict:
    """One JSON object, or {} when it is missing, unreadable or not an object.

    A broken file must not take down the screen that is trying to show the
    session; an absent transcript is not an error, it just has nothing in it.
    """
    try:
        if not path.is_file():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def stored_readable_blocks(
    folder: Path,
    session: Optional[dict] = None,
    search_dirs=None,
) -> list[dict]:
    """Readable blocks for a stored session, rebuilt if the formatter moved on."""
    stored = _read_json(Path(folder) / "transcript.json")
    meta = session if session is not None else _read_json(Path(folder) / "session.json")
    if stored.get("readableVersion") == READABLE_VERSION and stored.get("readableSegments"):
        return list(stored["readableSegments"])
    return rebuild_readable(
        {"segments": stored.get("segments") or [], "eventLog": stored.get("eventLog") or [],
         "promptOrder": meta.get("promptOrder") or []},
        prompt_texts=prompt_texts_for_session(meta, search_dirs),
        prompt_order=meta.get("promptOrder") or [],
    )


def build_transcript_markdown(
    session: dict,
    segments: list[dict],
    typed_answers: Optional[dict] = None,
    prompt_texts: Optional[dict] = None,
    *,
    recording_available: Optional[bool] = None,
    transcript_generated: bool = True,
    readable_blocks: Optional[list] = None,
) -> str:
    """Objective transcript export. No grammar fixes, no summary, no labels.

    Every segment keeps the range Whisper gave it, so the timing survives the
    trip to a text file instead of being collapsed into one paragraph. A
    missing recording or a missing transcript is stated as a fact and does not
    stop the file from being written.
    """
    started_epoch = _epoch_from_session_field(session.get("startedAtEpoch"))
    if started_epoch is None:
        started_epoch = _epoch_from_session_field(session.get("startedAt"))
    if started_epoch is None:
        started_epoch = time.time()

    ended_epoch = _epoch_from_session_field(session.get("endedAtEpoch"))
    if ended_epoch is None:
        ended_epoch = _epoch_from_session_field(session.get("endedAt"))

    started_dt = datetime.fromtimestamp(started_epoch)

    if recording_available is None:
        recording_available = bool(session.get("recordingPath"))
    duration = session.get("durationSeconds")
    try:
        duration = int(round(float(duration)))
    except (TypeError, ValueError):
        duration = None
    if duration is None and ended_epoch is not None and started_epoch:
        duration = int(round(ended_epoch - started_epoch))

    total_prompts = int(session.get("totalPrompts") or 0)
    reached = int(session.get("promptsReached") or 0)
    reached_line = f"{reached} / {total_prompts}" if total_prompts else str(reached)
    if recording_available:
        recording_line = (
            "kept in the session folder"
            if session.get("recordingRetained")
            else "not retained"
        )
    else:
        recording_line = "not available"

    lines = [
        "# Dialogue Trainer Session",
        "",
        f"- Date: {started_dt.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Source: {Path(session.get('contentSourcePath', '')).name or 'unknown source'}",
        f"- Mode: {session.get('mode') or 'n/a'} / "
        f"{session.get('spokenProgression') or session.get('writtenPrompt') or 'n/a'}",
        f"- Duration: {duration} seconds" if duration is not None else "- Duration: unknown",
        f"- Prompts reached: {reached_line}",
        f"- Recording: {recording_line}",
        f"- Whisper model: {Path(session.get('whisperModelPath') or '').name or 'n/a'}",
        "",
        "## Transcript",
        "",
        # An exported file travels on its own, so it has to carry the same
        # caveat the app shows: nobody corrected this text.
        NOTICE_READABLE,
        "",
        readable_text(readable_blocks) if readable_blocks is not None
        else transcript_body(segments, generated=transcript_generated),
        "",
    ]

    if prompt_texts:
        lines.append("## Prompts and typed answers")
        lines.append("")
        for pid, text in prompt_texts.items():
            ordinal = _prompt_ordinal(pid)
            lines.append(f"### Prompt {ordinal}")
            lines.append("")
            lines.append(text)
            answer = (typed_answers or {}).get(pid)
            if answer:
                lines.append("")
                lines.append("#### Typed answer")
                lines.append("")
                lines.append(answer)
            lines.append("")

    return "\n".join(lines)


def _prompt_ordinal(prompt_id: str) -> int:
    """Human position of a prompt id, counting from 1."""
    try:
        return int(str(prompt_id).split(":", 1)[0]) + 1
    except (TypeError, ValueError):
        return 1
