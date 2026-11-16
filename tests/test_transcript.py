import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lights_out.transcript import Event, Transcript, read_transcript


class Clock:
    """Starts at a fixed time and advances one second per reading."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(seconds=1)
        return current


def test_events_read_back_equal_and_in_order(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    transcript = Transcript(path, clock=Clock())
    transcript.append("start", 0, scenario="mini")
    transcript.append("proposal", 1, host="mon.lan", command="uptime")
    transcript.append("decision", 1, allowed=True, argv=["uptime"])

    events, truncated = read_transcript(path)

    assert truncated is False
    assert events == [
        Event("start", 0, "2026-01-02T03:04:05+00:00", {"scenario": "mini"}),
        Event("proposal", 1, "2026-01-02T03:04:06+00:00", {"host": "mon.lan", "command": "uptime"}),
        Event("decision", 1, "2026-01-02T03:04:07+00:00", {"allowed": True, "argv": ["uptime"]}),
    ]


def test_each_event_is_one_complete_line_on_disk_at_once(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    transcript = Transcript(path, clock=Clock())
    transcript.append("start", 0)
    assert path.read_text().endswith("\n")
    assert len(path.read_text().splitlines()) == 1
    transcript.append("end", 1, stop_reason="report")
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1]) == {
        "kind": "end",
        "step": 1,
        "ts": "2026-01-02T03:04:06+00:00",
        "data": {"stop_reason": "report"},
    }


def test_default_clock_is_utc(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    Transcript(path).append("start", 0)
    events, _ = read_transcript(path)
    assert datetime.fromisoformat(events[0].ts).utcoffset() == timedelta(0)


def test_text_survives_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    text = 'line one\nline "two"\ttab é中\x00 end'
    Transcript(path, clock=Clock()).append("output", 1, stdout=text)
    events, _ = read_transcript(path)
    assert events[0].data["stdout"] == text
    assert len(path.read_text().splitlines()) == 1


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "a" / "t.jsonl"
    Transcript(path, clock=Clock()).append("start", 0)
    assert path.is_file()


def test_refuses_an_existing_transcript(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    Transcript(path, clock=Clock()).append("start", 0)
    with pytest.raises(FileExistsError):
        Transcript(path)


def test_an_empty_existing_file_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.touch()
    Transcript(path, clock=Clock()).append("start", 0)
    assert len(read_transcript(path)[0]) == 1


def test_values_that_are_not_json_are_rejected_and_nothing_is_written(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    transcript = Transcript(path, clock=Clock())
    with pytest.raises(TypeError):
        transcript.append("output", 1, stdout=b"bytes")
    assert not path.exists() or path.read_text() == ""


def test_a_cut_off_last_line_is_skipped_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    transcript = Transcript(path, clock=Clock())
    transcript.append("start", 0)
    transcript.append("proposal", 1, host="mon.lan", command="uptime")
    whole = path.read_text()
    path.write_text(whole + '{"kind": "decision", "step": 1, "ts": "2026-01-0')

    events, truncated = read_transcript(path)

    assert truncated is True
    assert [event.kind for event in events] == ["start", "proposal"]


def test_a_complete_file_without_final_newline_is_not_truncated(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    Transcript(path, clock=Clock()).append("start", 0)
    path.write_text(path.read_text().rstrip("\n"))
    events, truncated = read_transcript(path)
    assert truncated is False
    assert len(events) == 1


def test_a_malformed_line_that_is_not_last_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    transcript = Transcript(path, clock=Clock())
    transcript.append("start", 0)
    transcript.append("end", 1)
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], "{not json", lines[1]]) + "\n")
    with pytest.raises(ValueError, match="line 2"):
        read_transcript(path)


@pytest.mark.parametrize(
    "line",
    [
        '{"kind": "start", "step": 0, "ts": "2026-01-02T03:04:05+00:00"}',
        '{"kind": "banana", "step": 0, "ts": "2026-01-02T03:04:05+00:00", "data": {}}',
        '{"kind": "start", "step": "0", "ts": "2026-01-02T03:04:05+00:00", "data": {}}',
        '{"kind": "start", "step": 0, "ts": 5, "data": {}}',
        '{"kind": "start", "step": 0, "ts": "2026-01-02T03:04:05+00:00", "data": []}',
        "[1, 2, 3]",
    ],
)
def test_a_well_formed_line_with_the_wrong_shape_is_an_error(tmp_path: Path, line: str) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(line + "\n")
    with pytest.raises(ValueError, match="line 1"):
        read_transcript(path)


def test_empty_file_reads_as_no_events(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.touch()
    assert read_transcript(path) == ([], False)
