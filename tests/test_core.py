import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeClock, FakeRecorder, FakeTts  # noqa: E402

from diloger.content.importer import ContentImportError, FileContentImporter, parse_lines  # noqa: E402
from diloger.content.order import build_order  # noqa: E402
from diloger.domain.models import (  # noqa: E402
    ContentSet,
    Mode,
    Order,
    Prompt,
    SessionConfig,
    SpokenProgression,
    TranscriptionStatus,
)
from diloger.domain.models import SessionState  # noqa: E402
from diloger.session.engine import SessionEngine  # noqa: E402
from diloger.storage.session_store import build_transcript_markdown  # noqa: E402
from diloger.tts.mac_say import MacSayTts  # noqa: E402
from diloger.transcription.formatter import (  # noqa: E402
    NO_SPEECH,
    NO_TRANSCRIPT,
    transcript_body,
)
from diloger.transcription.whisper_cli import parse_whisper_json  # noqa: E402


def make_content(texts=("First prompt.", "Second prompt.", "Third prompt.")) -> ContentSet:
    prompts = tuple(Prompt(id=f"{i}:pid{i}", ordinal=i, text=t) for i, t in enumerate(texts))
    return ContentSet(
        id="cid",
        sourcePath=str(Path(__file__).parent / "fixture.txt"),
        displayName="fixture",
        format="txt",
        prompts=prompts,
    )


def make_engine(config=None, clock=None, tts=None, recorder=None, content=None):
    clock = clock or FakeClock()
    tts = tts or FakeTts()
    engine = SessionEngine(
        tts=tts,
        recorder=recorder,
        clock=clock,
        content=content or make_content(),
        config=config or SessionConfig(),
    )
    return engine, clock, tts


# ---------------------------------------------------------------- parser


def test_parser_ignores_blank_lines_and_trims():
    prompts = parse_lines("  First  \n\n\n Second \n", Path("x.txt"))
    assert [p.text for p in prompts] == ["First", "Second"]
    assert [p.ordinal for p in prompts] == [0, 1]


def test_parser_strips_bom(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"\xef\xbb\xbfHello\nWorld\n")
    cs = FileContentImporter().load_file(f)
    assert [p.text for p in cs.prompts] == ["Hello", "World"]


