"""Transcript: an append-only record of everything that happens in a drill.

One JSON object per line. Each event is written and flushed as it happens, so a drill that
crashes leaves a readable record up to that point. Scoring works from this file alone.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast, get_args

EventKind = Literal[
    "start", "proposal", "decision", "output", "tool_error", "usage", "report", "end"
]
_KINDS = frozenset(get_args(EventKind))


@dataclass(frozen=True)
class Event:
    kind: EventKind
    step: int
    ts: str  # UTC, ISO 8601
    data: Mapping[str, object]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Transcript:
    """Writes events to ``path``. Refuses to add to a transcript that already has content."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        if path.exists() and path.stat().st_size > 0:
            raise FileExistsError(f"{path}: transcript already exists")
        self._path = path
        self._clock = clock

    def append(self, kind: EventKind, step: int, **data: object) -> None:
        """Write one event as one line. The file is closed after each event, not synced."""
        line = json.dumps(
            {"kind": kind, "step": step, "ts": self._clock().isoformat(), "data": data}
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_transcript(path: Path) -> tuple[list[Event], bool]:
    """Return the events in ``path`` and whether its last line was cut off.

    A final line with no newline that does not parse is what a crash mid-write leaves behind:
    it is skipped and reported. Any other malformed line is an error.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    complete = text.endswith("\n") or text == ""
    if complete:
        lines = lines[:-1] if text else []
    events: list[Event] = []
    for number, line in enumerate(lines, start=1):
        last_and_unterminated = not complete and number == len(lines)
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            if last_and_unterminated:
                return events, True
            raise ValueError(f"{path}: line {number}: not valid JSON ({error.msg})") from error
        events.append(_event(raw, path, number))
    return events, False


def _event(raw: object, path: Path, number: int) -> Event:
    if isinstance(raw, dict):
        kind, step, ts, data = raw.get("kind"), raw.get("step"), raw.get("ts"), raw.get("data")
        if (
            kind in _KINDS
            and isinstance(step, int)
            and not isinstance(step, bool)
            and isinstance(ts, str)
            and isinstance(data, dict)
        ):
            return Event(cast(EventKind, kind), step, ts, data)
    raise ValueError(f"{path}: line {number}: not a transcript event")
