"""Every scenario shipped in scenarios/ must be solvable under the default policy."""

from pathlib import Path

import pytest

from lights_out.policy import Allow, Policy
from lights_out.scenario import Scenario, load_scenario

ROOT = Path(__file__).resolve().parent.parent
POLICY = Policy.from_toml(ROOT / "policies" / "default.toml")
SCENARIO_DIRS = sorted(path for path in (ROOT / "scenarios").iterdir() if path.is_dir())


def test_there_is_at_least_one_bundled_scenario() -> None:
    assert SCENARIO_DIRS


@pytest.fixture(params=SCENARIO_DIRS, ids=lambda path: path.name)
def scenario(request: pytest.FixtureRequest) -> Scenario:
    return load_scenario(request.param)


def test_every_host_role_exists_in_the_default_policy(scenario: Scenario) -> None:
    for host in scenario.hosts:
        assert host.role in POLICY.roles(), f"{host.name}: role {host.role}"


def test_every_recording_is_reachable_under_the_default_policy(scenario: Scenario) -> None:
    for host_name, argv in scenario.recordings:
        host = scenario.host(host_name)
        assert host is not None
        decision = POLICY.check(host.role, " ".join(argv))
        assert decision == Allow(argv), f"{host_name}: {' '.join(argv)}: {decision}"


def test_every_key_fact_evidence_is_allowed_and_recorded(scenario: Scenario) -> None:
    for fact in scenario.truth.key_facts:
        if fact.evidence is None:
            continue
        host_name, command = fact.evidence
        host = scenario.host(host_name)
        assert host is not None
        decision = POLICY.check(host.role, command)
        assert isinstance(decision, Allow), f"{fact.id}: {command}: {decision}"
        assert (host_name, decision.argv) in scenario.recordings, f"{fact.id}: no recording"


def test_every_key_fact_phrase_can_be_seen_in_its_evidence(scenario: Scenario) -> None:
    """At least one accepted phrase of a fact appears in the output that is meant to reveal it.

    A fact whose id starts with ``absent-`` is revealed by something *missing* from the output
    (an option that should be in a config file and is not), so it is exempt.
    """
    for fact in scenario.truth.key_facts:
        if fact.evidence is None:
            continue
        host_name, command = fact.evidence
        stdout = scenario.recordings[(host_name, tuple(command.split()))].stdout.lower()
        assert any(phrase.lower() in stdout for phrase in fact.any_of) or fact.id.startswith(
            "absent-"
        ), f"{fact.id}: none of {fact.any_of} appears in the output of {command}"


def test_the_briefing_does_not_give_the_answer_away(scenario: Scenario) -> None:
    briefing = scenario.briefing.lower()
    for phrase in scenario.truth.component:
        assert phrase.lower() not in briefing, f"briefing names the component: {phrase}"


def test_the_bundled_script_exists(scenario: Scenario) -> None:
    assert (ROOT / "scenarios" / scenario.id / "script.json").is_file()