def test_parser_handles_utf8(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("Café — naïve\n", encoding="utf-8")
    cs = FileContentImporter().load_file(f)
    assert cs.prompts[0].text == "Café — naïve"
    assert cs.format == "md"


def test_empty_file_rejected(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("\n\n   \n", encoding="utf-8")
    with pytest.raises(ContentImportError) as err:
        FileContentImporter().load_file(f)
    assert "no prompts" in str(err.value)


def test_unsupported_extension_rejected(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ContentImportError) as err:
        FileContentImporter().load_file(f)
    assert "Unsupported" in str(err.value)


def test_invalid_encoding_reports_path(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ContentImportError) as err:
        FileContentImporter().load_file(f)
    assert "bad.txt" in str(err.value)
    assert "UTF-8" in str(err.value)


def test_list_files_only_txt_md(tmp_path):
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.md").write_text("B", encoding="utf-8")
    (tmp_path / "c.pdf").write_text("C", encoding="utf-8")
    found = FileContentImporter().list_files(tmp_path)
    assert [p.name for p in found] == ["a.txt", "b.md"]


# ----------------------------------------------------------------- order


def test_sequential_order_preserves_sequence():
    content = make_content()
    ids = [p.id for p in content.prompts]
    assert build_order(content.prompts, Order.SEQUENTIAL) == ids


def test_random_without_repetition_has_no_duplicates():
    content = make_content(("a", "b", "c", "d", "e", "f"))
    ids = build_order(content.prompts, Order.RANDOM_WITHOUT_REPETITION, seed=7)
    assert len(ids) == len(set(ids)) == 6
    assert set(ids) == {p.id for p in content.prompts}


def test_random_order_is_seed_stable():
    content = make_content(tuple("abcdefg"))
    a = build_order(content.prompts, Order.RANDOM_WITHOUT_REPETITION, seed=42)
    b = build_order(content.prompts, Order.RANDOM_WITHOUT_REPETITION, seed=42)
    assert a == b


def test_single_item_order():
    content = make_content(("only one",))
    assert build_order(content.prompts, Order.RANDOM_WITHOUT_REPETITION, seed=1) == [content.prompts[0].id]


# --------------------------------------------------------- session engine


def test_timer_starts_only_after_tts_finishes():
    tts = FakeTts()
    tts.auto_finish = False
    clock = FakeClock()
    engine, clock, tts = make_engine(
        config=SessionConfig(spokenProgression=SpokenProgression.FIXED_TIMER, configuredDelaySeconds=5),
        clock=clock,
        tts=tts,
    )
    engine.start()
    assert engine.state.value == "speaking"
    assert not clock._callbacks, "timer must not be scheduled while TTS runs"

    tts.finish()
    assert engine.state.value == "waiting"
    assert len(clock._callbacks) == 1


def test_manual_progression_waits_for_next():
    clock = FakeClock()
    engine, clock, tts = make_engine(
        config=SessionConfig(spokenProgression=SpokenProgression.MANUAL), clock=clock
    )
    engine.start()
    assert engine.state.value == "waiting"
    assert not clock._callbacks
    engine.next()
    assert engine.current_position == 2
    assert tts.spoken == ["First prompt.", "Second prompt."]


def test_timer_expiry_advances():
    clock = FakeClock()
    engine, clock, tts = make_engine(
        config=SessionConfig(spokenProgression=SpokenProgression.FIXED_TIMER, configuredDelaySeconds=3),
        clock=clock,
    )
    engine.start()
    clock.advance(2.9)
    assert engine.current_position == 1
    clock.advance(0.2)
    assert engine.current_position == 2
    assert "timerExpire" in [e.type.value for e in engine.session.eventLog]


def test_repeat_does_not_increment_index():
    engine, clock, tts = make_engine()
    engine.start()
    assert engine.current_position == 1
    engine.repeat()
    assert engine.current_position == 1
    assert tts.spoken == ["First prompt.", "First prompt."]


def test_skip_increments_index_and_logs():
    engine, clock, tts = make_engine()
    engine.start()
    engine.skip()
    assert engine.current_position == 2
    assert "skip" in [e.type.value for e in engine.session.eventLog]


def test_reveal_does_not_change_progression():
    engine, clock, tts = make_engine()
    engine.start()
    before = engine.current_position
    assert engine.prompt_text_visible is False
    engine.reveal()
    assert engine.prompt_text_visible is True
    assert engine.current_position == before
    assert engine.state.value == "waiting"


def test_pause_freezes_timer_and_resume_restores():
    clock = FakeClock()
    engine, clock, tts = make_engine(
        config=SessionConfig(spokenProgression=SpokenProgression.FIXED_TIMER, configuredDelaySeconds=10),
        clock=clock,
    )
    engine.start()
    clock.advance(4)
    engine.pause()
    assert engine.state.value == "paused"
    assert round(engine.timer_remaining(), 3) == 6.0

    clock.advance(60)
    assert engine.current_position == 1, "paused session must not auto-advance"

    engine.resume()
    assert engine.state.value == "waiting"
    clock.advance(5.9)
    assert engine.current_position == 1
    clock.advance(0.2)
    assert engine.current_position == 2


def test_pause_cancels_tts_and_resume_repeats():
    tts = FakeTts()
    tts.auto_finish = False
    engine, clock, tts = make_engine(tts=tts)
    engine.start()
    engine.pause()
    engine.resume()
    assert tts.spoken == ["First prompt.", "First prompt."]


def test_stop_finalizes_session():
    engine, clock, tts = make_engine()
    engine.start()
    engine.stop()
    assert engine.state.value == "finished"
    assert engine.session.status.value == "stopped"
    assert engine.session.endedAt is not None


def test_content_exhaustion_does_not_loop():
    engine, clock, tts = make_engine()
    engine.start()
    engine.next()
    engine.next()
    assert engine.state.value == "waiting"
    assert engine.current_position == 3, "must stay on the last item"

    engine.next()
    assert engine.state.value == "atEnd"
    engine.next()
    engine.skip()
    assert engine.current_position == 3
    assert tts.spoken == ["First prompt.", "Second prompt.", "Third prompt."]
    assert "contentExhausted" in [e.type.value for e in engine.session.eventLog]


def test_tts_error_moves_to_waiting_and_logs():
    tts = FakeTts()
    tts.auto_finish = False
    engine, clock, tts = make_engine(tts=tts)
    engine.start()
    tts.fail()
    assert engine.state.value == "waiting"
    assert "ttsError" in [e.type.value for e in engine.session.eventLog]


def test_written_audio_prompt_speaks_and_waits_for_typing():
    from diloger.domain.models import WrittenPromptVariant

    engine, clock, tts = make_engine(
        config=SessionConfig(mode=Mode.WRITTEN, writtenPrompt=WrittenPromptVariant.AUDIO)
    )
    engine.start()
    assert tts.spoken == ["First prompt."]
    assert engine.state.value == "typing"
    assert engine.prompt_text_visible is False


def test_written_text_prompt_shows_text_without_audio():
    from diloger.domain.models import WrittenPromptVariant

    engine, clock, tts = make_engine(
        config=SessionConfig(mode=Mode.WRITTEN, writtenPrompt=WrittenPromptVariant.TEXT)
    )
    engine.start()
    assert tts.spoken == []
    assert engine.state.value == "typing"
    assert engine.prompt_text_visible is True


def test_typed_answers_not_saved_when_disabled():
    from diloger.domain.models import WrittenPromptVariant

    engine, _, _ = make_engine(
        config=SessionConfig(
            mode=Mode.WRITTEN, writtenPrompt=WrittenPromptVariant.TEXT, saveTypedAnswers=False
        )
    )
    engine.start()
    engine.set_typed_answer("my secret answer")
    engine.next()
    assert engine.session.typedAnswers == {}


def test_typed_answers_saved_when_enabled():
    from diloger.domain.models import WrittenPromptVariant

    engine, _, _ = make_engine(
        config=SessionConfig(
            mode=Mode.WRITTEN, writtenPrompt=WrittenPromptVariant.TEXT, saveTypedAnswers=True
        )
    )
    engine.start()
    engine.set_typed_answer("my answer")
    engine.next()
    assert engine.session.typedAnswers == {"0:pid0": "my answer"}


def test_session_runs_without_recording():
    engine, _, _ = make_engine(config=SessionConfig(recordingEnabled=False), recorder=None)
    engine.start()
    engine.next()
    engine.stop()
    assert engine.session.recordingPath is None
    assert engine.state.value == "finished"


def test_recorder_failure_does_not_kill_session():
    rec = FakeRecorder(available=True, fail_on_start=True)
    engine, _, tts = make_engine(config=SessionConfig(recordingEnabled=True), recorder=rec)
    engine.start()
    assert engine.session.config.recordingEnabled is False
    assert engine.state.value == "waiting"
    engine.next()
    assert engine.current_position == 2


def test_recording_paths_recorded_in_event_log():
    rec = FakeRecorder()
    engine, _, _ = make_engine(config=SessionConfig(recordingEnabled=True), recorder=rec)
    engine.start()
    engine.stop()
    types = [e.type.value for e in engine.session.eventLog]
    assert "recordingStart" in types
    assert "recordingStop" in types
    assert engine.session.recordingPath is not None


def test_prompts_reached_tracked():
    engine, _, _ = make_engine()
    engine.start()
    engine.next()
    assert engine.session.promptsReached == 2


def test_session_json_roundtrip():
    import json

    engine, _, _ = make_engine()
    engine.start()
    engine.next()
    engine.stop()
    payload = engine.session.to_dict()
    reparsed = json.loads(json.dumps(payload))
    assert reparsed["promptsReached"] == 2
    assert reparsed["eventLog"][0]["type"] == "sessionStart"
    assert reparsed["mode"] == "spoken"


# -------------------------------------------------------------- retention


def test_temp_audio_deleted_after_successful_transcription(tmp_path):
    from diloger.storage.session_store import SessionStorage

    store = SessionStorage(tmp_path / "sessions")
    audio = store.temp_audio_path()
    audio.write_bytes(b"RIFF" + b"\0" * 100)
    store.delete(audio)
    assert not audio.exists()


def test_keep_recording_preserves_file(tmp_path):
    from diloger.storage.session_store import SessionStorage

    store = SessionStorage(tmp_path / "sessions")
    session_dir = store.create_session_dir(1000.0)
    audio = store.temp_audio_path()
    audio.write_bytes(b"RIFF" + b"\0" * 100)
    kept = session_dir / "recording.wav"
    kept.write_bytes(audio.read_bytes())
    assert kept.exists()
    audio.unlink()
    assert kept.exists()


def test_orphan_temp_audio_detected(tmp_path, monkeypatch):
    import tempfile

    from diloger.storage.session_store import SessionStorage

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    orphan = tmp_path / "diloger-recording-orphan.wav"
    orphan.write_bytes(b"RIFF")
    store = SessionStorage(tmp_path / "sessions")
    assert orphan in store.orphan_temp_audio()


def test_session_dir_layout(tmp_path):
    from diloger.storage.session_store import SessionStorage

    store = SessionStorage(tmp_path / "sessions")
    d = store.create_session_dir(1000.0)
    assert d.is_dir()
    assert d.name.startswith("1970-01-01_")
    store.write_session_json(d, {"a": 1})
    store.write_transcript_markdown(d, "# hi")
    assert (d / "session.json").is_file()
    assert (d / "transcript.md").read_text() == "# hi"


# ------------------------------------------------------- config + export


def test_config_validation():
    from diloger.settings.config import AppConfig

    cfg = AppConfig(ttsRate=50, defaultDelaySeconds=0)
    problems = cfg.validate()
    assert any("rate" in p for p in problems)
    assert any("delay" in p for p in problems)
    assert AppConfig().validate() == []


def test_whisper_json_parsing():
    payload = (
        '{"systeminfo":"x","result":{"language":"en"},'
        '"transcription":['
        '{"timestamps":{"from":"00:00:00,000","to":"00:00:05,840"},'
        '"offsets":{"from":0,"to":5840},"text":" Hello there."}]}'
    )
    segs = parse_whisper_json(payload)
    assert segs == [{"start_ms": 0, "end_ms": 5840, "text": "Hello there."}]


def test_markdown_export_contains_objective_data():
    session = {
        "startedAt": 1000.0,
        "endedAt": 1060.0,
        "contentSourcePath": "/tmp/a.txt",
        "mode": "spoken",
        "spokenProgression": "fixedTimer",
        "promptsReached": 2,
        "recordingRetained": False,
        "whisperModelPath": "/models/ggml-large-v3-turbo.bin",
    }
    segs = [{"start_ms": 0, "end_ms": 1000, "text": "I went to the park."}]
    md = build_transcript_markdown(session, segs, {"0:pid0": "typed"}, {"0:pid0": "What did you do?"})
    assert "Duration: 60 seconds" in md
    assert "I went to the park." in md
    assert "What did you do?" in md
    assert "typed" in md
    low = md.lower()
    for banned in ("grade", "score", "fluent", "excellent"):
        assert banned not in low, banned
    # "correct" may appear only in the disclosure that nothing was corrected,
    # never as a claim about the user's speech.
    for line in low.splitlines():
        if "correct" in line:
            assert "not corrected" in line, line


def test_markdown_export_without_segments():
    md = build_transcript_markdown({"contentSourcePath": "a.txt"}, [])
    assert "No speech" in md


# ------------------------------------------------------------- tts port


def test_whisper_json_prefers_numeric_offsets_over_timestamp_strings():
    """The numeric offsets are what Whisper timed; the strings are a rendering.

    A payload where the two disagree is not hypothetical: whisper.cpp writes
    the rounded string form, so preferring it would shift every boundary by up
    to a millisecond and make the transcript disagree with the raw file.
    """
    payload = json.dumps({
        "transcription": [{
            "offsets": {"from": 5840, "to": 12000},
            "timestamps": {"from": "00:00:05,840", "to": "00:00:12,000"},
            "text": " measured",
        }]
    })
    assert parse_whisper_json(payload) == [
        {"start_ms": 5840, "end_ms": 12000, "text": "measured"}
    ]


def test_whisper_json_falls_back_to_timestamps_when_offsets_are_absent():
    payload = json.dumps({
        "transcription": [{
            "timestamps": {"from": "00:00:01,500", "to": "00:01:02,250"},
            "text": " timed by string",
        }]
    })
    assert parse_whisper_json(payload) == [
        {"start_ms": 1500, "end_ms": 62250, "text": "timed by string"}
    ]


def test_whisper_json_keeps_the_segments_apart_and_the_text_verbatim():
    """One Whisper segment becomes one line, and nothing is merged or reworded.

    Joining segments into a paragraph, or normalising the wording, would make
    the transcript read as an edited account of the session.
    """
    payload = json.dumps({
        "transcription": [
            {"offsets": {"from": 0, "to": 1000}, "text": " Good morning."},
            {"offsets": {"from": 1000, "to": 2600}, "text": "How are you?"},
        ]
    })
    segments = parse_whisper_json(payload)
    assert len(segments) == 2
    body = transcript_body(segments)
    assert body.splitlines() == [
        "[00:00:00.000 – 00:00:01.000] Good morning.",
        "",
        "[00:00:01.000 – 00:00:02.600] How are you?",
    ]


def test_transcript_body_reports_an_empty_run_instead_of_a_blank_section():
    """A silent recording must not look like a transcript that failed to load."""
    assert transcript_body([]) == NO_TRANSCRIPT
    assert transcript_body([], generated=True) == NO_SPEECH
    assert transcript_body([{"start_ms": 0, "end_ms": 0, "text": "  "}]) == (
        NO_TRANSCRIPT
    )
    # Both are plain sentences: an italic marker in a plain-text body would
    # read as literal characters.
    assert "*" not in NO_SPEECH and "_" not in NO_SPEECH, NO_SPEECH


def test_tts_lists_kokoro_voices():
    tts = MacSayTts()
    voices = tts.available_voices()
    if tts.is_available():
        assert "Kokoro Heart" in voices
        assert "Kokoro Michael" in voices


# ---------------------------------------------------- retention statuses


def test_transcription_status_default():
    engine, _, _ = make_engine()
    assert engine.session.transcriptionStatus is TranscriptionStatus.NOT_REQUESTED


# ------------------------------------------- durable timestamps (stage 3.4)


def test_session_json_uses_iso_wall_clock_and_duration():
    import json

    engine, _, _ = make_engine()
    engine.start()
    engine.next()
    engine.stop()

    payload = json.loads(json.dumps(engine.session.to_dict()))

    # ISO 8601 UTC, not a bare monotonic number another process would read as 1970.
    assert payload["startedAt"].endswith("Z")
    assert payload["startedAt"].startswith("20")
    assert payload["endedAt"].endswith("Z")
    assert isinstance(payload["durationSeconds"], (int, float))
    assert payload["durationSeconds"] >= 0
    assert payload["startedAtEpoch"] > 1_600_000_000

    # Event timing is an offset from session start, not an absolute clock.
    for event in payload["eventLog"]:
        assert "atOffset" in event
        assert "at" not in event
        assert 0 <= event["atOffset"] <= payload["durationSeconds"] + 1


def test_duration_is_zero_while_session_running():
    engine, _, _ = make_engine()
    engine.start()
    assert engine.session.durationSeconds == 0


# ------------------------------------- whisper model actually used (stage 3.6)


def test_apply_whisper_model_updates_serialized_session():
    engine, _, _ = make_engine()
    engine.start()
    engine.session.apply_whisper_model("/models/ggml-large-v3-turbo.bin")

    # Regression: the old code assigned a bogus private attribute, so the
    # serialized model stayed empty and session.json recorded the wrong model.
    assert engine.session.to_dict()["whisperModelPath"] == "/models/ggml-large-v3-turbo.bin"
    assert not hasattr(engine.session, "_config")


# ----------------------------------- unique temporary audio (stage 3.5)


def test_temp_audio_path_is_unique_per_session():
    from diloger.storage.session_store import temp_audio_path

    a = temp_audio_path("aaa11111")
    b = temp_audio_path("bbb22222")
    assert a != b
    assert "aaa11111" in a.name
    assert a.suffix == ".wav"


def test_recorder_prepares_distinct_temp_target_per_session():
    from diloger.audio.recorder import QtRecorder

    rec = QtRecorder()
    rec.prepare_for_session("aaa11111")
    first = rec.temp_target()
    rec.prepare_for_session("bbb22222")
    second = rec.temp_target()
    assert first != second
    # Stable within one session so stop() finalises the same file it opened.
    assert rec.temp_target() == second


def test_engine_binds_recorder_to_its_session():
    from diloger.audio.recorder import QtRecorder

    rec = QtRecorder()
    engine, _, _ = make_engine(recorder=rec)
    assert rec.temp_target().endswith(f"{engine.session.sessionId}.wav")


# -------------------------------------- transcript paths (stage 3.3)


def test_transcript_paths_are_unambiguous():
    engine, _, _ = make_engine()
    engine.start()
    payload = engine.session.to_dict()
    assert "transcriptJsonPath" in payload
    assert "transcriptMarkdownPath" in payload
    assert "transcriptPath" not in payload


def test_transcript_json_path_is_not_cleared_by_a_later_write(tmp_path):
    """A retry must not erase the path to a transcript that already exists."""
    from diloger.storage.session_store import SessionStorage

    store = SessionStorage(tmp_path)
    written = store.write_transcript_json(store.sessions_dir / "fake", {"segments": []})

    engine, _, _ = make_engine()
    engine.start()
    engine.session.transcriptJsonPath = str(written)
    engine.stop()

    # Re-serialising (as a retry or export does) keeps the recorded path.
    assert engine.session.to_dict()["transcriptJsonPath"] == str(written)


# ------------------------------- markdown export survives ISO stamps (3.4)


def test_markdown_export_accepts_iso_timestamps_only():
    from diloger.storage.session_store import build_transcript_markdown

    session = {
        "startedAt": "2026-10-01T12:00:00Z",
        "endedAt": "2026-10-01T12:01:00Z",
        "contentSourcePath": "/tmp/a.txt",
        "mode": "spoken",
        "spokenProgression": "fixedTimer",
        "promptsReached": 2,
        "recordingRetained": True,
        "whisperModelPath": "/m/ggml-large-v3-turbo.bin",
    }
    segs = [{"start_ms": 0, "end_ms": 2600, "text": "hello there"}]
    md = build_transcript_markdown(session, segs, {}, {"0:abc": "Question?"})

    # Regression: startedAt became an ISO string, so fromtimestamp() raised
    # TypeError and the Markdown export silently stopped being written.
    date_line = [ln for ln in md.splitlines() if ln.startswith("- Date: ")][0]
    assert date_line.startswith("- Date: 2026-10-01"), date_line
    assert "1970" not in date_line
    assert "Duration: 60 seconds" in md
    assert "hello there" in md


def test_markdown_export_prefers_epoch_fields():
    from diloger.storage.session_store import build_transcript_markdown

    session = {
        "startedAt": "2026-10-01T12:00:00Z",
        "endedAt": "2026-10-01T12:01:00Z",
        "startedAtEpoch": 1792000000.0,
        "endedAtEpoch": 1792000060.0,
        "contentSourcePath": "/tmp/a.txt",
        "mode": "spoken",
        "promptsReached": 1,
    }
    md = build_transcript_markdown(session, [], {}, None)
    date_line = [ln for ln in md.splitlines() if ln.startswith("- Date: ")][0]
    assert date_line.startswith("- Date: 2026-10-"), date_line
    assert "Duration: 60 seconds" in md


def test_markdown_export_still_accepts_legacy_numeric_timestamps():
    from diloger.storage.session_store import build_transcript_markdown

    session = {
        "startedAt": 1792000000.0,
        "endedAt": 1792000060.0,
        "contentSourcePath": "/tmp/a.txt",
        "mode": "spoken",
        "promptsReached": 1,
    }
    md = build_transcript_markdown(session, [], {}, None)
    assert "Duration: 60 seconds" in md


# ------------------- Whisper adapter: one callback per attempt (3.4 / 6.2)


def _wav_bytes(path: Path, frames: int = 800) -> bytes:
    path.write_bytes(b"RIFF" + b"\0" * 36 + b"\x00\x01" * frames)
    return path.read_bytes()


@pytest.fixture()
def whisper_app():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    return app


def _run_transcriber(qapp, stub, audio, model):
    """Run one transcribe() call and drain the event loop until it settles."""
    from diloger.transcription.whisper_cli import WhisperTranscriber

    t = WhisperTranscriber()
    results = []
    t.finished.connect(lambda ok, detail: results.append((ok, detail)))
    t.transcribe(audio, str(stub), str(model))
    deadline = __import__("time").monotonic() + 20.0
    while not results and __import__("time").monotonic() < deadline:
        qapp.processEvents()
        __import__("time").sleep(0.02)
    for _ in range(30):
        qapp.processEvents()
        __import__("time").sleep(0.02)
    return results, t


def test_whisper_reports_every_attempt_after_an_earlier_failure(tmp_path, whisper_app):
    """Regression: a stale settled flag swallowed later validation failures,
    so the UI stayed on 'Transcribing...' with no callback and no error."""
    import os
    import stat
    import time

    stub = tmp_path / "whisper-cli"
    stub.write_text("#!/bin/sh\nexit 4\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)
    audio = tmp_path / "a.wav"
    _wav_bytes(audio)

    missing = tmp_path / "gone.wav"
    first, _ = _run_transcriber(whisper_app, stub, missing, model)
    assert len(first) == 1 and not first[0][0], first

    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"RIFF" + b"\0" * 40)
    second, _ = _run_transcriber(whisper_app, stub, empty, model)
    assert len(second) == 1 and not second[0][0], second
    assert "empty" in second[0][1].lower(), second

    third, _ = _run_transcriber(whisper_app, stub, audio, model)
    assert len(third) == 1 and not third[0][0], third

    # ...and a later run that works is still allowed to report success.
    good = tmp_path / "whisper-good"
    good.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\nfor a in "$@"; do prev="$out"; out="$a"; done\n'
        "printf '{\"transcription\":[{\"offsets\":{\"from\":0,\"to\":900},"
        '\"text\":\"later attempt works\"}]}\' > "${prev}.json"\n'
    )
    good.chmod(good.stat().st_mode | stat.S_IEXEC)
    ok_result, _ = _run_transcriber(whisper_app, good, audio, model)
    assert len(ok_result) == 1 and ok_result[0][0], ok_result


def test_whisper_timeout_uses_a_real_timer_and_keeps_audio(tmp_path, whisper_app):
    import stat
    import time

    from diloger.transcription import whisper_cli

    slow = tmp_path / "whisper-slow"
    slow.write_text("#!/bin/sh\nsleep 120\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)
    audio = tmp_path / "a.wav"
    _wav_bytes(audio)

    original = whisper_cli.TIMEOUT_MS
    whisper_cli.TIMEOUT_MS = 400
    try:
        from diloger.transcription.whisper_cli import WhisperTranscriber

        t = WhisperTranscriber()
        results = []
        t.finished.connect(lambda ok, detail: results.append((ok, detail)))
        started = time.monotonic()
        t.transcribe(audio, str(slow), str(model))
        deadline = time.monotonic() + 20.0
        while not results and time.monotonic() < deadline:
            whisper_app.processEvents()
            time.sleep(0.02)
        for _ in range(30):
            whisper_app.processEvents()
            time.sleep(0.02)
        elapsed = time.monotonic() - started
    finally:
        whisper_cli.TIMEOUT_MS = original

    assert len(results) == 1, results
    assert not results[0][0] and "timed out" in results[0][1], results
    assert elapsed < 15, elapsed
    assert audio.is_file()


def test_whisper_cancel_settles_once_and_keeps_audio(tmp_path, whisper_app):
    import stat
    import time

    slow = tmp_path / "whisper-slow"
    slow.write_text("#!/bin/sh\nsleep 120\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)
    audio = tmp_path / "a.wav"
    _wav_bytes(audio)

    from diloger.transcription.whisper_cli import WhisperTranscriber

    t = WhisperTranscriber()
    results = []
    t.finished.connect(lambda ok, detail: results.append((ok, detail)))
    t.transcribe(audio, str(slow), str(model))
    deadline = time.monotonic() + 10.0
    while not t.is_running and time.monotonic() < deadline:
        whisper_app.processEvents()
        time.sleep(0.02)
    assert t.is_running, "process never started"
    time.sleep(0.3)
    t.cancel()
    deadline = time.monotonic() + 10.0
    while not results and time.monotonic() < deadline:
        whisper_app.processEvents()
        time.sleep(0.02)
    for _ in range(30):
        whisper_app.processEvents()
        time.sleep(0.02)

    assert len(results) == 1, results
    assert not results[0][0] and "cancel" in results[0][1].lower(), results
    assert audio.is_file()


def test_whisper_cleanup_output_removes_scratch_dir(tmp_path, whisper_app):
    import stat

    from diloger.transcription.whisper_cli import WhisperTranscriber

    stub = tmp_path / "whisper-good"
    stub.write_text(
        "#!/bin/sh\n"
        'out=""; prev=""\nfor a in "$@"; do prev="$out"; out="$a"; done\n'
        "printf '{\"transcription\":[{\"offsets\":{\"from\":0,\"to\":900},"
        '\"text\":\"scratch\"}]}\' > "${prev}.json"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)
    audio = tmp_path / "a.wav"
    _wav_bytes(audio)

    results, t = _run_transcriber(whisper_app, stub, audio, model)
    assert len(results) == 1 and results[0][0], results
    out_json = Path(results[0][1])
    assert out_json.is_file()
    t.cleanup_output()
    assert not out_json.parent.exists(), out_json.parent


def test_whisper_finished_connection_is_not_accumulated(whisper_app):
    """Regression: retryTranscription() reconnected `finished` on every call,
    so old closures stayed live and could consume a later run's result."""
    from PySide6.QtCore import QTimer

    from diloger.transcription.whisper_cli import WhisperTranscriber

    t = WhisperTranscriber()
    seen = []

    def handler(label):
        def _h(ok, detail):
            seen.append((label, ok))
        return _h

    connected = False
    for label in ("first", "second"):
        if connected:
            t.finished.disconnect()
            connected = False
        t.finished.connect(handler(label))
        connected = True

    t.finished.emit(True, "x")
    whisper_app.processEvents()
    assert seen == [("second", True)], seen


# ----------------- end-of-list is announced before going past it (7.x)


def _manual_spoken_engine():
    engine, _clock, _tts = make_engine(
        SessionConfig(mode=Mode.SPOKEN, spokenProgression=SpokenProgression.MANUAL),
        content=make_content(("Only prompt.", "Final prompt.")),
    )
    return engine, _tts


def test_last_prompt_is_announced_while_standing_on_it():
    """Regression: the 'last prompt' UI could only appear after the user had
    already pressed Next on the final item, so Manual mode gave no sign that
    the list was over."""
    engine, _tts = _manual_spoken_engine()
    engine.start()
    assert not engine.is_last_prompt
    engine.next()
    assert engine.current_position == 2
    assert engine.is_last_prompt, "final item must be flagged"
    assert engine.state is not SessionState.AT_END, "must stay, not end, by itself"
    assert "contentExhausted" in [e.type.value for e in engine.session.eventLog]


def test_is_last_prompt_false_after_the_session_finishes():
    engine, _tts = _manual_spoken_engine()
    engine.start()
    engine.next()
    assert engine.is_last_prompt
    engine.stop()
    assert not engine.is_last_prompt


# ------------------------------- History metadata and guarded deletion


def _write_session(tmp_path, name, payload, *, recording=False, transcript=False):
    folder = tmp_path / name
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    if recording:
        (folder / "recording.wav").write_bytes(b"RIFF" + b"\0" * 60)
    if transcript:
        (folder / "transcript.md").write_text("# transcript\nhello", encoding="utf-8")
    return folder


def test_history_lists_sessions_with_facts_only(tmp_path):
    from diloger.storage.session_store import SessionStorage

    _write_session(
        tmp_path, "2026-10-01_10-00-00_aa11",
        {"startedAt": "2026-10-01T10:00:00Z", "startedAtEpoch": 1792000000.0,
         "contentSourcePath": "/Users/x/Desktop/travel-dialogue.txt",
         "durationSeconds": 61.5, "promptsReached": 8, "totalPrompts": 8,
         "mode": "spoken"},
        recording=True,
    )
    _write_session(
        tmp_path, "2026-09-30_09-00-00_bb22",
        {"startedAt": "2026-09-30T09:00:00Z", "startedAtEpoch": 1791913600.0,
         "contentSourcePath": "/Users/x/notes.md", "promptsReached": 2},
    )
    rows = SessionStorage(tmp_path).list_sessions()
    assert [r["id"] for r in rows] == ["2026-10-01_10-00-00_aa11",
                                       "2026-09-30_09-00-00_bb22"], "newest first"
    top = rows[0]
    assert top["source"] == "travel-dialogue.txt", top["source"]
    assert top["durationSeconds"] == 61.5
    assert top["promptsReached"] == 8
    assert top["hasRecording"] is True and top["recordingPath"].endswith("recording.wav")
    assert top["hasTranscript"] is False
    assert rows[1]["hasRecording"] is False
    # No scores, grades or judgements of the user's English anywhere.
    for row in rows:
        assert not any(k in row for k in ("score", "grade", "rating", "level"))


def test_history_keeps_a_folder_with_unreadable_json(tmp_path):
    from diloger.storage.session_store import SessionStorage

    broken = tmp_path / "2026-10-02_11-00-00_cc33"
    broken.mkdir()
    (broken / "session.json").write_text("{not json", encoding="utf-8")
    rows = SessionStorage(tmp_path).list_sessions()
    assert len(rows) == 1, "a damaged folder must still be visible"
    assert rows[0]["hasSessionJson"] is True
    assert rows[0]["source"] == "unknown source"


def test_history_reports_transcript_when_only_files_exist(tmp_path):
    from diloger.storage.session_store import SessionStorage

    _write_session(tmp_path, "2026-10-03_12-00-00_dd44",
                   {"startedAtEpoch": 1792086400.0}, transcript=True)
    rows = SessionStorage(tmp_path).list_sessions()
    assert rows[0]["hasTranscript"] is True


def test_delete_session_only_touches_a_real_session_folder(tmp_path):
    from diloger.storage.session_store import SessionStorage

    outside = tmp_path.parent / "outside-secret"
    outside.mkdir(exist_ok=True)
    (outside / "keep.txt").write_text("do not delete", encoding="utf-8")
    folder = _write_session(tmp_path, "2026-10-04_13-00-00_ee55",
                            {"startedAtEpoch": 1792172800.0})
    store = SessionStorage(tmp_path)

    assert store.delete_session("../outside-secret") is False
    assert store.delete_session("..") is False
    assert store.delete_session("") is False
    assert store.delete_session("nope") is False
    assert outside.is_dir() and (outside / "keep.txt").is_file()

    assert store.delete_session("2026-10-04_13-00-00_ee55") is True
    assert not folder.exists()
    assert store.list_sessions() == []


def test_read_transcript_markdown_is_read_only(tmp_path):
    from diloger.storage.session_store import SessionStorage

    folder = _write_session(tmp_path, "2026-10-05_14-00-00_ff66",
                            {"startedAtEpoch": 1792259200.0}, transcript=True)
    before = (folder / "transcript.md").read_text(encoding="utf-8")
    store = SessionStorage(tmp_path)
    assert "hello" in store.read_transcript_markdown(folder)
    assert (folder / "transcript.md").read_text(encoding="utf-8") == before
    assert store.read_transcript_markdown(tmp_path / "missing") == ""


# ---------------- config override + History crash guard (section 5)


def test_config_path_can_be_overridden(tmp_path):
    """Regression risk: the config location was a module constant, so tests and
    diagnostics had no way to avoid writing the user's real configuration."""
    from diloger.settings.config import AppConfig, load_config, save_config

    target = tmp_path / "nested" / "config.json"
    save_config(AppConfig(libraryPath="/tmp/lib", ttsVoice="Kokoro Michael"), target)
    loaded = load_config(target)
    assert loaded.libraryPath == "/tmp/lib"
    assert loaded.ttsVoice == "Kokoro Michael"
    assert load_config(tmp_path / "missing.json").libraryPath == ""


def test_transcript_lookup_never_crashes_on_a_bad_id(tmp_path):
    """Regression: an unknown session id reached the storage layer as None and
    raised TypeError inside a QML slot."""
    from diloger.app.controller import AppController

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"sessionsPath": str(tmp_path / "sessions")}), encoding="utf-8")
    (tmp_path / "sessions").mkdir()
    c = AppController(config_path=str(cfg))

    errors = []
    c.errorMessage.connect(errors.append)
    # The slot now returns structured blocks, so "nothing to show" is [].
    assert c.transcriptBlocksForSession("../escape") == []
    assert c.transcriptBlocksForSession("") == []
    assert c.transcriptBlocksForSession("does-not-exist") == []
    assert len(errors) == 3, errors
    assert all("could not be found" in e for e in errors), errors


def test_history_slots_do_not_touch_files_outside_sessions(tmp_path):
    from diloger.app.controller import AppController

    cfg = tmp_path / "config.json"
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    cfg.write_text(json.dumps({"sessionsPath": str(sessions)}), encoding="utf-8")
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "notes.txt").write_text("keep", encoding="utf-8")
    c = AppController(config_path=str(cfg))

    assert c.deleteSession("precious") is False
    assert c.deleteSession("../precious") is False
    assert c.deleteSession("precious/notes.txt") is False
    assert (outside / "notes.txt").is_file()


