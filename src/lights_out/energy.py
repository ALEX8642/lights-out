"""Energy ledger: price each drill of a batch from wall-plug power readings.

Power comes from a ``PowerSource`` (a Home Assistant power sensor, or a CSV file of readings).
A smart plug reports a new reading only when it changes, so each reading is held until the next
one. A drill's energy is the integral over its start and end timestamps from the batch summary;
"above idle" subtracts a baseline power measured separately, in a quiet window, because a
baseline taken just before a drill is polluted by the drill before it.

Pricing happens after the batch, from its summary, so a batch can be re-priced (a different
baseline, a cleaner plug) without running it again. See docs/adr/0010-batches.md.
"""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

type Sample = tuple[datetime, float]  # (time, watts)


class PowerSource(Protocol):
    def samples(self, start: datetime, end: datetime) -> list[Sample]:
        """Readings that cover [start, end], including the last one before ``start``."""
        ...


@dataclass(frozen=True)
class DrillEnergy:
    scenario: str
    temperature: float
    repeat: int
    correct: bool
    seconds: float
    wh: float | None  # None when no readings cover the drill
    wh_above_idle: float | None


def integrate(samples: Sequence[Sample], start: datetime, end: datetime) -> float | None:
    """Watt-hours between ``start`` and ``end``, holding each reading until the next."""
    ordered = sorted(samples)
    if not ordered:
        return None
    joules = 0.0
    for index, (time, watts) in enumerate(ordered):
        following = ordered[index + 1][0] if index + 1 < len(ordered) else end
        low, high = max(time, start), min(following, end)
        if high > low:
            joules += watts * (high - low).total_seconds()
    return joules / 3600


class CsvSource:
    """Readings from a CSV file with ``time`` (ISO 8601 with offset) and ``watts`` columns."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def samples(self, start: datetime, end: datetime) -> list[Sample]:
        out: list[Sample] = []
        with self.path.open(newline="", encoding="utf-8") as handle:
            for number, row in enumerate(csv.DictReader(handle), start=2):
                try:
                    out.append((datetime.fromisoformat(row["time"]), float(row["watts"])))
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(f"{self.path}: line {number}: {error}") from error
        return out


class HomeAssistantSource:
    """Readings of one power sensor from Home Assistant's history API."""

    def __init__(self, base_url: str, entity_id: str, token: str, timeout: float = 30) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"Home Assistant URL must be http:// or https://, got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.entity_id = entity_id
        self._token = token
        self._timeout = timeout

    def samples(self, start: datetime, end: datetime) -> list[Sample]:
        # Ask from a little earlier, so the reading in force when the window opens is included.
        since = (start - timedelta(minutes=30)).isoformat()
        query = urllib.parse.urlencode(
            {
                "filter_entity_id": self.entity_id,
                "end_time": end.isoformat(),
                "minimal_response": "",
            }
        )
        url = f"{self.base_url}/api/history/period/{urllib.parse.quote(since)}?{query}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token}"})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            data = json.load(response)
        out: list[Sample] = []
        for item in data[0] if data else []:
            try:
                out.append((datetime.fromisoformat(item["last_changed"]), float(item["state"])))
            except (KeyError, ValueError):
                continue  # "unavailable", "unknown"
        return out


def price_batch(summary: Path, source: PowerSource, idle_watts: float) -> list[DrillEnergy]:
    """Energy for every drill in a batch's ``summary.jsonl``."""
    rows = [json.loads(line) for line in summary.read_text().splitlines() if line.strip()]
    if not rows:
        return []
    starts = [datetime.fromisoformat(r["started"]) for r in rows]
    ends = [datetime.fromisoformat(r["ended"]) for r in rows]
    samples = source.samples(min(starts), max(ends))
    out: list[DrillEnergy] = []
    for row, start, end in zip(rows, starts, ends, strict=True):
        seconds = (end - start).total_seconds()
        wh = integrate(samples, start, end)
        above = None if wh is None else wh - idle_watts * seconds / 3600
        out.append(
            DrillEnergy(
                scenario=row["scenario"],
                temperature=float(row["temperature"]),
                repeat=int(row["repeat"]),
                correct=bool(row["correct"]),
                seconds=seconds,
                wh=wh,
                wh_above_idle=above,
            )
        )
    return out
