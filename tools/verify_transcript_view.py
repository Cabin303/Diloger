"""Check what the transcript actually shows on screen, not what the code says.

The unit tests can read the formatter; only this probe can prove that a user
opens Finished or History and sees a readable transcript rather than an export
document. It builds the real ApplicationWindow, gives a stored session real
Whisper segments, a real event log and a real content file, then reads the
rendered text back out of the live QML tree.

What it proves, per screen:

* blocks appear at all, one per real piece of speech;
* no `#`, `##`, `###`, `- Date:` or other Markdown syntax reaches the screen;
* every block carries a `HH:MM:SS.mmm` timecode;
* a sentence starts with a capital letter;
* the question is the text from the user's content file;
* a segment containing both voices is not labelled as the user's answer.

    .venv/bin/python tools/verify_transcript_view.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from diloger.app.controller import AppController  # noqa: E402
from diloger.app.main import QML_DIR, _install_palette  # noqa: E402

failures: list[str] = []

# Anything that means the export document was shown as if it were the transcript.
MARKDOWN_LEAKS = (
    "# Dialogue Trainer Session",
    "- Date:",
    "- Source:",
    "## Transcript",
    "## Prompts and typed answers",
    "### Prompt",
    "#### Typed answer",
)
TIME_RANGE = re.compile(r"\[\d{2}:\d{2}:\d{2}\.\d{3}\s–\s\d{2}:\d{2}:\d{2}\.\d{3}\]")
WORDS = re.compile(r"[A-Za-z][A-Za-z']*")

SESSION_ID = "2026-10-03_09-00-00_probe"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def build(tmp: Path) -> tuple[AppController, QQuickItem, QApplication, QQmlApplicationEngine, list[str]]:
    """A stored session that mirrors the real one on disk, prompts included."""
    sessions = tmp / "sessions"
    library = tmp / "library"
    sessions.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)

    source = library / "Probe Day.txt"
    source.write_text(
        "What did you do during your last contract?\n"
        "How long have you been at sea?\n",
        encoding="utf-8",
    )

    folder = sessions / SESSION_ID
    folder.mkdir(parents=True, exist_ok=True)

    # Prompt ids are `<line ordinal>:<sha1 of the line>`; these are the real
    # ids for the two lines above, so the content lookup is really exercised.
    session = {
        "sessionId": SESSION_ID,
        "startedAt": "2026-10-03T09:00:00Z",
        "endedAt": "2026-10-03T09:00:20Z",
        "startedAtEpoch": 1792000000.0,
        "endedAtEpoch": 1792000020.0,
        "contentSourcePath": str(source),
        "durationSeconds": 20.0,
        "promptsReached": 2,
        "totalPrompts": 2,
        "mode": "spoken",
        "transcriptionStatus": "succeeded",
        "recordingRetained": True,
        "promptOrder": ["1:118a7b5f2ccc", "0:0665536828fe"],
        "typedAnswers": {},
        "eventLog": [
            {"type": "sessionStart", "atOffset": 0.0},
            {"type": "promptStart", "atOffset": 0.0, "promptId": "0:0665536828fe"},
            {"type": "ttsStart", "atOffset": 0.0, "promptId": "0:0665536828fe"},
            {"type": "ttsFinish", "atOffset": 4.5, "promptId": "0:0665536828fe"},
            {"type": "promptStart", "atOffset": 5.0, "promptId": "1:118a7b5f2ccc"},
            {"type": "ttsStart", "atOffset": 5.0, "promptId": "1:118a7b5f2ccc"},
            {"type": "ttsFinish", "atOffset": 8.0, "promptId": "1:118a7b5f2ccc"},
            {"type": "next", "atOffset": 8.0, "promptId": "1:118a7b5f2ccc"},
        ],
    }
    (folder / "session.json").write_text(json.dumps(session), encoding="utf-8")

    # Four segments covering every attribution the formatter has to get right:
    # a clean trainer echo, a segment holding both voices, a plain answer with a
    # real transcription error, and a second trainer echo.
    transcript = {
        "model": "ggml-large-v3-turbo.bin",
        "segments": [
            # entirely inside the first prompt's playback -> Question
            {"start_ms": 200, "end_ms": 4000,
             "text": "what did you do during your last contract"},
            # inside the first prompt, runs on past it -> both voices, honest label
            {"start_ms": 3000, "end_ms": 10000,
             "text": "i mostly worked on deck and the vessel stayed in port"},
            # after every playback window -> the user's answer, typo included
            {"start_ms": 8500, "end_ms": 12000,
             "text": "in plant dry dock for one and a half months"},
            # inside the second prompt's playback -> Question
            {"start_ms": 5100, "end_ms": 7900,
             "text": "how long have you been at sea"},
        ],
        "eventLog": session["eventLog"],
    }
    (folder / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    # The export document. If any of this reaches the screen, the screen is
    # reading the export file instead of the view model.
    (folder / "transcript.md").write_text(
        "# Dialogue Trainer Session\n\n"
        "- Date: 2026-10-03 09:00:00\n"
        "- Source: Probe Day.txt\n\n"
        "## Transcript\n\n"
        "[00:00:00.000 – 00:00:06.000] what did you do\n\n"
        "## Prompts and typed answers\n\n"
        "### Prompt 1\n\nWhat did you do during your last contract?\n",
        encoding="utf-8",
    )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Diloger")
    app.setOrganizationName("Diloger")
    QQuickStyle.setStyle("Basic")
    font = QFont()
    font.setPointSize(15)
    app.setFont(font)

    config = tmp / "config.json"
    config.write_text(json.dumps({"libraryPath": str(library), "sessionsPath": str(sessions)}),
                      encoding="utf-8")

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR.parent))
    controller = AppController(config_path=str(config))
    controller.setParent(engine)
    controller.theme.setParent(engine)
    controller.player.setParent(engine)
    ctx = engine.rootContext()
    ctx.setContextProperty("app", controller)
    ctx.setContextProperty("theme", controller.theme)
    ctx.setContextProperty("player", controller.player)

    problems: list[str] = []
    engine.warnings.connect(lambda ws: problems.extend(w.toString() for w in ws))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    _install_palette(controller.theme, engine)
    spin_for(0.6)
    if not engine.rootObjects():
        problems.append("Main.qml did not load")
    return controller, engine.rootObjects()[0], app, engine, problems


def spin_for(seconds: float) -> None:
    app = QApplication.instance()
    end = __import__("time").monotonic() + seconds
    while __import__("time").monotonic() < end:
        app.processEvents()
        __import__("time").sleep(0.01)


def walk(item: QQuickItem):
    yield item
    for node in item.childItems():
        if isinstance(node, QQuickItem):
            yield from walk(node)


def find_items(window, object_name: str) -> list[QQuickItem]:
    """Resolve contentItem() now, not earlier: navigation deletes the old tree."""
    return [i for i in walk(window.contentItem()) if i.objectName() == object_name]


def _as_blocks(value) -> list[dict]:
    """QML hands back a QJSValue; make it a plain python list of dicts."""
    if value is None:
        return []
    variant = value.toVariant() if hasattr(value, "toVariant") else value
    if not isinstance(variant, list):
        return []
    return [dict(v) for v in variant if isinstance(v, dict)]


def rendered_text(view: QQuickItem) -> str:
    """Everything a user could read on the transcript view, joined."""
    parts: list[str] = []
    for item in walk(view):
        if not item.isVisible():
            continue
        value = item.property("text")
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(parts)


def audit(label: str, view: QQuickItem, blocks: list[dict]) -> None:
    text = rendered_text(view)
    check(f"{label}: the transcript view rendered", text.strip() != "", f"{len(text)} chars")

    leaked = [marker for marker in MARKDOWN_LEAKS if marker in text]
    check(f"{label}: no Markdown syntax on screen", not leaked, f"leaked {leaked}" if leaked else "clean")

    check(f"{label}: blocks reached the screen", len(blocks) > 0, f"{len(blocks)} blocks")

    ranged = TIME_RANGE.findall(text)
    check(f"{label}: every block has a HH:MM:SS.mmm timecode",
          len(ranged) == len(blocks), f"{len(ranged)} ranges / {len(blocks)} blocks")

    # A readable sentence starts with a capital. The label line and the
    # timecode are structure; only the block text is prose.
    body = [b["text"] for b in blocks if b.get("text")]
    uncapped = [t for t in body if t[:1].isalpha() and not t[:1].isupper()]
    check(f"{label}: every sentence starts with a capital", not uncapped,
          f"lowercase: {uncapped}" if uncapped else "all capitalised")

    labels = [b["label"] for b in blocks]
    check(f"{label}: the question comes from the content file",
          any("What did you do during your last contract?" == b["text"] for b in blocks),
          f"labels {labels}")

    # The first segment straddles the trainer's playback, so it must not be
    # presented as the user's answer.
    straddler = next((b for b in blocks if "vessel stayed" in (b.get("text") or "")), None)
    check(f"{label}: a segment holding both voices is not called an answer",
          straddler is not None and straddler["label"] != "Recorded answer",
          f"{straddler['label']!r}" if straddler else "segment not found")

    # The typo is the recording's, not the app's: correcting it would be editing
    # what was actually said.
    # Case-insensitive: the formatter capitalises the first letter, and that is
    # presentation. "plant" itself must survive -- "port" would be the app
    # rewriting what was actually said.
    typo = [b for b in blocks if "in plant dry dock" in (b.get("text") or "").lower()]
    check(f"{label}: the transcribed wording is not replaced",
          not any("port dry dock" in (b.get("text") or "").lower() for b in blocks),
          "no silent correction")
    check(f"{label}: spelling is left exactly as transcribed", bool(typo), "kept as transcribed")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="diloger-transcript-"))
    try:
        controller, window, app, engine, problems = build(tmp)
        root = window

        print("\n=== History ===")
        controller.showHistory()
        spin_for(0.3)
        screen = find_items(root, "historyScreen")[0]
        screen.setProperty("selectedId", SESSION_ID)
        spin_for(0.4)
        check("history: a session can be selected", screen.property("selectedId") == SESSION_ID)
        screen.openTranscript()
        spin_for(0.4)
        views = find_items(root, "historyTranscriptView")
        check("history: the transcript view is on screen", bool(views))
        if views:
            audit("history", views[0], _as_blocks(views[0].property("blocks")))

        print("\n=== Finished ===")
        # The Finished screen reads a view model, so it can be driven directly
        # with exactly the blocks the controller would hand it. No TTS, no audio.
        blocks = _as_blocks(find_items(root, "historyTranscriptView")[0].property("blocks"))
        controller.showFinished()
        spin_for(0.3)
        finished = find_items(root, "finishedScreen")[0]
        info = dict(finished.property("info"))
        info.update({
            "transcriptBlocks": blocks,
            "transcriptNotice": "Local Whisper output with timestamps.",
            "hasTranscript": True,
            "transcriptionStatus": "succeeded",
        })
        finished.setProperty("info", info)
        spin_for(0.5)
        fviews = find_items(root, "finishedTranscriptView")
        check("finished: the transcript view is on screen", bool(fviews))
        if fviews:
            fblocks = _as_blocks(fviews[0].property("blocks"))
            audit("finished", fviews[0], fblocks)
            check("finished: renders the same blocks as history",
                  [b.get("text") for b in fblocks] == [b.get("text") for b in blocks],
                  f"{len(fblocks)} blocks")

        print("\n=== Markdown that must stay out of the screen ===")
        exported = (tmp / "sessions" / SESSION_ID / "transcript.md").read_text(encoding="utf-8")
        on_screen = rendered_text(find_items(root, "historyTranscriptView")[0])
        check("history: the export document is still on disk unchanged",
              "# Dialogue Trainer Session" in exported and "- Date:" in exported,
              "export file intact")
        check("history: none of that document is on screen",
              not any(marker in on_screen for marker in MARKDOWN_LEAKS))

        print("\n=== QML runtime ===")
        runtime = [p for p in problems
                   if "TypeError" in p or "ReferenceError" in p or "is not a" in p
                   or "Unable to assign" in p or "Binding loop" in p]
        check("no QML runtime errors", not runtime, "; ".join(runtime) if runtime else "clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("Both screens show a readable transcript, not an export document.")
    return 0


if __name__ == "__main__":
    sys.exit(main())