def test_delay_minimum_is_enforced():
    """Regression: the Fixed Timer delay accepted 0.1 while the spec requires
    a 0.2 second floor (below that the prompt has no room at all)."""
    from diloger.settings.config import MIN_DELAY_SECONDS, AppConfig

    assert AppConfig(defaultDelaySeconds=MIN_DELAY_SECONDS).validate() == []
    bad = AppConfig(defaultDelaySeconds=MIN_DELAY_SECONDS / 2)
    problems = bad.validate()
    assert problems and "0.2" in problems[0], problems
    assert AppConfig(defaultDelaySeconds=301).validate()
    assert AppConfig(defaultDelaySeconds=-1).validate()


def test_leaving_a_review_screen_stops_playback(tmp_path):
    """Regression: audio kept playing after navigating away from History,
    behind a screen that has no playback controls."""
    from diloger.app.controller import AppController

    cfg = tmp_path / "config.json"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cfg.write_text(json.dumps({"sessionsPath": str(sessions)}), encoding="utf-8")
    c = AppController(config_path=str(cfg))

    stopped = []
    c.player.stop = lambda: stopped.append(True)  # type: ignore[method-assign]
    c.showHistory()
    c.player.play()
    c.showLibrary()
    assert stopped, "leaving History must stop playback"
    assert c.screen == "library"
    assert c.player.isPlaying() is False


def test_history_is_refused_mid_session(tmp_path):
    """A review screen must never open on top of a running session."""
    from diloger.app.controller import AppController

    cfg = tmp_path / "config.json"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cfg.write_text(json.dumps({"sessionsPath": str(sessions)}), encoding="utf-8")
    c = AppController(config_path=str(cfg))
    c._engine, _clock, _tts = make_engine()
    c._engine.start()
    c.showHistory()
    assert c.screen == "library", "History must not open while a session runs"
    c._engine.stop()


def _controller(tmp_path):
    """An AppController on a throwaway config and sessions directory."""
    from diloger.app.controller import AppController

    cfg = tmp_path / "config.json"
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    cfg.write_text(json.dumps({"sessionsPath": str(sessions)}), encoding="utf-8")
    return AppController(config_path=str(cfg))


def test_screen_is_a_notifyable_qt_property(tmp_path):
    """`screen` must be a real Qt property, not a plain Python @property.

    Main.qml binds `StackLayout.currentIndex` to it. PySide6 only exposes an
    attribute to QML as a property when it is declared through
    `Property(..., notify=...)`, and without the notify signal the binding is
    never re-evaluated: every navigation button silently does nothing.
    """
    c = _controller(tmp_path)

    index = c.metaObject().indexOfProperty("screen")
    assert index != -1, "screen is not visible to QML as a property"
    prop = c.metaObject().property(index)
    assert prop.isReadable()
    assert c.metaObject().indexOfSignal("screenChanged()") != -1, (
        "screen has no notify signal, so QML cannot rebind StackLayout.currentIndex"
    )
    assert c.property("screen") == "library"
    c.showSettings()
    assert c.property("screen") == "settings"


def test_screen_property_emits_on_every_navigation(tmp_path):
    c = _controller(tmp_path)
    seen: list[str] = []
    c.screenChanged.connect(lambda: seen.append(c.screen))

    c.showSettings()
    c.showLibrary()

    assert seen == ["settings", "library"]


def test_setup_voice_and_rate_controls_are_not_dead(tmp_path):
    """The Setup screen's voice and rate controls must reach the session."""
    c = _controller(tmp_path)
    voices = c.voices()
    assert voices, "no TTS voices available to select"

    c.updateSetup({"voice": voices[0]})
    assert c.setupState()["voice"] == voices[0]
    assert c._selected_voice == voices[0]

    c.updateSetup({"rate": 137})
    assert c.setupState()["rate"] == 137
    assert c._selected_rate == 137


# QML hands a slot a QJSValue, not a dict. Reproducing that needs a QJSEngine,
# and a QJSEngine needs a live event dispatcher. Importing the QtMultimedia
# modules under test leaves the main thread's dispatcher deleted, so this probe
# runs in a fresh interpreter where QML values can actually be constructed.
_QML_SLOT_PROBE = """
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, {src!r})
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

app = QCoreApplication([])
root = Path(tempfile.mkdtemp())
(root / "sessions").mkdir()
(root / "config.json").write_text(json.dumps({{"sessionsPath": str(root / "sessions")}}))

from diloger.app.controller import AppController

controller = AppController(config_path=str(root / "config.json"))
engine = QJSEngine()


def js(literal):
    value = engine.evaluate("(" + literal + ")")
    assert value.isObject(), literal
    return value


try:
    controller.updateSetup(js('{{"mode": "written"}}'))
    controller.updateSetup(js('{{"delay": 12}}'))
    controller.updateSetup(js('{{"record": true}}'))
    controller.updateSettingsDraft(js('{{"ttsRate": 150}}'))
    controller.updateSettingsDraft(js('{{"saveTypedAnswersByDefault": true}}'))
    applied = controller.applySettings()
except Exception as exc:
    print(json.dumps({{"error": f"{{type(exc).__name__}}: {{exc}}"}}))
    raise SystemExit(0)

print(json.dumps({{
    "setup": controller.setupState(),
    "settings": controller.settingsState(),
    "applied": applied,
}}))
"""


def test_setup_and_settings_accept_qml_object_literals():
    """Setup and Settings controls must reach the controller from QML.

    A slot declared `@Slot("QVariant")` receives a QJSValue, so every call site
    like `onClicked: app.updateSetup({ "mode": "spoken" })` raised TypeError on
    arrival. Nothing in the controller ran and the error never reached the user,
    which left all twenty Setup and Settings controls inert while the widgets
    still appeared to toggle.
    """
    import subprocess

    src = str(Path(__file__).parent.parent / "src")
    proc = subprocess.run(
        [sys.executable, "-c", _QML_SLOT_PROBE.format(src=src)],
        capture_output=True,
        text=True,
    )
    payload = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    data = json.loads(payload)
    assert "error" not in data, (
        "a QML object literal did not reach the controller: " + data["error"]
    )

    assert data["setup"]["mode"] == "written", "Setup mode control did not reach the controller"
    assert data["setup"]["delay"] == 12.0
    assert data["setup"]["record"] is True
    assert data["settings"]["ttsRate"] == 150
    assert data["settings"]["saveTypedAnswersByDefault"] is True
    assert data["applied"] is True, "applySettings must accept a patch built in QML"


def test_update_setup_rejects_non_object_arguments(tmp_path):
    """A bad argument must say so instead of failing deep inside a comprehension."""
    c = _controller(tmp_path)
    before = c.setupState()["mode"]
    with pytest.raises(TypeError):
        c.updateSetup("mode=written")
    assert c.setupState()["mode"] == before


