import json
from pathlib import Path

import pytest

from lights_out.agent import ModelClient, ScriptedModel
from lights_out.batch import RunRecord, run_batch, summarise, wilson
from lights_out.cli import main
from lights_out.policy import Policy

ROOT = Path(__file__).resolve().parent.parent
POLICY = Policy.from_toml(ROOT / "policies" / "default.toml")
SCENARIOS = [ROOT / "scenarios" / "dns-secondary-lan", ROOT / "scenarios" / "wan-stale-lease"]


class Counting:
    """Builds each scenario's scripted model and counts how many drills were started."""

    def __init__(self) -> None:
        self.started = 0

    def __call__(self, scenario: Path, temperature: float) -> ModelClient:
        self.started += 1
        return ScriptedModel.from_json(scenario / "script.json")


@pytest.mark.parametrize(
    ("successes", "trials", "low", "high"),
    [(0, 0, 0.0, 1.0), (5, 5, 0.566, 1.0), (0, 5, 0.0, 0.434), (3, 5, 0.231, 0.882)],
)
def test_wilson_interval(successes: int, trials: int, low: float, high: float) -> None:
    got = wilson(successes, trials)
    assert got[0] == pytest.approx(low, abs=0.001)
    assert got[1] == pytest.approx(high, abs=0.001)


def test_runs_every_scenario_repeat_and_temperature(tmp_path: Path) -> None:
    factory = Counting()
    records = run_batch(SCENARIOS, POLICY, factory, tmp_path, repeats=2, temperatures=(0.0, 0.7))
    assert factory.started == 8
    assert len(records) == 8
    assert all(r.correct for r in records)
    assert {(r.scenario, r.temperature, r.repeat) for r in records} == {
        (s.name, t, i) for s in SCENARIOS for t in (0.0, 0.7) for i in (1, 2)
    }
    first = records[0]
    assert Path(first.transcript).is_file()
    assert first.wall_seconds >= 0 and first.started <= first.ended
    lines = (tmp_path / "summary.jsonl").read_text().splitlines()
    assert len(lines) == 8
    assert json.loads(lines[0])["scenario"] == "dns-secondary-lan"
    assert (
        (tmp_path / "summary.csv").read_text().splitlines()[0].startswith("scenario,temperature,")
    )


def test_a_rerun_resumes_and_does_not_redo_finished_drills(tmp_path: Path) -> None:
    run_batch(SCENARIOS, POLICY, Counting(), tmp_path, repeats=2, temperatures=(0.0,))
    again = Counting()
    records = run_batch(SCENARIOS, POLICY, again, tmp_path, repeats=3, temperatures=(0.0,))
    assert again.started == 2  # only the third repeat of each scenario
    assert len(records) == 6
    assert len((tmp_path / "summary.jsonl").read_text().splitlines()) == 6


def test_an_unfinished_transcript_is_set_aside_and_redone(tmp_path: Path) -> None:
    run_batch(SCENARIOS[:1], POLICY, Counting(), tmp_path, repeats=1, temperatures=(0.0,))
    (path,) = [p for p in tmp_path.rglob("*.jsonl") if p.name != "summary.jsonl"]
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:3]) + "\n")  # cut off: no end event
    (tmp_path / "summary.jsonl").unlink()
    again = Counting()
    records = run_batch(SCENARIOS[:1], POLICY, again, tmp_path, repeats=1, temperatures=(0.0,))
    assert again.started == 1
    assert records[0].correct
    assert path.with_suffix(".partial").is_file()


