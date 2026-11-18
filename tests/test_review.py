import json
from pathlib import Path

import pytest

from lights_out.review import RefusalGroup, collect_refusals

ROOT = Path(__file__).resolve().parent.parent
TS = "2026-01-01T00:00:00+00:00"


def transcript(path: Path, scenario: str, refusals: list[tuple[str, str, str, str]]) -> None:
    """Write a minimal transcript with the given (host, command, kind, reason) refusals."""
    lines = [{"kind": "start", "step": 0, "ts": TS, "data": {"scenario": scenario}}]
    for host, command, kind, reason in refusals:
        lines.append(
            {"kind": "proposal", "step": 1, "ts": TS, "data": {"host": host, "command": command}}
        )
        denied = {"allowed": False, "argv": None, "reason": reason, "denial": kind, "repeat": False}
        lines.append({"kind": "decision", "step": 1, "ts": TS, "data": denied})
    lines.append(
        {"kind": "proposal", "step": 2, "ts": TS, "data": {"host": "gw.lan", "command": "uptime"}}
    )
    allowed = {"allowed": True, "argv": ["uptime"], "reason": None, "denial": None, "repeat": False}
    lines.append({"kind": "decision", "step": 2, "ts": TS, "data": allowed})
    lines.append({"kind": "end", "step": 2, "ts": TS, "data": {"stop_reason": "report"}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def test_refusals_are_grouped_by_kind_role_program_and_reason(tmp_path: Path) -> None:
    unlisted = "program not allowed: ss. Allowed on firewall hosts: arp, cat"
    transcript(tmp_path / "a" / "1.jsonl", "dns-secondary-lan", [
        ("gw.lan", "ss -tulpn", "unlisted", unlisted),
        ("gw.lan", "ss -lnup", "unlisted", unlisted),
        ("mon.lan", "cat /etc/shadow", "unsafe", "refused: protected file: /etc/shadow"),
    ])  # fmt: skip
    transcript(tmp_path / "a" / "2.jsonl", "dns-secondary-lan", [
        ("gw.lan", "ss -tulpn", "unlisted", unlisted),
    ])  # fmt: skip
    (tmp_path / "summary.jsonl").write_text("{}\n")  # not a transcript; ignored

    groups = collect_refusals([tmp_path], ROOT / "scenarios")

    assert groups[0] == RefusalGroup(
        kind="unlisted",
        role="firewall",
        program="ss",
        reason="program not allowed",
        count=3,
        drills=2,
        examples=("ss -tulpn", "ss -lnup"),
    )
    assert groups[1].kind == "unsafe"
    assert (groups[1].role, groups[1].program, groups[1].reason) == (
        "linux",
        "cat",
        "refused: protected file",
    )


def test_an_unknown_scenario_groups_under_an_unknown_role(tmp_path: Path) -> None:
    transcript(
        tmp_path / "1.jsonl",
        "no-such-scenario",
        [("x.lan", "ls", "unlisted", "arguments not allowed for ls. Usage: ls")],
    )
    (group,) = collect_refusals([tmp_path], ROOT / "scenarios")
    assert group.role == "?"
    assert group.reason == "arguments not allowed"


def test_cli_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from lights_out.cli import main

    transcript(
        tmp_path / "1.jsonl",
        "dns-secondary-lan",
        [
            (
                "gw.lan",
                "ss -tulpn",
                "unlisted",
                "program not allowed: ss. Allowed on firewall hosts: x",
            )
        ],
    )
    assert main(["refusals", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "unlisted" in out and "firewall" in out and "ss -tulpn" in out