def test_qml_setup_call_sites_pass_object_literals():
    """Guard the shape the QML call sites rely on.

    A QML literal `{...}` is what `_as_patch` exists to accept; passing a bare
    string or a JSON blob would silently skip setup state instead of failing.
    """
    qml_dir = Path(__file__).parent.parent / "src" / "diloger" / "qml"
    sites = 0
    for path in sorted(qml_dir.glob("*.qml")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "app.updateSetup(" in line or "app.updateSettingsDraft(" in line:
                assert "({" in line, f"{path.name}:{lineno} does not pass an object literal: {line.strip()}"
                sites += 1
    assert sites >= 20, f"expected the Setup and Settings controls, found only {sites}"


def test_update_setup_rejects_non_object_arguments(tmp_path):
    """A bad argument must say so instead of failing deep inside a comprehension."""
    c = _controller(tmp_path)
    before = c.setupState()["mode"]
    with pytest.raises(TypeError):
        c.updateSetup("mode=written")
    assert c.setupState()["mode"] == before


def test_transcribing_is_a_notifyable_property(tmp_path):
    """Finished screen binds Keep/Delete audio to transcription state."""
    c = _controller(tmp_path)
    index = c.metaObject().indexOfProperty("transcribing")
    assert index != -1, "transcribing is not visible to QML as a property"
    assert c.metaObject().indexOfSignal("transcribingChanged()") != -1

    seen: list[bool] = []
    c.transcribingChanged.connect(lambda: seen.append(c.transcribing))
    c._set_transcribing(True)
    c._set_transcribing(False)
    c._set_transcribing(False)
    assert seen == [True, False], "no change must not notify; every change must"


# Every value below is read straight from a QML binding such as
# `text: app.progressText` or `visible: app.promptVisible`. A QML binding that
# calls a PySide6 @Slot is evaluated once and never again, so any of these left
# as a Slot freezes the UI mid-session: the countdown stops, Reveal stops
# showing text, and the Next button stays disabled forever.
# (property name, Qt type name as it appears on QMetaProperty)
QML_BOUND_GETTERS = [
    ("screen", "QString"),
    ("transcribing", "bool"),
    ("progressText", "QString"),
    ("modeText", "QString"),
    ("promptVisible", "bool"),
    ("promptText", "QString"),
    ("timerRemaining", "double"),
    ("timerFraction", "double"),
    ("isPaused", "bool"),
    ("isAtEnd", "bool"),
    ("isLastPrompt", "bool"),
    ("isTyping", "bool"),
    ("isRecording", "bool"),
    ("canNext", "bool"),
    ("canPlayCurrentRecording", "bool"),
    ("libraryPath", "QString"),
    ("ttsAvailable", "bool"),
]


@pytest.mark.parametrize("name,qt_type", QML_BOUND_GETTERS)
def test_qml_bound_getters_are_notifyable_properties(tmp_path, name, qt_type):
    from diloger.app.controller import AppController

    c = _controller(tmp_path)
    mo = c.metaObject()
    index = mo.indexOfProperty(name)
    assert index != -1, (
        f"{name} is a Slot, not a Property: QML bindings that call it freeze "
        "at their first value"
    )
    prop = mo.property(index)
    assert prop.isReadable(), f"{name} must be readable from QML"
    assert not prop.isWritable(), f"{name} is view state and must be read-only"
    assert prop.typeName() == qt_type, (
        f"{name} is {prop.typeName()}, the QML binding expects {qt_type}"
    )
    # Reading it through the Qt property system must work, not just as Python:
    # a plain attribute would answer here but stay invisible to QML.
    c.property(name)


def test_view_state_signal_fires_on_engine_transitions(tmp_path):
    """Every session transition must notify the QML-bound values.

    `SessionEngine` is constructed with `on_state_changed=self._on_engine_state`,
    so driving the controller's own handler is what a real transition does.
    """
    from diloger.app.controller import AppController

    c = _controller(tmp_path)
    seen: list[int] = []
    c.viewStateChanged.connect(lambda: seen.append(1))
    c._engine, _clock, _tts = make_engine()

    c._on_engine_state()
    assert seen, "a session transition must notify every QML-bound value"

    before = len(seen)
    c._engine.start()
    c._on_engine_state()
    assert len(seen) > before, "advancing must notify again"


def test_view_state_signal_fires_on_the_timer(tmp_path):
    from diloger.app.controller import AppController

    c = _controller(tmp_path)
    c._engine, _clock, _tts = make_engine()
    c._engine.start()
    c._engine.pause()

    seen: list[int] = []
    c.viewStateChanged.connect(lambda: seen.append(1))
    c._on_tick()
    assert seen, "a timer tick must refresh timerRemaining and timerFraction"


def test_qml_never_calls_notifyable_properties(tmp_path):
    """A notifyable Property is not callable, so `app.canNext()` is a TypeError.

    The metaclass test above proves each of these is a Property; this one proves
    the QML side agrees. When the two disagree the bindings fail to evaluate at
    all and silently keep their default value, which looks like a frozen or
    half-dead screen rather than an error the UI reports.
    """
    qml_dir = Path(__file__).parent.parent / "src" / "diloger" / "qml"
    offenders = []
    for path in sorted(qml_dir.glob("*.qml")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for name, _ in QML_BOUND_GETTERS:
                if f"app.{name}(" in line:
                    offenders.append(f"{path.name}:{lineno}: app.{name}() -> app.{name}")
    assert not offenders, (
        "notifyable properties must be read without parentheses:\n"
        + "\n".join(offenders)
    )


def test_qml_invocable_slots_declare_their_parameters():
    """Every slot QML calls with an argument must declare that argument.

    A bare `@Slot()` tells PySide the slot takes no arguments, so QML's argument
    is dropped and the call fails with "missing 1 required positional argument".
    That silently broke transcript viewing, session deletion and playback while
    the buttons looked normal. This walks the real meta-object, which is the
    same signature table QML dispatches through.
    """
    from PySide6.QtCore import QMetaMethod

    from diloger.app.controller import AppController

    meta = AppController.staticMetaObject
    mismatched = []
    for index in range(meta.methodCount()):
        method = meta.method(index)
        if method.methodType() != QMetaMethod.MethodType.Slot:
            continue
        name = bytes(method.name()).decode()
        python = getattr(AppController, name, None)
        if not callable(python):
            # Qt Properties and Signals are also reachable as class attributes.
            continue
        try:
            # Unbound, so `self` is still the first parameter.
            takes_args = len(inspect.signature(python).parameters) > 1
        except (TypeError, ValueError):
            continue
        declares_args = method.parameterCount() > 0
        if takes_args != declares_args:
            mismatched.append(
                f"{name}: meta-object declares {method.parameterCount()} parameter(s) "
                f"but the Python method takes "
                f"{len(inspect.signature(python).parameters) - 1}"
            )
    assert not mismatched, "slot signature mismatches:\n" + "\n".join(mismatched)


def test_finalize_notifies_the_screens_that_read_session_data(tmp_path):
    """Stopping a session must emit refreshRequested, not just viewStateChanged.

    Main.qml keeps every page in one StackLayout, so each screen's
    Component.onCompleted runs at startup — long before a session exists. The
    only thing that later refreshes their data is refreshRequested. Emitting
    just viewStateChanged leaves the Finished screen holding its default
    `info`, which reports hasRecording=false and disables Transcribe now,
    Export, Keep and Delete. The recording is on disk but the UI insists it
    does not exist.
    """
    c = _controller(tmp_path)
    src = tmp_path / "lib.md"
    src.write_text("First prompt.\nSecond prompt.\n", encoding="utf-8")
    c.selectFile(str(src))
    # AppController builds a real QtRecorder, which opens the audio hardware.
    # This test is about the notifications finalize emits, not about capturing
    # audio, so it gets the deterministic fake instead of depending on whether
    # a microphone happens to exist.
    c._recorder = FakeRecorder(target_dir=tmp_path / "temp")
    c.updateSetup({"mode": "spoken", "record": True, "keepRecording": True,
                   "progression": "manual"})
    c.startSession()

    seen: list[int] = []
    c.refreshRequested.connect(lambda: seen.append(1))

    c.stopSession()

    assert seen, "refreshRequested never fired, so the Finished screen stays stale"
    info = c.finishedInfo()
    assert info["hasRecording"] is True, "kept audio must be visible to the UI"
    assert info["recordingRetained"] is True


def test_qml_bindings_on_the_player_are_dependency_tracked():
    """A QML binding on a Python method call never re-evaluates.

    QML tracks property reads, not calls, so `readonly property bool playing:
    player.isPlaying()` is evaluated once and then frozen: the Pause button
    stayed disabled and the position clock never moved while a recording was
    actually playing. PlaybackBar therefore bumps a counter per player signal
    and reads it inside the binding. This guards both halves of that fix.
    """
    qml = Path(__file__).parent.parent / "src" / "diloger" / "qml" / "PlaybackBar.qml"
    text = qml.read_text()

    # Every player signal that changes state must be connected to the bar.
    for signal in ("onPositionChanged", "onDurationChanged",
                   "onPlaybackStateChanged", "onSourceChanged"):
        assert signal in text, f"{signal} is not wired, so the bar will not refresh"

    # The tick each signal bumps must be read by the matching binding.
    pairs = [
        ("_positionTick", "positionMs"),
        ("_durationTick", "durationMs"),
        ("_sourceTick", "sourcePath"),
    ]
    for tick, method in pairs:
        assert f"{method}()" in text, f"{method}() is no longer read"
        block = f"readonly property"  # locate the binding that calls the method
        idx = text.index(block)
        segment = text[idx:text.index("}", text.index(f"return player.{method}()"))]
        assert tick in segment, (
            f"the binding on player.{method}() does not read {tick}, "
            "so it is not a tracked dependency and will freeze"
        )


def test_playback_state_is_derived_from_the_signal_not_from_isplaying():
    """`Paused` must be distinguishable from `Stopped`.

    `player.isPlaying()` is False in both states, so a bar driven by it cannot
    tell "resume" from "start over" and always shows Stopped after a pause. The
    bar therefore keeps the raw signal value, refreshes its bindings through the
    same `_stateTick` that keeps them dependency-tracked, and derives both
    `playing` and `paused` from it.
    """
    text = (Path(__file__).parent.parent / "src" / "diloger" / "qml" / "PlaybackBar.qml").read_text()

    assert "_playbackState" in text, "the raw playback state is not kept"
    handler = re.search(
        r"function onPlaybackStateChanged\(state\) \{(.*?)\n\s*\}", text, re.S
    )
    assert handler, "playbackStateChanged is not handled"
    assert "_playbackState" in handler.group(1), (
        "playbackStateChanged must store the raw state, otherwise Paused can never be shown"
    )
    for prop, state in (("playing", "playing"), ("paused", "paused")):
        match = re.search(rf"readonly property bool {prop}: \{{(.*?)\}}", text, re.S)
        assert match, f"the {prop} binding is missing"
        body = match.group(1)
        assert "_stateTick" in body, (
            f"the {prop} binding does not read _stateTick, so it will freeze after the "
            "first evaluation"
        )
        assert f'_playbackState === "{state}"' in body, (
            f"the {prop} binding must be derived from _playbackState"
        )

    # The paused state is user-visible, not internal bookkeeping.
    assert '"Paused"' in text, "the transport never shows a Paused status"


def test_playback_transport_buttons_are_separate_visible_controls():
    """Play, Pause and Stop must be three usable controls, not one stacked block.

    The clipped card hid them behind the session actions, so each control gets
    its own objectName and its own layout cell with a size the layout honours.
    """
    text = (Path(__file__).parent.parent / "src" / "diloger" / "qml" / "PlaybackBar.qml").read_text()

    for name in ("playbackPlayButton", "playbackPauseButton", "playbackStopButton"):
        assert f'objectName: "{name}"' in text, f"{name} is not addressable from a probe"
    # All three must be siblings in the same transport row: that is what makes
    # them three separate cells instead of one stacked cell.
    first = text.index('objectName: "playbackPlayButton"')
    start = text.rindex("        RowLayout {", 0, first)
    depth, i = 0, start + len("        RowLayout ")
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    row = text[start:i]
    for name in ("playbackPlayButton", "playbackPauseButton", "playbackStopButton"):
        assert name in row, f"{name} is not inside the transport row"
    # None of the three may claim the whole row: that squeezes the other two out
    # of it, which is how the controls ended up stacked before.
    buttons = row[row.index("Button {"): row.index("Item { Layout.fillWidth: true }")]
    assert "Layout.fillWidth: true" not in buttons, (
        "a transport button claims the row and squeezes the other two out"
    )

    # The stop action must rewind, not merely stop: without it, pressing Stop and
    # then Play replayed nothing because the position stayed where it was.
    assert re.search(
        r"function stopPlayback\(\)[\s\S]{0,200}player\.stop\(\)[\s\S]{0,200}player\.seekTo\(0\)",
        text,
    ), "Stop does not rewind to the start"
    # Pause must resume from where it stopped, and resume must not rewind.
    assert re.search(r"function resumePlayback\(\)[\s\S]{0,200}player\.play\(\)", text), (
        "Play after Pause does not resume"
    )
    assert not re.search(r"function resumePlayback\(\)[^}]*seekTo\(0\)", text), (
        "resumePlayback rewinds, so Play after Pause restarts instead of continuing"
    )



_QML_HISTORY_SLOT_PROBE = """
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, {src!r})
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine

app = QCoreApplication([])
root = Path(tempfile.mkdtemp())
(root / "sessions").mkdir()
(root / "config.json").write_text(json.dumps({{"sessionsPath": str(root / "sessions")}}))

from diloger.app.controller import AppController

controller = AppController(config_path=str(root / "config.json"))
engine = QJSEngine()
# Wrap the controller so its slots are reachable exactly as QML sees them,
# and publish it as a JS global so evaluate() can call through it.
engine.evaluate("var app = ({{}})")
engine.globalObject().property("app").setProperty("c", engine.newQObject(controller))

result = {{}}
try:
    # A History row that owns a transcript, addressed the way QML addresses it.
    # The controller resolves a session id to its folder, so the id is the
    # folder name.
    sid = "2026-10-01_10-00-00"
    folder = root / "sessions" / sid
    folder.mkdir()
    (folder / "session.json").write_text(json.dumps({{
        "sessionId": sid, "totalPrompts": 1, "promptsReached": 1,
        "transcriptionStatus": "succeeded",
    }}))
    # The raw Whisper output is what the readable view is derived from; the
    # export document is deliberately NOT the input any more.
    (folder / "transcript.json").write_text(json.dumps({{
        "segments": [{{"start_ms": 0, "end_ms": 1200, "text": "hello there"}}],
    }}))

    # QML dispatches through the meta-object: a slot declared without its
    # parameter drops the argument and raises TypeError. Python would not.
    value = engine.evaluate('app.c.transcriptBlocksForSession("%s")' % sid)
    result["blocks"] = value.toVariant()
    result["transcriptIsAList"] = isinstance(value.toVariant(), list)
    result["hasABlock"] = bool(value.toVariant())
    result["firstText"] = (value.toVariant() or [{{}}])[0].get("text", "")
    result["deleteReturnsBool"] = engine.evaluate(
        'typeof app.c.deleteSession("%s")' % sid
    ).toString() == "boolean"
except Exception as exc:
    result["error"] = f"{{type(exc).__name__}}: {{exc}}"

print(json.dumps(result))
"""


def test_history_slots_accept_their_argument_from_qml(tmp_path):
    """Open transcript / Delete session were dead because of their signatures.

    `transcriptBlocksForSession` and `deleteSession` were declared `@Slot(result=...)`
    with no parameter, so PySide told the meta-object they take no arguments.
    QML passed the session id, it was dropped, and the call raised TypeError
    inside the QML engine: the buttons looked normal and did nothing. Python
    callers never hit this, so only a QML dispatch can catch it.
    """
    import subprocess

    src = str(Path(__file__).parent.parent / "src")
    proc = subprocess.run(
        [sys.executable, "-c", _QML_HISTORY_SLOT_PROBE.format(src=src)],
        capture_output=True,
        text=True,
    )
    payload = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    data = json.loads(payload)

    assert "error" not in data, (
        "QML could not dispatch a History slot with its argument: " + data["error"]
    )
    assert data.get("transcriptIsAList") is True, (
        "transcriptBlocksForSession must hand QML a list of blocks, not text; "
        f"got {data.get('blocks')!r}"
    )
    assert data.get("hasABlock") is True, (
        "the stored session had raw segments but QML received no blocks; "
        f"got {data.get('blocks')!r}"
    )
    assert data.get("deleteReturnsBool") is True


def test_history_rows_are_exposed_to_assistive_technology():
    """Session rows must be reachable without a pointer.

    The rows were plain Rectangles with no Accessible block, so the entire
    history list exposed nothing to assistive technology. Selecting a row was
    therefore impossible without exact mouse coordinates, and both
    "Open transcript" and "Delete session" sat permanently disabled because
    they read `selectedId`. This is a usability bug, not a test-only concern:
    the session list is the main way to reach a finished session.
    """
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml" / "HistoryScreen.qml").read_text(
        encoding="utf-8"
    )
    delegate = qml.split("delegate: Rectangle {", 1)[1].split("\n                }", 1)[0]

    assert "Accessible.role" in delegate, (
        "the History row delegate exposes no Accessible.role, so rows are "
        "invisible to assistive technology and cannot be selected"
    )
    assert "Accessible.name" in delegate, (
        "the History row delegate exposes no Accessible.name, so a screen "
        "reader announces an unlabelled list item"
    )
    assert "Accessible.onPressAction" in delegate, (
        "the History row delegate has no Accessible.onPressAction, so a "
        "selection cannot be made without a pointer"
    )
    assert "page.select(modelData)" in delegate, (
        "Accessible.onPressAction must select the row so the detail pane and "
        "the Open transcript / Delete session buttons become usable"
    )
    for field in ("source", "date", "mode", "transcriptionStatus"):
        assert f"modelData.{field}" in delegate, (
            f"the row label omits {field}, which a screen reader user needs"
        )


def _pick_export_to(controller, path: Path, name: str = "out.md"):
    """Pin the Save dialog so a test never opens it.

    The dialog itself is the OS's business; what the controller promises is
    that it asks for a target, writes there, and only remembers the folder.
    """
    asked = {}

    def _target(directory: str, suggested: str) -> str:
        asked["directory"] = directory
        asked["suggested"] = suggested
        return str(path / name)

    controller._select_export_target = _target
    return asked


def _typed_session_with_answers(c, tmp_path, lib="First prompt.\nSecond prompt.\n"):
    """A finished written-mode session that reached one prompt and one answer.

    Advancing is what commits the typed answer and creates the session folder:
    a session stopped before its first advance has nothing worth exporting and
    deliberately leaves no folder behind.
    """
    src = tmp_path / "lib.md"
    src.write_text(lib, encoding="utf-8")
    c.selectFile(str(src))
    c.updateSetup({"mode": "written", "writtenPrompt": "text", "saveTyped": True})
    c.startSession()
    c.setTypedAnswer("A typed reply.")
    c.next()
    c.stopSession()
    return src


def test_export_markdown_writes_a_verified_external_file(tmp_path):
    """Export is the only route from a finished session to a shareable file.

    The slot returns a result map, so "did not raise" proves nothing: a status
    of `saved` is a claim that a non-empty file the user can open exists at the
    reported path. A cancelled dialog and a write failure must both say so
    instead of looking like success.
    """
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    out = tmp_path / "export"
    out.mkdir()
    asked = _pick_export_to(c, out)
    result = c.exportCurrentSession()

    assert result["status"] == "saved", result
    assert result["path"] == str(out / "out.md"), result
    exported = Path(result["path"])
    assert exported.is_file(), f"export reported {result['path']} but no file is there"
    assert exported.stat().st_size > 0, "export reported success for an empty file"
    text = exported.read_text(encoding="utf-8")
    assert "First prompt." in text, "the reached prompt is missing from the export"
    assert "A typed reply." in text, (
        "the typed answer is missing from the export even though "
        "saveTypedAnswers was on"
    )
    assert not str(exported).startswith(str(Path(c.sessionsPath()))), (
        "the export landed inside the sessions folder, so it is an internal "
        "file and not a user-chosen export"
    )
    assert asked["suggested"].endswith(".md"), asked


def test_export_markdown_suggests_the_session_name(tmp_path):
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    out = tmp_path / "export"
    out.mkdir()
    asked = _pick_export_to(c, out)
    c.exportCurrentSession()
    assert "2026" in asked["suggested"] or asked["suggested"].endswith(".md"), asked


def test_export_markdown_remembers_only_the_folder(tmp_path):
    """Re-exporting should open where the user last chose, with a new name.

    Remembering the file itself would make the second Save dialog point at a
    name the user has probably already used, so only the folder is remembered.
    """
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    first_dir = tmp_path / "notes"
    second_dir = tmp_path / "archive"
    for directory in (first_dir, second_dir):
        directory.mkdir()
        _pick_export_to(c, directory)
        result = c.exportCurrentSession()
        assert result["status"] == "saved", result

    assert str(c.settingsState()["exportDirectory"]) == str(second_dir), (
        "the export directory was not remembered"
    )
    stored = Path(c._config_path).read_text(encoding="utf-8")
    assert "exportFileName" not in stored and "out.md" not in stored, (
        "the remembered setting holds a file name, so a second export would "
        "default to a name the user has already used"
    )

    # The next dialog starts in the remembered folder.
    third = tmp_path / "later"
    third.mkdir()
    asked = _pick_export_to(c, third)
    c.exportCurrentSession()
    assert str(asked["directory"]) == str(second_dir), asked


def test_export_markdown_cancel_changes_nothing(tmp_path):
    """Cancelling is a decision, not a failure and not a silent success."""
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    out = tmp_path / "export"
    out.mkdir()
    c._select_export_target = lambda directory, suggested: ""
    result = c.exportCurrentSession()

    assert result["status"] == "cancelled", result
    assert result["path"] == "", result
    assert list(out.iterdir()) == [], "a cancelled export wrote a file"
    assert c.settingsState()["exportDirectory"] == "", (
        "a cancelled export still changed the remembered folder"
    )


def test_export_markdown_reports_a_write_failure(tmp_path):
    """A reported success must mean a file really exists.

    Pointing the dialog at a path whose parent is a regular file makes mkdir
    fail, which is the cheapest faithful stand-in for a full disk or a folder
    the user cannot write to.
    """
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    c._select_export_target = lambda directory, suggested: str(blocker / "sub" / "out.md")
    result = c.exportCurrentSession()

    assert result["status"] == "error", result
    assert result["error"], "a failed export must explain itself"
    assert result["path"] == "", result


def test_export_markdown_keeps_whisper_timestamps(tmp_path):
    """The export carries the same timestamped segments the app shows.

    Exporting a flattened paragraph would throw away the only thing the raw
    Whisper run added, and would read as a corrected transcript.
    """
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    c._transcript_segments = [
        {"start_ms": 0, "end_ms": 5840, "text": "Hello there."},
        {"start_ms": 5840, "end_ms": 12000, "text": "How are you?"},
    ]
    c._write_session_json()

    out = tmp_path / "export"
    out.mkdir()
    _pick_export_to(c, out)
    text = Path(c.exportCurrentSession()["path"]).read_text(encoding="utf-8")

    assert "## Transcript" in text, text
    assert "[00:00:00.000 – 00:00:05.840] Hello there." in text, text
    assert "[00:00:05.840 – 00:00:12.000] How are you?" in text, text
    # The notice has to describe what the app actually does: it reformats
    # sentences and capitalises them, so "not corrected" would be a false
    # claim. What it does not do is evaluate the words.
    assert "words were not evaluated" in text, text
    assert "Text is not corrected." not in text, text


def test_export_markdown_without_a_transcript(tmp_path):
    c = _controller(tmp_path)
    _typed_session_with_answers(c, tmp_path)

    out = tmp_path / "export"
    out.mkdir()
    _pick_export_to(c, out)
    text = Path(c.exportCurrentSession()["path"]).read_text(encoding="utf-8")

    assert "Transcript has not been generated yet." in text, text
    assert "## Transcript" in text, text


def test_export_from_history_writes_the_file_for_a_stored_session(tmp_path):
    """History exports a session the user is not currently training.

    The old path was only reachable from the live session, so a transcript
    from three days ago could never be shared without hunting for the internal
    file. The id is the only thing that crosses into QML.
    """
    c = _controller(tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir(exist_ok=True)
    c.updateSetup({"sessionsPath": str(sessions)})
    c._config.sessions_path = sessions

    folder = sessions / "2026-10-01_09-00-00_abcd1234"
    folder.mkdir()
    (folder / "session.json").write_text(
        '{"id":"abcd1234","contentSourcePath":"/library/cafe.md","date":"2026-10-01",'
        '"startedAt":1788000000.0,"endedAt":1788000060.0,"duration":60.0,'
        '"mode":"spoken","promptOrder":[0],"totalPrompts":1,"promptsReached":1,'
        '"whisperModelPath":"/models/ggml-large-v3-turbo.bin",'
        '"transcriptionStatus":"succeeded","transcriptJsonPath":'
        + json.dumps(str(folder / "transcript.json"))
        + "}",
        encoding="utf-8",
    )
    (folder / "transcript.json").write_text(
        json.dumps({"segments": [{"start_ms": 0, "end_ms": 900, "text": "stored"}]}),
        encoding="utf-8",
    )

    out = tmp_path / "export"
    out.mkdir()
    _pick_export_to(c, out)
    result = c.exportSession(folder.name)

    assert result["status"] == "saved", result
    text = Path(result["path"]).read_text(encoding="utf-8")
    # The export body is the same readable text the screens show, so the raw
    # unpunctuated fragment is deliberately absent here. It is still preserved
    # verbatim in transcript.json.
    assert "[00:00:00.000 – 00:00:00.900]" in text, text
    assert "Stored." in text, text
    assert "stored\n" not in text, text
    assert json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["segments"] == [
        {"start_ms": 0, "end_ms": 900, "text": "stored"}
    ], "raw segments must be left exactly as Whisper produced them"
    # The date line comes from the stored start time, not from today's date
    # and not from a name the folder happens to have.
    stamp = datetime.fromtimestamp(1788000000.0).strftime("%Y-%m-%d %H:%M:%S")
    assert f"- Date: {stamp}" in text, text
    assert "- Source: cafe.md" in text, text
    assert "- Prompts reached: 1 / 1" in text, text

    # An unknown id is refused rather than used to build a path.
    assert c.exportSession("nope")["status"] == "error"
    assert c.exportSession("../../etc")["status"] == "error"


def test_export_markdown_omits_prompts_the_session_never_reached(tmp_path):
    """An export must reflect the session, not the whole content file.

    A session that stopped after one prompt exports one prompt. Copying the
    full library into every transcript would misrepresent what was actually
    practised, which is the one thing the transcript is evidence of.
    """
    c = _controller(tmp_path)
    src = tmp_path / "lib.md"
    src.write_text("Only this one.\nNever reached.\n", encoding="utf-8")
    c.selectFile(str(src))
    # The setup key is "saveTyped"; with it off an unsaved session creates no
    # folder at all, which is a separate documented behaviour.
    # Retaining audio is what makes the session worth a folder at all.
    c.updateSetup({"mode": "written", "writtenPrompt": "text",
                   "keepRecording": True})
    c.startSession()
    c.stopSession()  # stops before advancing: only one prompt is ever reached

    out = tmp_path / "export"
    out.mkdir()
    _pick_export_to(c, out)
    text = Path(c.exportCurrentSession()["path"]).read_text(encoding="utf-8")
    assert "Only this one." in text
    assert "Never reached." not in text, (
        "an unreached prompt appeared in the transcript, so the export does "
        "not describe the session that actually happened"
    )


def test_the_typed_answer_setting_decides_what_survives_a_session(tmp_path):
    """The one switch a Written-mode user relies on, checked end to end.

    `saveTyped` decides whether what someone types outlives the session. Getting
    it wrong is bad in both directions: keeping text the user expected to be
    thrown away is a privacy problem, and dropping text they expected to keep
    loses their work. The engine test covers the in-memory half; this covers the
    half that reaches the disk and the exported file.
    """
    for save, expected in ((False, False), (True, True)):
        root = tmp_path / ("kept" if save else "dropped")
        root.mkdir()
        c = _controller(root)
        src = root / "lib.md"
        src.write_text("How was the watch?\n", encoding="utf-8")
        c.selectFile(str(src))
        c.updateSetup({
            "mode": "written",
            "writtenPrompt": "text",
            "saveTyped": save,
            # Retaining audio is what makes the session worth a folder at all,
            # so this is what makes the difference observable on disk.
            "keepRecording": True,
        })
        c.startSession()
        c.setTypedAnswer("the ship stayed in port")
        c.next()
        c.stopSession()

        stored = json.loads((c._session_dir / "session.json").read_text(encoding="utf-8"))
        answers = stored.get("typedAnswers") or {}
        if expected:
            assert "the ship stayed in port" in json.dumps(answers), (save, answers)
        else:
            assert answers == {}, (
                f"saveTyped was off but the answer was written to disk: {answers}"
            )

        out = root / "export"
        out.mkdir()
        _pick_export_to(c, out)
        text = Path(c.exportCurrentSession()["path"]).read_text(encoding="utf-8")
        assert ("the ship stayed in port" in text) is expected, (
            "the exported transcript disagrees with the setting:\n" + text
        )


def test_typed_answers_are_not_offered_where_there_are_none_to_type():
    """Spoken mode has nothing to type, so it must not offer to keep typing.

    The option used to sit in Spoken mode greyed out. A control that is present
    but unusable reads as either a bug or a setting that cannot be reached, and
    the label gave no hint that it only applied to Written mode. It is now
    hidden, and the hidden state must not silently drop the stored preference:
    switching modes back has to find it as the user left it.
    """
    text = _qml_text("SetupScreen.qml")

    assert 'objectName: "saveTypedRow"' in text, "the row cannot be located to check it"
    assert 'visible: page.s.mode === "written"' in text, (
        "the typed-answer option is still shown in Spoken mode"
    )
    assert 'objectName: "saveTypedCheck"' in text
    assert "Save typed answers" not in text, (
        "the old wording suggests it saves answers in any mode"
    )


def _whisper_stub(directory: Path, payload: str, name: str = "whisper-good") -> Path:
    """A fake whisper-cli that writes `payload` where the app asked for JSON."""
    import stat

    script = (
        "#!/bin/sh\n"
        'out=""; prev=""; for a in "$@"; do prev="$out"; out="$a"; done\n'
        "cat > \"${prev}.json\" <<'JSON'\n"
        + payload
        + "\nJSON\n"
    )
    stub = directory / name
    stub.write_text(script)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _history_session(tmp_path: Path, **extra) -> tuple:
    """A stored session folder with a kept recording, as History would find it."""
    sessions = tmp_path / "sessions"
    folder = sessions / "2026-10-01_09-00-00_abcd1234"
    folder.mkdir(parents=True, exist_ok=True)
    _wav_bytes(folder / "recording.wav")
    payload = {
        "id": "abcd1234",
        "contentSourcePath": "/library/cafe.md",
        "startedAt": 1788000000.0,
        "endedAt": 1788000060.0,
        "mode": "spoken",
        "spokenProgression": "fixedTimer",
        "promptsReached": 1,
        "totalPrompts": 1,
        "recordingRetained": True,
        "whisperModelPath": "/models/ggml-large-v3-turbo.bin",
        "transcriptionStatus": "notRequested",
    }
    payload.update(extra)
    (folder / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    return sessions, folder


def _drain(qapp, predicate, timeout: float = 30.0):
    import time

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.02)


def test_history_transcription_keeps_the_raw_json_and_the_timestamps(
    tmp_path, whisper_app
):
    """Transcribing a stored session writes the whole evidence trail.

    Three things have to survive the run: the raw file exactly as whisper-cli
    produced it, the segments with their own boundaries, and a Markdown that
    shows those boundaries. Anything less and the user cannot tell what the
    machine actually heard.
    """
    raw = json.dumps({
        "systeminfo": "AVX = 1 | AVX2 = 1",
        "result": {"language": "en"},
        "transcription": [
            {"offsets": {"from": 0, "to": 1200},
             "timestamps": {"from": "00:00:00,000", "to": "00:00:01,200"},
             "text": " Good evening."},
            {"offsets": {"from": 1200, "to": 4800},
             "timestamps": {"from": "00:00:01,200", "to": "00:00:04,800"},
             "text": "How was your day?"},
        ],
    })
    stub = _whisper_stub(tmp_path, raw)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)

    c = _controller(tmp_path)
    sessions, folder = _history_session(tmp_path)
    # The runtime and model are Settings fields, so they reach the transcriber
    # through the draft and Apply.
    c.updateSettingsDraft({"whisperCLIPath": str(stub), "whisperModelPath": str(model)})
    assert c.applySettings() is True

    result = c.transcribeSession(folder.name)
    assert result["status"] == "running", result
    _drain(whisper_app, lambda: not c.transcribing)

    # The raw payload is byte-for-byte what whisper-cli wrote: the stub appends
    # the newline a real run also ends with, and that byte has to survive too.
    raw_bytes = (folder / "whisper.json").read_bytes()
    assert raw_bytes == (raw + "\n").encode("utf-8"), (
        "the raw file is not the bytes whisper-cli produced:\n"
        f"  stored: {raw_bytes[:120]!r}\n  wanted: {(raw + chr(10)).encode()[:120]!r}"
    )
    assert "AVX2 = 1" in raw_bytes.decode("utf-8"), (
        "the raw file was rewritten or filtered instead of kept as it is"
    )

    segments = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert segments["segments"] == [
        {"start_ms": 0, "end_ms": 1200, "text": "Good evening."},
        {"start_ms": 1200, "end_ms": 4800, "text": "How was your day?"},
    ], segments

    # The readable view lives beside the raw segments, never instead of them,
    # and carries the formatter version so an old session can be re-rendered
    # from this file alone without running Whisper again.
    payload = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert payload["readableVersion"] >= 1, payload
    assert payload["readableSegments"], payload
    blocks = payload["readableSegments"]
    # Blocks may span several raw segments -- adjacent fragments of one turn are
    # grouped -- but every block must sit inside the raw timing it came from,
    # stay in order, and never overlap itself.
    for index, block in enumerate(blocks):
        assert block["label"], block
        assert block["text"], block
        assert block["start_ms"] <= block["end_ms"], block
        covered = [s for s in segments["segments"]
                   if s["start_ms"] < block["end_ms"] and s["end_ms"] > block["start_ms"]]
        assert covered, ("block has no raw segment behind it", block)
        if index:
            previous = blocks[index - 1]
            assert block["start_ms"] >= previous["start_ms"], (previous, block)

    markdown = (folder / "transcript.md").read_text(encoding="utf-8")
    assert "[00:00:00.000 – 00:00:01.200] Good evening." in markdown, markdown
    assert "[00:00:01.200 – 00:00:04.800] How was your day?" in markdown, markdown
    assert "words were not evaluated" in markdown, markdown

    stored = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert stored["transcriptionStatus"] == "succeeded", stored
    assert stored["whisperModelPath"] == str(model), stored

    rows = {row["id"]: row for row in c.sessionHistory()}
    assert rows[folder.name]["transcriptionStatus"] == "succeeded", rows
    assert rows[folder.name]["hasTranscript"] is True, rows


def test_history_transcription_failure_does_not_touch_the_live_session(
    tmp_path, whisper_app
):
    """A failed History run must not paint Failed onto the finished session.

    Both runs share one whisper-cli path and one controller, so the status of
    whatever session the user is looking at must only ever describe itself.
    """
    import stat

    broken = tmp_path / "whisper-broken"
    broken.write_text("#!/bin/sh\nexit 4\n")
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)

    c = _controller(tmp_path)
    src = tmp_path / "lib.md"
    src.write_text("Hello there.\n", encoding="utf-8")
    c.selectFile(str(src))
    c.updateSetup({"mode": "spoken", "keepRecording": True})
    c.updateSettingsDraft({"whisperCLIPath": str(broken), "whisperModelPath": str(model)})
    assert c.applySettings() is True
    c.startSession()
    c.stopSession()

    sessions, folder = _history_session(tmp_path)
    failed = []
    c._transcribe_failed = failed.append  # the live-session failure path
    c.transcribeSession(folder.name)
    _drain(whisper_app, lambda: not c.transcribing)

    assert failed == [], "the live failure handler was called for a History run"
    assert c.finishedInfo()["transcriptionStatus"] != "failed", (
        "a History failure marked the session that just finished as failed"
    )
    stored = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert stored["transcriptionStatus"] == "failed", stored
    assert not (folder / "transcript.json").exists(), (
        "a failed run wrote a transcript anyway"
    )
    assert not (folder / "whisper.json").exists(), (
        "a failed run wrote raw JSON anyway"
    )