def test_summarise_groups_by_scenario_and_temperature() -> None:
    def rec(scenario: str, correct: bool, unsafe: int = 0) -> RunRecord:
        return RunRecord(
            scenario=scenario, temperature=0.0, repeat=1, transcript="t", correct=correct,
            fix_correct=None, stop_reason="report", steps=10, prompt_tokens=100,
            completion_tokens=10, denied_unsafe=unsafe, denied_unlisted=1, tool_errors=0,
            key_facts_found=1, key_facts_total=2, evidence_run=1, evidence_total=2,
            started="2026-01-01T00:00:00+00:00", ended="2026-01-01T00:01:00+00:00",
            wall_seconds=60.0,
        )  # fmt: skip

    groups = summarise([rec("a", True), rec("a", False, unsafe=2), rec("b", True)])
    a = next(g for g in groups if g.scenario == "a")
    assert (a.runs, a.correct, a.denied_unsafe) == (2, 1, 2)
    assert a.correct_interval == pytest.approx(wilson(1, 2))


def test_cli_batch_scripted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["batch", *map(str, SCENARIOS), "--scripted", "--repeats", "2", "--out", str(tmp_path)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "dns-secondary-lan" in out and "wan-stale-lease" in out
    assert "2/2" in out


def test_cli_batch_needs_a_model_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["batch", str(SCENARIOS[0]), "--out", str(tmp_path)]) == 2
    assert "one of --scripted or --endpoint" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_batch_rejects_bad_repeats(tmp_path: Path, value: str) -> None:
    assert (
        main(["batch", str(SCENARIOS[0]), "--scripted", "--repeats", value, "--out", str(tmp_path)])
        == 2
    )


def test_partial_credit_is_counted_apart_from_correct() -> None:
    def rec(correct: bool, partial: bool) -> RunRecord:
        return RunRecord(
            scenario="a", temperature=0.0, repeat=1, transcript="t", correct=correct,
            fix_correct=None, stop_reason="report", steps=1, prompt_tokens=0,
            completion_tokens=0, denied_unsafe=0, denied_unlisted=0, tool_errors=0,
            key_facts_found=0, key_facts_total=0, evidence_run=0, evidence_total=0,
            started="2026-01-01T00:00:00+00:00", ended="2026-01-01T00:01:00+00:00",
            wall_seconds=60.0, partial=partial,
        )  # fmt: skip

    (group,) = summarise([rec(True, False), rec(False, True), rec(False, True), rec(False, False)])
    assert (group.correct, group.partial) == (1, 2)
    assert group.correct_interval == pytest.approx(wilson(1, 4))


def test_a_summary_line_from_before_partial_credit_still_loads(tmp_path: Path) -> None:
    run_batch(SCENARIOS[:1], POLICY, Counting(), tmp_path, repeats=1, temperatures=(0.0,))
    summary = tmp_path / "summary.jsonl"
    line = json.loads(summary.read_text())
    del line["partial"]
    summary.write_text(json.dumps(line) + "\n")
    again = Counting()
    (record,) = run_batch(SCENARIOS[:1], POLICY, again, tmp_path, repeats=1, temperatures=(0.0,))
    assert again.started == 0 and record.partial is False


def test_drills_that_ran_out_of_steps_are_counted() -> None:
    def rec(stop: str) -> RunRecord:
        return RunRecord(
            scenario="a", temperature=0.7, repeat=1, transcript="t", correct=False,
            fix_correct=None, stop_reason=stop, steps=20, prompt_tokens=0,
            completion_tokens=0, denied_unsafe=0, denied_unlisted=0, tool_errors=0,
            key_facts_found=0, key_facts_total=0, evidence_run=0, evidence_total=0,
            started="2026-01-01T00:00:00+00:00", ended="2026-01-01T00:01:00+00:00",
            wall_seconds=60.0,
        )  # fmt: skip

    (group,) = summarise([rec("step_budget"), rec("report"), rec("step_budget")])
    assert group.out_of_steps == 2


def test_cli_batch_table_shows_out_of_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["batch", str(SCENARIOS[0]), "--scripted", "--out", str(tmp_path)])
    header = [line for line in capsys.readouterr().out.splitlines() if line.startswith("scenario")]
    assert "no report" in header[0]
