import dataclasses
from pathlib import Path

from lights_out.scenario import Recording, Scenario, load_scenario
from lights_out.targets import (
    MAX_OUTPUT_CHARS,
    NO_RECORDING,
    TRUNCATION_MARKER,
    CommandResult,
    ReplayTarget,
    Target,
)

MINI = Path(__file__).parent / "fixtures" / "scenarios" / "mini"


def with_recording(stdout: str, exit_code: int = 0) -> Scenario:
    scenario = load_scenario(MINI)
    recordings = dict(scenario.recordings)
    recordings[("mon.lan", ("df", "-h"))] = Recording(exit_code, stdout)
    return dataclasses.replace(scenario, recordings=recordings)


def test_recorded_command_returns_its_recording() -> None:
    target = ReplayTarget(load_scenario(MINI))
    result = target.run("mon.lan", ("uptime",))
    assert result == CommandResult(
        0, " 10:01:02 up 3 days,  1 user,  load average: 0.00, 0.01, 0.05\n", True
    )


def test_exit_code_is_replayed() -> None:
    target = ReplayTarget(with_recording("df: cannot read table of mounted file systems\n", 1))
    assert target.run("mon.lan", ("df", "-h")).exit_code == 1


def test_unrecorded_command_says_so() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("mon.lan", ("ss", "-tulpn")) == CommandResult(127, NO_RECORDING, False)


def test_lookup_is_per_host() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("gw.lan", ("uptime",)).recorded is False


def test_lookup_needs_the_exact_argument_list() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("mon.lan", ("dig", "printer.lan")).recorded is False
    assert target.run("mon.lan", ("dig", "printer.lan", "@10.20.0.1")).recorded is True


def test_unknown_host_is_unrecorded_not_an_error() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("ghost.lan", ("uptime",)) == CommandResult(127, NO_RECORDING, False)


def test_long_output_is_truncated_with_a_marker() -> None:
    target = ReplayTarget(with_recording("x" * 9000))
    result = target.run("mon.lan", ("df", "-h"))
    assert result.stdout == "x" * MAX_OUTPUT_CHARS + TRUNCATION_MARKER
    assert result.recorded is True


def test_output_at_the_limit_is_unchanged() -> None:
    target = ReplayTarget(with_recording("x" * MAX_OUTPUT_CHARS))
    assert target.run("mon.lan", ("df", "-h")).stdout == "x" * MAX_OUTPUT_CHARS


def test_limit_is_8000_characters() -> None:
    assert MAX_OUTPUT_CHARS == 8000


def test_replay_target_satisfies_the_target_protocol() -> None:
    target: Target = ReplayTarget(load_scenario(MINI))
    assert target.run("mon.lan", ("uptime",)).recorded is True