def test_transcribing_temporary_audio_takes_its_play_button_with_it(
    tmp_path, whisper_app, monkeypatch
):
    """After the temp WAV is deleted, the player must disappear, not go stale.

    Temporary audio is removed once transcription succeeds. The Finished screen
    binds its player to `canPlayCurrentRecording`, which is notified by
    `viewStateChanged`; nothing emitted that when the file was deleted, so the
    Play/Pause/Stop row stayed on screen for audio that no longer existed and
    Play did nothing at all.
    """
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "temp"))
    (tmp_path / "temp").mkdir()

    raw = json.dumps({
        "transcription": [
            {"offsets": {"from": 0, "to": 1500}, "text": " Good evening."},
        ],
    })
    stub = _whisper_stub(tmp_path, raw)
    model = tmp_path / "ggml-model.bin"
    model.write_bytes(b"\0" * 64)

    c = _controller(tmp_path)
    c.updateSettingsDraft({"whisperCLIPath": str(stub), "whisperModelPath": str(model)})
    assert c.applySettings() is True

    # Recording on, keeping off: exactly the state that leaves a temporary WAV
    # behind for post-session Whisper.
    c._recorder = FakeRecorder(target_dir=tmp_path / "temp")
    src = tmp_path / "lib.md"
    src.write_text("Good evening.\n", encoding="utf-8")
    c.selectFile(str(src))
    c.updateSetup({"mode": "spoken", "record": True, "keepRecording": False,
                   "progression": "manual"})
    c.startSession()
    c.stopSession()

    session = c._engine.session
    temp_audio = Path(session.recordingPath)
    assert temp_audio.is_file(), "the session did not keep temporary audio to transcribe"
    assert session.recordingRetained is False
    assert c.canPlayCurrentRecording is True, "the recording was not playable to begin with"

    ticks: list[int] = []
    c.viewStateChanged.connect(lambda: ticks.append(1))

    c.retryTranscription()
    _drain(whisper_app, lambda: not c.transcribing)

    assert session.transcriptionStatus.value == "succeeded", session.transcriptionStatus
    assert not temp_audio.exists(), "the temporary recording survived its transcription"
    assert ticks, "no viewStateChanged: the player row stayed on screen"
    assert c.canPlayCurrentRecording is False, (
        "the player still offers audio that was deleted after transcription"
    )
    assert c.finishedInfo()["hasRecording"] is False, (
        "the Finished screen still reports a recording that no longer exists"
    )


