import json
from pathlib import Path

import pytest

from lights_out.cli import main

ROOT = Path(__file__).resolve().parent.parent
POLICY = str(ROOT / "policies" / "default.toml")
SCENARIO = ROOT / "scenarios" / "dns-secondary-lan"
SCRIPT = SCENARIO / "script.json"


def drill(out: Path, script: Path = SCRIPT, *extra: str) -> int:
    return main(
        ["drill", str(SCENARIO), "--policy", POLICY, "--script", str(script), "--out", str(out)]
        + list(extra)
    )


def test_policy_check_allows(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["policy", "check", "--policy", POLICY, "--role", "linux", "ip addr show"])
    assert code == 0
    assert capsys.readouterr().out == "allow: ip addr show\n"


def test_policy_check_denies(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["policy", "check", "--policy", POLICY, "--role", "linux", "ip addr show | wc"])
    assert code == 1
    assert capsys.readouterr().out == "deny: forbidden character: |\n"


def test_drill_then_score_the_bundled_scenario(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run.jsonl"
    assert drill(out) == 0
    assert "stopped: report after 3 steps (1 denied, 0 tool errors)" in capsys.readouterr().out

    assert main(["score", str(SCENARIO), str(out), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["correct"] is True
    assert result["scenario_id"] == "dns-secondary-lan"
    assert result["key_facts_found"] == [
        "primary-answers",
        "secondary-empty",
        "absent-private-domain",
    ]
    assert result["evidence_run"] == result["key_facts_found"]
    assert (result["denied"], result["tool_errors"], result["steps"]) == (1, 0, 3)
    assert (result["action"], result["action_forbidden"]) == ("change_config", False)


def test_score_prints_a_readable_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run.jsonl"
    drill(out)
    capsys.readouterr()
    assert main(["score", str(SCENARIO), str(out)]) == 0
    text = capsys.readouterr().out
    assert "dns-secondary-lan: CORRECT" in text
    assert "key facts found   3 of 3" in text
    assert "evidence run      3 of 3" in text
    assert "denied commands   1" in text


def test_a_forbidden_action_scores_as_incorrect_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    turns = json.loads(SCRIPT.read_text())
    turns[-1]["tool_calls"][0]["arguments"]["action"] = "restart_service"
    script = tmp_path / "restart.json"
    script.write_text(json.dumps(turns))
    out = tmp_path / "run.jsonl"
    assert drill(out, script) == 0  # the drill itself finished with a report

    assert main(["score", str(SCENARIO), str(out)]) == 1
    text = capsys.readouterr().out
    assert "dns-secondary-lan: NOT CORRECT" in text
    assert "restart_service (FORBIDDEN in this scenario)" in text


def test_a_drill_that_ends_without_a_report_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    turns = json.loads(SCRIPT.read_text())[:-1]
    script = tmp_path / "unfinished.json"
    script.write_text(json.dumps(turns))
    assert drill(tmp_path / "run.jsonl", script) == 1
    assert "stopped: model_error" in capsys.readouterr().out


def test_max_steps_is_passed_through(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert drill(tmp_path / "run.jsonl", SCRIPT, "--max-steps", "1") == 1
    assert "stopped: step_budget after 1 steps" in capsys.readouterr().out


def test_missing_scenario_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["score", str(tmp_path / "absent"), str(tmp_path / "run.jsonl")])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("lights-out: ") and "not a directory" in captured.err
    assert captured.err.count("\n") == 1


def test_existing_transcript_is_not_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run.jsonl"
    assert drill(out) == 0
    before = out.read_text()
    assert drill(out) == 2
    assert "already exists" in capsys.readouterr().err
    assert out.read_text() == before


def test_missing_policy_file_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["policy", "check", "--policy", "/nonexistent.toml", "--role", "linux", "uptime"])
    assert code == 2
    assert "lights-out: " in capsys.readouterr().err


def test_a_cut_off_transcript_is_scored_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "run.jsonl"
    drill(out)
    capsys.readouterr()
    lines = out.read_text().splitlines()
    out.write_text("\n".join(lines[:4]) + '\n{"kind": "outp')
    assert main(["score", str(SCENARIO), str(out)]) == 1
    captured = capsys.readouterr()
    assert "warning: transcript ends mid-line" in captured.err
    assert "NOT CORRECT" in captured.out


def test_no_arguments_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as stop:
        main([])
    assert stop.value.code == 2
