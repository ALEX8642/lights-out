"""Batches: many drills per scenario and temperature, so a result has a rate and an interval.

One transcript per drill, under ``OUT/<scenario>/t<temperature>/<repeat>.jsonl``. After each drill
one line is appended to ``OUT/summary.jsonl``; ``OUT/summary.csv`` is rewritten at the end. A
batch can be resumed: a drill whose transcript is complete (it has an ``end`` event) is scored
from the file and not run again; an unfinished one is moved aside to ``.partial`` and redone.

The timestamps in each record are what an energy ledger needs to attribute wall-plug power to a
drill (docs/adr/0010-batches.md).
"""

from __future__ import annotations

import csv
import dataclasses
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lights_out.agent import ModelClient, run_drill
from lights_out.policy import Policy
from lights_out.scenario import Scenario, load_scenario
from lights_out.scoring import score
from lights_out.targets import ReplayTarget
from lights_out.transcript import Event, Transcript, read_transcript

type ModelFactory = Callable[[Path, float], ModelClient]
"""Builds a fresh model client for one drill: (scenario directory, temperature) -> client."""


@dataclass(frozen=True)
class RunRecord:
    scenario: str
    temperature: float
    repeat: int
    transcript: str
    correct: bool
    fix_correct: bool | None
    stop_reason: str
    steps: int
    prompt_tokens: int
    completion_tokens: int
    denied_unsafe: int
    denied_unlisted: int
    tool_errors: int
    key_facts_found: int
    key_facts_total: int
    evidence_run: int
    evidence_total: int
    started: str
    ended: str
    wall_seconds: float
    partial: bool = False  # blamed the contributing condition, all else right (ADR 0012)


@dataclass(frozen=True)
class GroupSummary:
    scenario: str
    temperature: float
    runs: int
    correct: int
    correct_interval: tuple[float, float]
    partial: int
    out_of_steps: int  # stopped by the step budget without a report
    fix_correct: int
    fix_scored: int
    denied_unsafe: int
    denied_unlisted: int
    mean_steps: float
    mean_wall_seconds: float
    mean_tokens: float


def wilson(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval for a proportion (95% by default); (0, 1) with no trials."""
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    centre = (p + z * z / (2 * trials)) / (1 + z * z / trials)
    half = (
        z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / (1 + z * z / trials)
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def run_batch(
    scenarios: Sequence[Path],
    policy: Policy,
    make_model: ModelFactory,
    out: Path,
    *,
    repeats: int,
    temperatures: Sequence[float],
    max_steps: int = 20,
    meta: Mapping[str, object] | None = None,
    on_record: Callable[[RunRecord], None] | None = None,
) -> list[RunRecord]:
    """Run every scenario ``repeats`` times at each temperature; return one record per drill."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    out.mkdir(parents=True, exist_ok=True)
    summary = out / "summary.jsonl"
    done = _already_summarised(summary)
    records: list[RunRecord] = []
    for scenario_dir in scenarios:
        scenario = load_scenario(scenario_dir)
        for temperature in temperatures:
            for repeat in range(1, repeats + 1):
                path = out / scenario.id / f"t{temperature:g}" / f"{repeat}.jsonl"
                key = str(path)
                if key in done:
                    records.append(done[key])
                    continue
                record = _finished(scenario, temperature, repeat, path)
                if record is None:
                    if path.exists():
                        path.replace(path.with_suffix(".partial"))
                    run_drill(
                        scenario,
                        policy,
                        ReplayTarget(scenario),
                        make_model(scenario_dir, temperature),
                        Transcript(path),
                        max_steps=max_steps,
                        meta={**dict(meta or {}), "temperature": temperature, "repeat": repeat},
                    )
                    record = _finished(scenario, temperature, repeat, path)
                    assert record is not None  # run_drill always writes an end event
                with summary.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(dataclasses.asdict(record)) + "\n")
                records.append(record)
                if on_record is not None:
                    on_record(record)
    _write_csv(out / "summary.csv", records)
    return records


def summarise(records: Sequence[RunRecord]) -> list[GroupSummary]:
    """One line per (scenario, temperature), in the order first seen."""
    groups: dict[tuple[str, float], list[RunRecord]] = {}
    for record in records:
        groups.setdefault((record.scenario, record.temperature), []).append(record)
    out: list[GroupSummary] = []
    for (scenario, temperature), runs in groups.items():
        n = len(runs)
        correct = sum(r.correct for r in runs)
        scored = [r for r in runs if r.fix_correct is not None]
        out.append(
            GroupSummary(
                scenario=scenario,
                temperature=temperature,
                runs=n,
                correct=correct,
                correct_interval=wilson(correct, n),
                partial=sum(r.partial for r in runs),
                out_of_steps=sum(r.stop_reason == "step_budget" for r in runs),
                fix_correct=sum(bool(r.fix_correct) for r in scored),
                fix_scored=len(scored),
                denied_unsafe=sum(r.denied_unsafe for r in runs),
                denied_unlisted=sum(r.denied_unlisted for r in runs),
                mean_steps=sum(r.steps for r in runs) / n,
                mean_wall_seconds=sum(r.wall_seconds for r in runs) / n,
                mean_tokens=sum(r.prompt_tokens + r.completion_tokens for r in runs) / n,
            )
        )
    return out


def _finished(scenario: Scenario, temperature: float, repeat: int, path: Path) -> RunRecord | None:
    if not path.is_file():
        return None
    try:
        events, _ = read_transcript(path)
    except ValueError:
        return None
    if not events or events[-1].kind != "end":
        return None
    return _record(scenario, temperature, repeat, path, events)


def _record(
    scenario: Scenario, temperature: float, repeat: int, path: Path, events: Sequence[Event]
) -> RunRecord:
    result = score(scenario, events)
    started, ended = events[0].ts, events[-1].ts
    wall = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
    return RunRecord(
        scenario=scenario.id,
        temperature=temperature,
        repeat=repeat,
        transcript=str(path),
        correct=result.correct,
        fix_correct=result.fix_correct,
        stop_reason=str(events[-1].data.get("stop_reason", "")),
        steps=result.steps,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        denied_unsafe=result.denied_unsafe,
        denied_unlisted=result.denied_unlisted,
        tool_errors=result.tool_errors,
        key_facts_found=len(result.key_facts_found),
        key_facts_total=result.key_facts_total,
        evidence_run=len(result.evidence_run),
        evidence_total=result.evidence_total,
        started=started,
        ended=ended,
        wall_seconds=wall,
        partial=result.partial,
    )


def _already_summarised(summary: Path) -> dict[str, RunRecord]:
    if not summary.is_file():
        return {}
    records: dict[str, RunRecord] = {}
    for line in summary.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = RunRecord(**json.loads(line))
            records[record.transcript] = record
    return records


def _write_csv(path: Path, records: Sequence[RunRecord]) -> None:
    fields = [f.name for f in dataclasses.fields(RunRecord)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(dataclasses.asdict(record))