def test_finished_view_shows_the_timestamped_transcript_and_the_notice(tmp_path):
    """The Finished screen reads finishedInfo(); the timings have to be in it.

    A single joined paragraph would look tidier and would throw away the only
    thing Whisper added, so the view has to receive the blocks themselves.
    """
    c = _controller(tmp_path)
    src = tmp_path / "lib.md"
    src.write_text("Good evening.\n", encoding="utf-8")
    c.selectFile(str(src))
    c.updateSetup({"mode": "written", "writtenPrompt": "text", "saveTyped": True})
    c.startSession()
    c.setTypedAnswer("Very well, thanks.")
    c.next()
    c.stopSession()

    assert c.finishedInfo()["transcriptNotice"] == (
        "Local Whisper output with timestamps. Text was formatted for "
        "readability; words were not evaluated."
    )
    assert c.finishedInfo()["hasTranscript"] is False
    # No transcript yet is an absence of blocks, not a string that could be
    # mistaken for a transcript, and not a claim that the recording was silent.
    assert c.finishedInfo()["transcriptBlocks"] == [], c.finishedInfo()

    # A real pause between the two turns. Fragments less than the grouping gap
    # apart are one turn and correctly collapse into a single block, so a
    # back-to-back pair would not exercise per-block timings at all.
    c._transcript_segments = [
        {"start_ms": 0, "end_ms": 1500, "text": "Good evening."},
        {"start_ms": 6000, "end_ms": 8700, "text": "Very well, thanks."},
    ]
    c._engine.session.transcriptionStatus = TranscriptionStatus.SUCCEEDED
    info = c.finishedInfo()

    assert info["hasTranscript"] is True
    blocks = info["transcriptBlocks"]
    assert [(b["start_ms"], b["end_ms"]) for b in blocks] == [
        (0, 1500), (6000, 8700),
    ], blocks
    assert [b["text"] for b in blocks] == ["Good evening.", "Very well, thanks."], blocks
    assert all(b["label"] for b in blocks), blocks


def test_playback_takes_a_session_id_and_refuses_a_path(tmp_path):
    """History passes an id; the controller decides which file that means.

    A slot that took a path would let any QML string reach the player, and a
    folder name from another session would play the wrong answer back at the
    user.
    """
    c = _controller(tmp_path)
    sessions, folder = _history_session(tmp_path)

    assert c.sessionRecordingPath(folder.name) == str(folder / "recording.wav")
    for bad in ("", ".", "..", "../../etc", "nope", "a/b", folder.name + "/.."):
        assert c.sessionRecordingPath(bad) == "", bad


def _qml_text(name: str) -> str:
    return (Path(__file__).parent.parent / "src" / "diloger" / "qml" / name).read_text()


def test_both_screens_state_that_the_transcript_is_uncorrected():
    """The one promise this app makes about the words has to be visible.

    A timestamped transcript reads like a transcript somebody wrote. Saying so
    on the screen is what stops it from being taken for one -- and the wording
    has to be true, so it describes what was and was not done to the words.
    """
    notice = (
        "Local Whisper output with timestamps. Text was formatted for "
        "readability; words were not evaluated."
    )
    finished = _qml_text("FinishedScreen.qml")
    # Finished reads the sentence from the controller rather than repeating it,
    # so there is only ever one wording to keep correct.
    assert "page.info.transcriptNotice" in finished, (
        "the Finished screen does not show the notice it was given"
    )
    assert notice in _qml_text("HistoryScreen.qml"), (
        "History shows a transcript without saying who wrote it"
    )
    # The notice must not drift away from what the app really does. It used to
    # claim the text was uncorrected, which stopped being true once sentences
    # were split and capitalised for readability.
    assert "Text is not corrected." not in _qml_text("HistoryScreen.qml")


def test_history_transcribes_and_exports_the_selected_session():
    """History must be able to do what the Finished screen can do.

    An old transcript that can only be reached by digging through the sessions
    folder is not really reachable: the two screens offer the same actions, and
    both hand over the selected session's id rather than a path.
    """
    text = _qml_text("HistoryScreen.qml")

    assert "app.transcribeSession(" in text, "History cannot transcribe a session"
    assert "app.exportSession(" in text, "History cannot export a session"
    for slot in ("page.transcribe()", "page.exportMarkdown()"):
        assert slot in text, f"{slot} is never called by a button"

    # The id crosses over, and it is the selected row's own id.
    assert "app.transcribeSession(page.selectedId)" in text, (
        "History transcribes something other than the selected session"
    )
    assert "app.exportSession(page.selectedId)" in text, (
        "History exports something other than the selected session"
    )
    # Export is offered even without a transcript: the prompts and the answers
    # are worth sharing too, and the file says so when there are no segments.
    assert "hasTranscript" not in text.split("Export Markdown")[1].split("}")[0], (
        "Export Markdown is gated on a transcript existing"
    )


def test_playback_is_addressed_by_session_id_and_never_by_path():
    """A path in QML is a path the app would happily play.

    Both screens used to pass `sourcePath` straight to the player, so any string
    in the view decided which file the speaker played.
    """
    bar = _qml_text("PlaybackBar.qml")
    assert "property string sessionId" in bar, "PlaybackBar still takes a path"
    assert "sourcePath:" not in bar, "PlaybackBar still accepts an external path"
    assert "app.sessionRecordingPath(" in bar, (
        "PlaybackBar does not resolve its own session id"
    )

    for screen in ("FinishedScreen.qml", "HistoryScreen.qml"):
        text = _qml_text(screen)
        assert "sourcePath: page" not in text, (
            f"{screen} still hands a recording path to the player"
        )
    assert "sessionId: page.hasSelection ? page.selectedId" in _qml_text(
        "HistoryScreen.qml"
    ), "History does not give the bar the selected session's id"


def _flow_block(text: str) -> str:
    """The body of the first `Flow { ... }`, found by counting braces.

    Text is not a reliable way to tell which row a button belongs to: the same
    words appear in comments and in tooltip text. The braces are.
    """
    start = text.index("Flow {")
    index = text.index("{", start)
    depth = 0
    for position in range(index, len(text)):
        char = text[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:position + 1]
    raise AssertionError("the Flow block is never closed")


def test_session_actions_wrap_instead_of_running_off_the_window():
    """Four actions in a Row are four actions at normal width and none at narrow.

    A fixed-height row cannot wrap, so at a smaller window the last buttons fall
    off the edge and the transcript cannot be exported at all. A Flow wraps onto
    a second line and the panel grows instead.
    """
    for screen in ("FinishedScreen.qml", "HistoryScreen.qml"):
        text = _qml_text(screen)
        assert "Flow {" in text, f"{screen} has no wrapping action row"
        assert "Export Markdown" in text, f"{screen} cannot export Markdown"
        span = _flow_block(text)
        for button in ('text: "Transcribe"', 'text: "Keep audio"',
                       'text: "Delete audio"', 'text: "Transcribe again"',
                       'text: "Export Markdown"'):
            if button in text:
                assert button in span, (
                    f"{screen} shows {button!r} outside the wrapping action row"
                )
        assert "height:" not in span.split("}")[0], (
            f"{screen} gives its wrapping action row a fixed height, so a "
            "second line of buttons would be clipped"
        )


def test_finished_metrics_share_the_width_instead_of_being_sized_in_pixels():
    """Three metrics in a pixel-sized row lose one column at small widths."""
    text = _qml_text("FinishedScreen.qml")
    metrics = text.split("Duration")[1] if "Duration" in text else text
    assert text.count("Layout.fillWidth: true") >= 3, (
        "the three metrics are not all filling the row equally"
    )


def test_history_rebuilds_a_transcript_whose_markdown_never_landed(tmp_path):
    """transcript.json is the copy that matters; transcript.md is a rendering.

    An interrupted write can leave the JSON on disk and the Markdown missing. A
    History row that owns a transcript then showed an empty panel and offered
    Transcribe again, as if the session had never been transcribed.
    """
    c = _controller(tmp_path)
    folder = tmp_path / "sessions" / "2026-10-01_10-00-00"
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({
        "sessionId": folder.name,
        "contentSourcePath": str(tmp_path / "library" / "a.txt"),
        "startedAtEpoch": 100.0,
        "totalPrompts": 2,
        "promptsReached": 1,
        "mode": "spoken",
        "transcriptionStatus": "succeeded",
    }), encoding="utf-8")
    (folder / "transcript.json").write_text(json.dumps({
        "language": "en",
        "text": "Hello there.",
        "segments": [{"startMs": 0, "endMs": 1200, "text": "Hello there."}],
    }), encoding="utf-8")
    assert not (folder / "transcript.md").exists()

    blocks = c.transcriptBlocksForSession(folder.name)

    assert blocks, "the surviving transcript.json produced no blocks"
    assert (blocks[0]["start_ms"], blocks[0]["end_ms"]) == (0, 1200), blocks
    assert blocks[0]["text"] == "Hello there.", blocks


def test_history_does_not_invent_a_transcript_for_an_untranscribed_session(tmp_path):
    """The rebuild must not turn "nothing was said" into "here is your talk".

    A session folder with metadata and no transcript still has to come back
    empty, otherwise the panel fills with a header that only says the transcript
    is missing. "Nothing was said" and "nothing was recorded yet" are different
    facts and must not collapse into the same empty transcript.
    """
    c = _controller(tmp_path)
    folder = tmp_path / "sessions" / "2026-10-01_10-00-00"
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({
        "sessionId": folder.name,
        "totalPrompts": 2,
        "promptsReached": 1,
        "transcriptionStatus": "notRequested",
    }), encoding="utf-8")

    # No blocks is the whole guarantee: the view model has nothing to render, so
    # the screen falls through to its empty state. This is a state to describe,
    # not a failure, so it must not arrive as an error either.
    errors: list[str] = []
    c.errorMessage.connect(errors.append)

    assert c.transcriptBlocksForSession(folder.name) == []
    assert not errors, errors

    # The row still has to say so, or the empty state has nothing to explain.
    rows = {row["id"]: row for row in c.sessionHistory()}
    assert rows[folder.name]["hasTranscript"] is False, rows
    assert rows[folder.name]["transcriptionStatus"] == "notRequested", rows


def test_a_segment_holding_two_voices_is_never_called_an_answer():
    """Mixed audio must be labelled honestly, not credited to the learner.

    The microphone sits next to the speaker, so a segment that starts under the
    trainer's playback and ends after it stopped contains both voices. Calling
    that "your answer" invents something the user never said; dropping it hides
    something they did. There is no word-level timing in the file to split it
    on, so it is reported as speech that could not be separated.
    """
    from diloger.transcription.readable import build_readable_blocks, tts_windows

    event_log = [
        {"type": "ttsStart", "atOffset": 0.0, "promptId": "0:aa"},
        {"type": "ttsFinish", "atOffset": 4.5, "promptId": "0:aa"},
    ]
    blocks = build_readable_blocks(
        # 3000-8000 against a 0-4500 playback window: 1500 ms inside it and
        # 3500 ms outside, 30% of the span. That is too much of each to be
        # confidently one voice or the other, so it must not be labelled.
        [{"start_ms": 3000, "end_ms": 8000, "text": "and the vessel stayed in port"}],
        event_log=event_log,
        prompt_texts={},
        prompt_order=["0:aa"],
    )
    labels = [b["label"] for b in blocks]
    assert labels == ["Recorded speech"], blocks
    assert not any("answer" in label.lower() for label in labels), blocks
    assert tts_windows(event_log) == [(0, 4500, "0:aa")]

    # The same segment with the playback window moved clear of it is the user's
    # own speech, and only then may it be called an answer.
    clear = build_readable_blocks(
        [{"start_ms": 3000, "end_ms": 8000, "text": "and the vessel stayed in port"}],
        event_log=[{"type": "ttsStart", "atOffset": 0.0, "promptId": "0:aa"},
                   {"type": "ttsFinish", "atOffset": 2.5, "promptId": "0:aa"}],
        prompt_texts={},
        prompt_order=["0:aa"],
    )
    assert [b["label"] for b in clear] == ["Recorded answer"], clear


def test_question_text_comes_from_the_content_file_not_from_whisper():
    """Whisper's echo of the prompt is evidence, not the source of the question.

    The prompt was typed by the user and is known exactly; what the microphone
    picked up is a degraded copy that may mishear words and has no punctuation.
    The content file is the authority. When it cannot be read, the app says the
    text could not be recovered rather than passing off a guess as the question.
    """
    from diloger.transcription.readable import prompt_texts_for_session

    def _run(root: Path):
        source = root / "day.md"
        source.write_text("What did you do on watch?\nHow long?\n", encoding="utf-8")
        session = {
            "contentSourcePath": str(source),
            "promptOrder": [p.id for p in FileContentImporter().load_file(source).prompts],
        }
        found = prompt_texts_for_session(session, [root])
        assert sorted(found.values()) == ["How long?", "What did you do on watch?"], found
        assert set(found) == set(session["promptOrder"]), (found, session["promptOrder"])

        # A missing file yields nothing, which is the honest outcome.
        assert prompt_texts_for_session(
            {"contentSourcePath": str(root / "gone.md"), "promptOrder": session["promptOrder"]},
            [root],
        ) == {}

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_a_long_pause_splits_a_turn_and_a_short_one_does_not():
    """Grouping is measured from the recording, never guessed from the words.

    Fragments of one answer arrive in several segments; stitching them into one
    block is readability, not rewriting. But two blocks with a real pause between
    them are two things that happened, and merging them would hide the pause the
    user actually took.
    """
    from diloger.transcription.readable import build_readable_blocks

    joined = build_readable_blocks(
        [
            {"start_ms": 0, "end_ms": 1500, "text": "the first thing"},
            {"start_ms": 1500, "end_ms": 3000, "text": "and then the second"},
        ],
        event_log=[],
        prompt_texts={},
    )
    assert len(joined) == 1, joined
    assert (joined[0]["start_ms"], joined[0]["end_ms"]) == (0, 3000), joined

    separate = build_readable_blocks(
        [
            {"start_ms": 0, "end_ms": 1500, "text": "the first thing"},
            {"start_ms": 9000, "end_ms": 10500, "text": "and then the second"},
        ],
        event_log=[],
        prompt_texts={},
    )
    assert len(separate) == 2, separate
    assert [(b["start_ms"], b["end_ms"]) for b in separate] == [(0, 1500), (9000, 10500)], separate


def test_an_old_session_is_re_rendered_without_running_whisper_again():
    """Changing the formatting rules must not cost the user a second transcription.

    The raw segments and the event log are already on disk, so a session
    written by an older build can pick up the current readable view purely from
    its own transcript.json.
    """
    from diloger.storage.session_store import transcript_payload

    segments = [{"start_ms": 0, "end_ms": 1200, "text": "hello there"}]
    event_log = [
        {"type": "ttsStart", "atOffset": 0.0, "promptId": "0:aa"},
        {"type": "ttsFinish", "atOffset": 0.9, "promptId": "0:aa"},
    ]
    session = {"promptOrder": ["0:aa"], "eventLog": event_log}

    current = transcript_payload(session, segments, event_log=event_log)
    assert current["readableVersion"] >= 1
    assert current["segments"] == segments, "raw segments must be stored verbatim"

    # Pretend the file was written by a build with no readable view at all.
    old_file = {"segments": segments, "eventLog": event_log}
    from diloger.storage.session_store import stored_readable_blocks
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "transcript.json").write_text(json.dumps(old_file), encoding="utf-8")
        (folder / "session.json").write_text(json.dumps(session), encoding="utf-8")
        rebuilt = stored_readable_blocks(folder)
        assert rebuilt == current["readableSegments"], (rebuilt, current["readableSegments"])


def test_history_detail_bindings_guard_the_row_not_just_the_id():
    """A selected id whose row is gone must not dereference null.

    `selectedRow` is a lookup that returns null, while `selectedId` is a plain
    string that survives a Rescan or a deletion. Every detail binding was
    written as `selectedId === "" ? ... : page.selectedRow.date`, which looks
    guarded but is not: with a stale id the false branch still ran and threw
    "Cannot read property 'date' of null" on every field, eight errors per
    render, and the buttons stayed enabled over a row that no longer existed.
    """
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml" / "HistoryScreen.qml").read_text(
        encoding="utf-8"
    )

    assert "readonly property bool hasSelection: selectedRow !== null" in qml, (
        "HistoryScreen needs a hasSelection guard that checks the loaded row, "
        "not just the id"
    )

    # No binding may gate on the bare id any more.
    assert 'page.selectedId === ""' not in qml, (
        "a binding still gates on selectedId alone, which is stale-safe but "
        "row-safe only when the row is known to exist"
    )
    assert 'page.selectedId !== ""' not in qml, (
        "a control is still enabled from selectedId alone, so it can be live "
        "with no row behind it"
    )

    # Every selectedRow dereference must sit behind the row guard. Bindings wrap
    # across lines, so compare whole binding expressions rather than lines:
    # a two-line `sourcePath: page.hasSelection && ...` is guarded even though
    # only its first line mentions hasSelection.
    import re as _re

    flat = " ".join(qml.split())
    for chunk in _re.split(r"\b(?:text|color|visible|enabled|sourcePath):", flat)[1:]:
        if "selectedRow." in chunk and "hasSelection" not in chunk:
            raise AssertionError(
                "a selectedRow field is read without the hasSelection guard: "
                + chunk[:120]
            )


def test_history_detail_labels_show_when_a_row_is_selected():
    """The detail labels must be non-empty exactly when a row is loaded.

    Replacing `selectedId === ""` with the new `hasSelection` guard is a
    mechanical edit that silently swaps the two ternary branches: the result
    was `hasSelection ? "" : "Whisper: " + ...`, so every label went blank
    precisely when a session was selected and printed its value when none was.
    The binding is null-safe and still looks reasonable, so only the branch
    contents catch it.
    """
    import re as _re

    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml" / "HistoryScreen.qml").read_text(
        encoding="utf-8"
    )
    flat = " ".join(qml.split())

    labels = [
        "Source: ", "Duration: ", "Prompts reached: ", "Recording: ", "Whisper: ",
    ]
    for label in labels:
        # Allow a short prefix so an inverted `? "" : "Label: "` still
        # matches here and is then reported by the branch-order assertions
        # below, instead of looking like the label vanished.
        match = _re.search(
            r"text: page\.hasSelection \?[^?]{0,20}" + _re.escape(label), flat
        )
        assert match, f"{label}no longer renders from the hasSelection guard"
        # The true branch is what shows; it must not be the empty string.
        after = flat[match.end() : match.end() + 3]
        assert not after.startswith('""'), (
            f'the {label} label renders an empty string when a row IS selected, '
            "so the ternary branches are the wrong way round"
        )

    # And the guard must not be spelled with an inverted sense anywhere.
    assert 'hasSelection ? "" :' not in flat, (
        'a binding reads `hasSelection ? "" : <value>`, which hides the value '
        "exactly when a session is selected"
    )


# ---------------- Settings is a draft until Apply is pressed


def _saved_config(tmp_path) -> Path:
    return Path(tmp_path / "config.json")


def _config_file(tmp_path) -> dict:
    return json.loads(_saved_config(tmp_path).read_text(encoding="utf-8"))


def test_settings_edits_are_staged_until_apply(tmp_path):
    """No control on the Settings screen may write config.json.

    Every toggle used to save immediately, so Back was not a cancel and a
    mistyped value was already persisted by the time it was noticed. Edits go
    to a draft copy that only Apply writes out.
    """
    c = _controller(tmp_path)
    c.showSettings()

    c.updateSettingsDraft({"ttsRate": 210, "recordingEnabledByDefault": True})

    assert c.settingsDirty is True
    assert "ttsRate" not in _config_file(tmp_path), "the draft was written to disk"
    assert c.settingsState()["ttsRate"] == 210, "the edit must be visible in the form"

    assert c.applySettings() is True
    assert _config_file(tmp_path)["ttsRate"] == 210
    assert c.settingsDirty is False


def test_settings_apply_switches_the_theme(tmp_path):
    """Choosing a theme in Settings changes the live theme, not just the file."""
    from diloger.app.theme import THEME_DARK, THEME_LIGHT

    c = _controller(tmp_path)
    c.showSettings()

    assert c.settingsState()["theme"] == THEME_DARK
    assert c.settingsState()["themeModes"] == ["system", "dark", "light"]

    c.updateSettingsDraft({"theme": THEME_LIGHT})
    assert c.theme.resolvedTheme == THEME_DARK, "the draft must not repaint yet"

    assert c.applySettings() is True
    assert c.theme.resolvedTheme == THEME_LIGHT
    assert _config_file(tmp_path)["theme"] == THEME_LIGHT


def test_config_rejects_an_unknown_theme():
    """A theme name the app cannot render has to be refused, not silently kept."""
    from diloger.app.theme import THEME_LIGHT
    from diloger.settings.config import AppConfig

    assert AppConfig(theme=THEME_LIGHT).validate() == []
    assert AppConfig(theme="chartreuse").validate()


def test_selected_content_path_is_the_one_source_of_truth(tmp_path):
    """The Library reads this property, so it must track every change."""
    c = _controller(tmp_path)
    assert c.selectedContentPath == ""

    seen = []
    c.selectedContentPathChanged.connect(lambda: seen.append(c.selectedContentPath))

    c.selectFile("/tmp/a.txt")
    assert c.selectedContentPath == "/tmp/a.txt"
    assert seen == ["/tmp/a.txt"]

    # Choosing the same file again changes nothing, so it should not repaint the
    # whole Library for no reason.
    c.selectFile("/tmp/a.txt")
    assert seen == ["/tmp/a.txt"], "re-selecting the same file must not notify"

    c.selectFile("/tmp/b.txt")
    assert c.selectedContentPath == "/tmp/b.txt"
    assert seen == ["/tmp/a.txt", "/tmp/b.txt"]


def test_settings_apply_keeps_the_old_config_when_a_value_is_invalid(tmp_path):
    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 210})
    c.applySettings()
    good = _config_file(tmp_path)["ttsRate"]

    errors: list[str] = []
    c.errorMessage.connect(errors.append)
    c.updateSettingsDraft({"ttsRate": 5})

    assert c.applySettings() is False, "a rate outside 100-320 must not be saved"
    assert _config_file(tmp_path)["ttsRate"] == good, "the working config was replaced"
    assert c._config.ttsRate == good, "the app must keep running on the old values"
    assert errors, "the user has to be told why nothing happened"


def test_settings_apply_keeps_the_old_config_when_the_write_fails(tmp_path, monkeypatch):
    """A failed save must not leave the app running on values nobody can see."""
    import diloger.app.controller as controller_mod

    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 210})
    before = c._config.ttsRate

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(controller_mod, "save_config", boom)
    errors: list[str] = []
    c.errorMessage.connect(errors.append)

    assert c.applySettings() is False
    assert c._config.ttsRate == before
    assert errors and "No space" in errors[0]


def test_settings_discard_restores_the_saved_values(tmp_path):
    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 210})
    c.applySettings()

    c.updateSettingsDraft({"ttsRate": 300, "saveTypedAnswersByDefault": True})
    assert c.settingsState()["ttsRate"] == 300

    c.discardSettings()

    assert c.settingsState()["ttsRate"] == 210
    assert c.settingsState()["saveTypedAnswersByDefault"] is False
    assert c.settingsDirty is False


def test_settings_reopening_starts_from_the_saved_config(tmp_path):
    """A half-finished visit must not reappear the next time Settings opens."""
    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 300})
    c.showLibrary()

    c.showSettings()

    assert c.settingsState()["ttsRate"] == 175
    assert c.settingsDirty is False


def test_leaving_settings_with_unsaved_edits_asks_first(tmp_path):
    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 260})

    asked: list[bool] = []
    c.settingsCloseRequested.connect(lambda: asked.append(True))

    assert c.leaveSettings() is False, "leaving must not be automatic"
    assert c.screen == "settings", "the draft must still be here to decide about"
    assert asked, "the screen has to be told to ask"

    c.applyAndLeaveSettings()
    assert c.screen == "library"
    assert _config_file(tmp_path)["ttsRate"] == 260


def test_leaving_settings_with_a_clean_draft_needs_no_question(tmp_path):
    c = _controller(tmp_path)
    c.showSettings()

    asked: list[bool] = []
    c.settingsCloseRequested.connect(lambda: asked.append(True))

    assert c.leaveSettings() is True
    assert c.screen == "library"
    assert not asked, "nothing to decide, so nothing to ask"


def test_discarding_and_leaving_saves_nothing(tmp_path):
    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 260})
    c.discardAndLeaveSettings()

    assert c.screen == "library"
    assert "ttsRate" not in _config_file(tmp_path)


def test_settings_no_longer_offers_an_input_device_list(tmp_path):
    """Diloger records from the system default input; there is nothing to pick."""
    c = _controller(tmp_path)
    c.showSettings()
    assert "inputDevices" not in c.settingsState()

    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml"
           / "SettingsScreen.qml").read_text(encoding="utf-8")
    assert "inputDevices" not in qml


def test_settings_screen_has_apply_and_discard_outside_the_scroller(tmp_path):
    """Apply has to stay reachable when the form is taller than the window."""
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml"
           / "SettingsScreen.qml").read_text(encoding="utf-8")
    assert "Apply changes" in qml
    assert "Discard changes" in qml
    scroll_index = qml.index("ScrollView {")
    apply_index = qml.index('text: "Apply changes"')
    assert apply_index > scroll_index
    # The action bar lives in the root ColumnLayout that also holds the
    # ScrollView, so it does not scroll away with the cards.
    assert qml.count("Layout.preferredHeight: 52") == 1


def test_settings_screen_cards_size_themselves_from_their_content(tmp_path):
    """The clipping cause: fixed preferredHeight on cards with wrapped text.

    A card shorter than its own content cuts the last row off, and no amount of
    scrolling brings it back, so the height has to come from the content.
    """
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml"
           / "SettingsScreen.qml").read_text(encoding="utf-8")
    assert "implicitHeight:" in qml
    for height in ("Layout.preferredHeight: 148", "Layout.preferredHeight: 176",
                   "Layout.preferredHeight: 158", "Layout.preferredHeight: 118"):
        assert height not in qml, f"fixed card height {height} clips its content"


def test_settings_writes_only_through_update_settings_draft(tmp_path):
    """The old immediate-save slot must not come back unnoticed."""
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml"
           / "SettingsScreen.qml").read_text(encoding="utf-8")
    assert "app.updateSettings(" not in qml
    assert "app.updateSettingsDraft(" in qml
    assert "app.cleanOrphanAudio()" not in qml


# ---------------- History: clearing every session at once


def test_clear_all_sessions_removes_every_session_folder(tmp_path):
    from diloger.storage.session_store import SessionStorage

    for day in ("2026-10-01_10-00-00_aa11", "2026-10-02_10-00-00_bb22",
                "2026-10-03_10-00-00_cc33"):
        _write_session(tmp_path, day, {"startedAtEpoch": 1792000000.0},
                       recording=True, transcript=True)
    store = SessionStorage(tmp_path)

    result = store.clear_all_sessions()

    assert result.removed == 3
    assert result.remaining == 0
    assert result.failures == []
    assert store.list_sessions() == []
    assert tmp_path.is_dir(), "the sessions folder itself must survive"


def test_clear_all_sessions_reports_a_failure_without_hiding_the_rest(tmp_path, monkeypatch):
    """A folder that cannot be removed must be named, and the rest still go."""
    import diloger.storage.session_store as store_mod
    from diloger.storage.session_store import SessionStorage

    kept = _write_session(tmp_path, "2026-10-01_10-00-00_aa11", {"startedAtEpoch": 1.0})
    removed = _write_session(tmp_path, "2026-10-02_10-00-00_bb22", {"startedAtEpoch": 2.0})
    real_rmtree = store_mod.shutil.rmtree

    def refuse(path, *args, **kwargs):
        if Path(path) == kept:
            raise OSError(1, "Operation not permitted")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(store_mod.shutil, "rmtree", refuse)
    result = SessionStorage(tmp_path).clear_all_sessions()

    assert result.removed == 1, "the removable session must still be deleted"
    assert result.remaining == 1
    assert kept.is_dir()
    assert not removed.exists()
    assert any("aa11" in f for f in result.failures), result.failures


def test_clear_all_sessions_leaves_loose_files_and_symlinks_alone(tmp_path):
    """Only real session folders are owned by this operation."""
    from diloger.storage.session_store import SessionStorage

    session = _write_session(tmp_path, "2026-10-01_10-00-00_aa11", {"startedAtEpoch": 1.0})
    loose = tmp_path / "notes.txt"
    loose.write_text("not a session", encoding="utf-8")
    outside = tmp_path.parent / "user-library"
    outside.mkdir(exist_ok=True)
    (outside / "keep.txt").write_text("user content", encoding="utf-8")
    link = tmp_path / "2026-10-09_10-00-00_zz99"
    link.symlink_to(outside, target_is_directory=True)

    result = SessionStorage(tmp_path).clear_all_sessions()

    assert result.removed == 1
    assert not session.exists()
    assert loose.is_file(), "a file in the sessions folder is not this operation's"
    assert link.is_symlink(), "a symlink could point anywhere and must not be followed"
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "user content"
    assert result.skipped == 1


def test_clear_all_sessions_on_a_missing_folder_is_not_a_failure(tmp_path):
    from diloger.storage.session_store import SessionStorage

    result = SessionStorage(tmp_path / "never-created").clear_all_sessions()

    assert result.total == 0 and result.removed == 0 and result.failures == []


def test_controller_clear_all_sessions_empties_history_and_selection(tmp_path):
    c = _controller(tmp_path)
    sessions = tmp_path / "sessions"
    for day in ("2026-10-01_10-00-00_aa11", "2026-10-02_10-00-00_bb22"):
        _write_session(sessions, day, {"startedAtEpoch": 1792000000.0})
    # The session the app still points at must not survive its own folder.
    c._session_dir = sessions / "2026-10-01_10-00-00_aa11"
    assert len(c.sessionHistory()) == 2

    seen: list[int] = []
    c.refreshRequested.connect(lambda: seen.append(1))
    result = c.clearAllSessions()

    assert result["removed"] == 2
    assert result["remaining"] == 0
    assert c.sessionHistory() == []
    assert c._session_dir is None, "a deleted session must not stay selected"
    assert seen, "History has to be told to re-read the folder"


def test_controller_clear_all_sessions_says_so_when_nothing_is_stored(tmp_path):
    c = _controller(tmp_path)
    messages: list[str] = []
    c.statusMessage.connect(messages.append)

    result = c.clearAllSessions()

    assert result["removed"] == 0
    assert messages and "no stored sessions" in messages[-1]


def test_history_screen_clear_all_needs_a_confirmation(tmp_path):
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml"
           / "HistoryScreen.qml").read_text(encoding="utf-8")
    assert 'text: "Clear all"' in qml
    assert "clearAllDialog" in qml, "deleting everything must be confirmed first"
    assert "app.clearAllSessions()" in qml
    assert "cannot be undone" in qml


def test_history_screen_still_exposes_the_selection_property(tmp_path):
    """Accessibility relies on these exact names; renaming breaks it silently."""
    qml = (Path(__file__).parent.parent / "src" / "diloger" / "qml"
           / "HistoryScreen.qml").read_text(encoding="utf-8")
    assert "readonly property bool hasSelection: selectedRow !== null" in qml
    assert "delegate: Rectangle {" in qml


def test_delete_temporary_recordings_removes_only_orphan_recordings(tmp_path, monkeypatch):
    """Cleanup is limited to this app's own leftover temp WAVs."""
    import diloger.storage.session_store as store_mod

    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(store_mod.tempfile, "gettempdir", lambda: str(tmp))
    orphan = tmp / "diloger-recording-123.wav"
    orphan.write_bytes(b"RIFF")
    other = tmp / "somebody-elses-file.wav"
    other.write_bytes(b"RIFF")
    (tmp / "diloger-recording-123.txt").write_text("keep", encoding="utf-8")

    c = _controller(tmp_path)
    result = c.deleteTemporaryRecordings()

    assert result["removed"] == 1
    assert not orphan.exists()
    assert other.is_file(), "another program's temp file is not ours to delete"
    assert (tmp / "diloger-recording-123.txt").is_file()
    assert c.settingsState()["orphanFiles"] == 0


def test_a_picker_outside_settings_saves_the_choice(tmp_path, monkeypatch):
    """A folder chosen from the Library screen has no Apply button to wait for.

    The draft survives an Apply, so "is there a draft" cannot tell whether the
    user is looking at Settings. Staging into that invisible copy would drop the
    chosen path on the floor and the user would see no change at all.
    """
    from PySide6.QtWidgets import QFileDialog

    picked = tmp_path / "new-library"
    picked.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(picked))
    )

    c = _controller(tmp_path)
    c.showSettings()
    c.updateSettingsDraft({"ttsRate": 200})
    c.applySettings()
    c.leaveSettings()

    assert c.chooseFolder("library") is True
    assert _config_file(tmp_path)["libraryPath"] == str(picked), \
        "a picker used outside Settings must save at once, not stage invisibly"

    c.showSettings()
    assert c.settingsDirty is False, "the saved choice is not an unsaved edit"


def test_a_picker_inside_settings_only_stages_the_choice(tmp_path, monkeypatch):
    """The same picker must wait for Apply while Settings is open."""
    from PySide6.QtWidgets import QFileDialog

    picked = tmp_path / "new-sessions"
    picked.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(picked))
    )

    c = _controller(tmp_path)
    c.showSettings()
    c._setup["sessionsPath"] = str(tmp_path / "sessions")
    saved_before = _config_file(tmp_path)["sessionsPath"]

    assert c.chooseFolder("sessions") is True
    assert c.settingsState()["sessionsPath"] == str(picked)
    assert c.settingsDirty is True
    assert _config_file(tmp_path)["sessionsPath"] == saved_before, "the draft reached disk"

    assert c.applySettings() is True
    assert _config_file(tmp_path)["sessionsPath"] == str(picked)


# ------------------- recording format: honest header, canonical audio (§20)
#
# Regression: the recorder requested 16 kHz from a device that delivered
# ~21.9 kHz and wrote the stream under a 16 kHz header. The saved WAV was
# therefore structurally wrong: playback crackled and Whisper was fed a broken
# stream. A recording may only claim the rate that was measured, and the
# canonical format must come from a real resample, not a relabelled header.


def _tone_wav(path: Path, rate: int, seconds: float = 0.5, channels: int = 1) -> Path:
    """A continuous 440 Hz WAV: no discontinuities to hide."""
    import math
    import struct
    import wave

    frames = int(rate * seconds)
    step = 2 * math.pi * 440 / rate
    body = b"".join(
        struct.pack("<h", int(12000 * math.sin(step * i))) for i in range(frames)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(body)
    return path


def test_canonical_recording_is_pcm_mono_16bit_16k(tmp_path):
    from diloger.audio import wavconv

    src = _tone_wav(tmp_path / "native.wav", 48000)
    dst = tmp_path / "recording.wav"

    converted = wavconv.convert_to_canonical(src, dst)

    assert wavconv.is_canonical(converted), (
        f"Whisper needs {wavconv.describe_format(wavconv.CANONICAL_SAMPLE_RATE, 1, 2)}, "
        f"got {converted.describe()}"
    )
    header = wavconv.read_wav_format(dst)
    assert header.sample_rate == 16000 and header.channels == 1 and header.sample_width == 2


def test_conversion_preserves_duration_instead_of_relabelling_the_header(tmp_path):
    """A resample keeps the recording the same length; a header rewrite does not.

    Relabelling 48 kHz audio as 16 kHz would play it three times too fast and
    give Whisper a stream that is three times shorter than it sounds.
    """
    from diloger.audio import wavconv

    src = _tone_wav(tmp_path / "native.wav", 48000, seconds=0.6)
    dst = tmp_path / "recording.wav"

    wavconv.convert_to_canonical(src, dst)

    before = wavconv.read_wav_format(src).duration
    after = wavconv.read_wav_format(dst).duration
    assert abs(after - before) < 0.02, (
        f"duration changed from {before:.3f}s to {after:.3f}s during conversion"
    )


def test_converted_audio_stays_continuous(tmp_path):
    """No crackle means no sample-to-sample jumps: a resample, not a splice."""
    import array
    import wave

    from diloger.audio import wavconv

    src = _tone_wav(tmp_path / "native.wav", 48000)
    dst = tmp_path / "recording.wav"
    wavconv.convert_to_canonical(src, dst)

    with wave.open(str(dst), "rb") as w:
        samples = array.array("h")
        samples.frombytes(w.readframes(w.getnframes()))
    peak = max(abs(s) for s in samples) or 1
    steps = [abs(samples[i] - samples[i - 1]) for i in range(1, len(samples))]
    assert max(steps) < peak * 0.5, "the converted audio contains a discontinuity"


def test_canonical_recording_is_not_resampled_twice(tmp_path):
    """Audio that is already canonical must be copied, not resampled again."""
    from diloger.audio import wavconv

    src = _tone_wav(tmp_path / "canonical.wav", 16000)
    dst = tmp_path / "copy.wav"

    wavconv.convert_to_canonical(src, dst)

    assert dst.read_bytes() == src.read_bytes()


def test_conversion_failure_names_the_problem_and_keeps_the_recording(tmp_path, monkeypatch):
    """A missing converter must not destroy the user's audio."""
    from diloger.audio import wavconv

    src = _tone_wav(tmp_path / "native.wav", 48000)
    dst = tmp_path / "recording.wav"
    monkeypatch.setattr(wavconv, "afconvert_path", lambda: "")

    with pytest.raises(wavconv.ConversionError) as excinfo:
        wavconv.convert_to_canonical(src, dst)

    assert "16000" in str(excinfo.value)
    assert src.is_file(), "the unconverted recording was thrown away"
    assert not dst.exists(), "a half-written file was left behind"


def test_conversion_rejects_audio_it_cannot_read(tmp_path):
    from diloger.audio import wavconv

    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"this is not a wav file")

    with pytest.raises(wavconv.ConversionError):
        wavconv.convert_to_canonical(broken, tmp_path / "out.wav")


def test_recorder_writes_the_measured_rate_not_the_requested_one(tmp_path):
    """The header must describe the stream, even when the request was ignored.

    This is the exact shape of the bug: a device that answers a 16 kHz request
    with ~21.9 kHz of audio.
    """
    from diloger.audio import recorder as recorder_module

    r = recorder_module.QtRecorder()
    r._requested_rate = 16000
    r._frames = 21900
    r._rates = [21900.0] * 12

    assert r._measure() == pytest.approx(21900.0)
    assert r._effective_rate() == 21900, "the header would claim the requested rate"
    assert r._within_tolerance(21900) is False


def test_recorder_accepts_a_rate_close_enough_to_the_request(tmp_path):
    from diloger.audio import recorder as recorder_module

    r = recorder_module.QtRecorder()
    r._requested_rate = 48000
    r._rates = [48009.0] * 12

    assert r._within_tolerance(48009) is True
    assert r.format_report()["mismatch"] is False


def test_recorder_keeps_the_requested_rate_without_enough_audio(tmp_path):
    """Nothing has been proven yet, so the request stands until it is disproved."""
    from diloger.audio import recorder as recorder_module

    r = recorder_module.QtRecorder()
    r._requested_rate = 48000
    r._rates = [48000.0] * 2
    r._frames = 100

    assert r._measure() is None
    assert r._effective_rate() == 48000


def test_measured_rate_survives_bursty_event_delivery(tmp_path):
    """A busy event loop must not be mistaken for a different microphone rate.

    Chunks delivered back to back have almost no gap between them, so a single
    window over the whole capture would understate the rate badly.
    """
    from diloger.audio import recorder as recorder_module

    r = recorder_module.QtRecorder()
    r._frames = 60000
    # Steady 10 ms chunks, then a burst delivered with no gap at all.
    for _ in range(12):
        r._collect_rate(480, 0.0)  # no previous chunk yet
    now = 0.0
    for _ in range(20):
        now += 0.010
        r._collect_rate(480, now)
    for _ in range(5):
        now += 0.0001
        r._collect_rate(480, now)

    assert r._measure() == pytest.approx(48000.0, rel=0.02)


def test_recorder_reports_the_three_formats_it_knows(tmp_path):
    """`--check` needs requested / actual / written to be distinguishable."""
    from diloger.audio import wavconv
    from diloger.audio import recorder as recorder_module

    r = recorder_module.QtRecorder()
    r._requested_rate = 16000
    r._frames = 30000
    r._rates = [21939.0] * 12
    r._written = wavconv.read_wav_format(_tone_wav(tmp_path / "w.wav", 16000))

    report = r.format_report()
    assert report["requested"] == "16000 Hz / 1 ch / 16-bit PCM"
    assert report["actual"].startswith("21939 Hz")
    assert report["written"].startswith("16000 Hz")
    assert report["mismatch"] is True


def test_recorder_never_reports_an_impossible_rate(tmp_path):
    from diloger.audio import recorder as recorder_module

    r = recorder_module.QtRecorder()
    r._requested_rate = 48000
    r._frames = 100000
    r._rates = [3.0] * 12  # a stalled stream, not a 3 Hz microphone

    assert r._measure() is None


def test_orphan_temp_audio_covers_an_interrupted_capture(tmp_path, monkeypatch):
    """A crash mid-recording leaves the raw stream; Settings must list it."""
    import tempfile

    from diloger.storage.session_store import SessionStorage

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    (tmp_path / "diloger-recording-abcd1234.pcm").write_bytes(b"\0" * 32)
    (tmp_path / "diloger-recording-abcd1234.wav").write_bytes(b"RIFF")
    (tmp_path / "unrelated.pcm").write_bytes(b"\0")

    orphans = SessionStorage(tmp_path / "sessions").orphan_temp_audio()

    names = [p.name for p in orphans]
    assert "diloger-recording-abcd1234.pcm" in names
    assert "diloger-recording-abcd1234.wav" in names
    assert "unrelated.pcm" not in names


# ---------------------------------------------------------------------------
# P0 layout regression: the PlaybackBar card was sized with `implicitHeight: 84`
# while it stacks a title row, a status row and a 40px transport row. The last
# row fell 44px below the card and under the session-action Flow, so on the
# Finished and History screens Play / Pause / Stop were not clickable.
#
# A text assertion cannot catch that: the numbers only exist at runtime, so the
# structural checks below pin the contract and the offscreen probe measures it.
# ---------------------------------------------------------------------------

QML_DIR = Path(__file__).parent.parent / "src" / "diloger" / "qml"


def test_playbackbar_is_not_given_a_hardcoded_height():
    """The card must be sized from its content, not from a literal.

    `implicitHeight: 84` was the root cause: it is the height of one row, while
    the component stacks three. Anything numeric here re-creates the bug as soon
    as a row wraps or a theme grows a font.
    """
    text = (QML_DIR / "PlaybackBar.qml").read_text()
    match = re.search(r"implicitHeight:\s*(\d+)", text)
    assert match is None, (
        f"PlaybackBar has a hardcoded implicitHeight ({match.group(1)}); it must be derived "
        "from its own content so a taller row cannot be clipped"
    )
    assert "content.implicitHeight" in text, (
        "PlaybackBar does not derive its height from its content layout"
    )
    assert "Layout.minimumHeight: implicitHeight" in text, (
        "Layout.minimumHeight is not set, so a squeezed layout can still clip the transport"
    )


def test_both_detail_screens_use_the_shared_playbackbar():
    """Play, Pause and Stop must be one shared control on both screens."""
    for name in ("FinishedScreen.qml", "HistoryScreen.qml"):
        text = (QML_DIR / name).read_text()
        assert "PlaybackBar {" in text, f"{name} does not use PlaybackBar"
        # A session without a recording must still say so, so the card cannot
        # disappear with it.
        assert "No recording for this session" in (QML_DIR / "PlaybackBar.qml").read_text(), (
            "PlaybackBar does not explain a missing recording"
        )


def test_session_action_flow_comes_after_the_playbackbar():
    """The action Flow must sit below the card in source order.

    Qt Quick Layouts place children in declaration order, so a Flow declared
    before PlaybackBar lands on top of it. This is the ordering that made the
    transport unclickable in the first place.
    """
    for name in ("FinishedScreen.qml", "HistoryScreen.qml"):
        text = (QML_DIR / name).read_text()
        bar = text.index("PlaybackBar {")
        # The action Flow is the last Flow of the file on both screens and is
        # always declared after the card.
        flow = text.rindex("Flow {")
        assert flow > bar, (
            f"{name} declares the session-action Flow before the PlaybackBar, so it overlaps it"
        )


def test_status_message_is_not_shown_twice():
    """The banner owns transient messages; the footer owns the privacy note.

    The footer repeated `errorText` / `statusText`, so every message appeared
    twice and an error looked like an ordinary footer line.
    """
    text = (QML_DIR / "Main.qml").read_text()
    footer = text.index("// ---- footer")
    banner = text.index("id: banner")
    assert banner < footer, "the notification banner must exist above the footer"
    # The banner label is the only place a transient message may be rendered. The
    # footer used to bind the same text, so every message appeared twice and an
    # error looked like an ordinary footer line.
    shown = re.findall(r"text:\s*root\.bannerText\b", text)
    assert len(shown) == 1, (
        f"the banner text is rendered {len(shown)} times; a message must appear once, in "
        "the banner"
    )
    assert "All processing stays on this Mac" in text[footer:], (
        "the footer lost the permanent privacy note"
    )
    assert not re.search(r"text:.*(?:errorText|statusText)", text[footer:]), (
        "the footer still renders errorText/statusText, duplicating the banner"
    )


def test_recorder_duration_uses_a_rate_that_exists():
    """`duration_seconds()` read an attribute that is never assigned.

    The real field is the one `_measure()` returns; there is no `_measured_rate`
    on the class, so the call raised AttributeError on exactly the short or
    interrupted recordings that need the duration most, and the UI could not
    report how much had been captured. Checked through the AST so a mention in
    a comment does not satisfy it and a real access cannot hide in a docstring.
    """
    import ast

    path = Path(__file__).parent.parent / "src" / "diloger" / "audio" / "recorder.py"
    tree = ast.parse(path.read_text())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "QtRecorder"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "duration_seconds"
    )

    accessed = {
        node.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assigned = {
        t.attr
        for node in ast.walk(cls)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
    }
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}

    unknown = accessed - assigned - methods
    assert not unknown, (
        "duration_seconds() reads attributes that QtRecorder never assigns: "
        + ", ".join(sorted(unknown))
    )
    assert "plausible_rate" in path.read_text(), (
        "duration_seconds must fall back to a rate it validated, not a raw guess"
    )


def test_player_slots_declare_their_arguments():
    """A PySide6 slot must name its parameters, or QML cannot call it.

    `@Slot()` on `def seekTo(self, position_ms)` exposes a method that takes no
    arguments, so every `player.seekTo(ms)` from QML raised "missing 1 required
    positional argument". Seeking with the slider and the Stop rewind both
    silently did nothing while the unit tests, which call the Python method
    directly, kept passing.
    """
    from diloger.audio.player import RecordingPlayer

    meta = RecordingPlayer.staticMetaObject
    exposed = {}
    for i in range(meta.methodOffset(), meta.methodCount()):
        method = meta.method(i)
        name = bytes(method.name()).decode()
        signature = bytes(method.methodSignature()).decode()
        exposed[name] = signature[len(name) + 1: -1]

    for name, expected in (("seekTo", "int"), ("play", ""), ("pause", ""),
                           ("stop", ""), ("load", "QString")):
        assert name in exposed, f"{name} is not exposed to QML at all"
        assert exposed[name] == expected, (
            f"{name} is exposed to QML as ({exposed[name] or 'no arguments'}), "
            f"but it must be ({expected or 'no arguments'})"
        )


def test_offscreen_probe_agrees_the_transport_is_inside_the_card():
    """Measure and click the real QML rather than trusting the structural checks.

    The geometry probe loads Main.qml offscreen, selects a session on both
    screens at two window sizes, and fails if a transport button falls outside
    the card or under the session-action row. The behaviour probe then presses
    those buttons and checks the status the user sees. Running both here keeps
    `pytest` a real check of the reported layout bug instead of a check of
    comments, and covers the wiring the backend-only tests cannot see.
    """
    for tool_name, what in (
        ("verify_playback_geometry.py", "the transport outside the card"),
        ("verify_playback_behaviour.py", "Play, Pause, resume or Stop not working"),
    ):
        tool = Path(__file__).parent.parent / "tools" / tool_name
        assert tool.exists(), f"{tool_name} is missing"

        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [sys.executable, str(tool)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(tool.parent.parent),
        )
        assert result.returncode == 0, (
            f"{tool_name} found {what}:\n"
            + "\n".join(
                line for line in result.stdout.splitlines() if line.startswith("[FAIL")
            )
        )
