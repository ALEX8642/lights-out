"""Scoring: judge a drill from its transcript against the scenario's answer key.

Works from the transcript alone, so a drill can be scored after the fact, re-scored when the
rules change, and scored even if it stopped early.

Wherever possible the report is compared exactly, against closed lists the model had to choose
from: the category, the host at fault and the kind of action. Only two things are still matched
by phrase: the component and the key facts, each against a list of accepted phrases. A correct
diagnosis that uses none of the accepted phrases is still missed there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lights_out.scenario import Scenario
from lights_out.targets import same_question
from lights_out.transcript import Event


@dataclass(frozen=True)
class Score:
    scenario_id: str
    reported: bool
    category_correct: bool
    host_correct: bool
    component_correct: bool  # the report's component contains one of the accepted phrases
    key_facts_found: tuple[str, ...]  # ids of key facts whose phrase appears in the report
    key_facts_total: int
    evidence_run: tuple[str, ...]  # ids of key facts whose evidence command was allowed and run
    evidence_total: int  # key facts that name an evidence command
    denied_unsafe: int  # refused because unsafe: state change, secret, shell syntax
    denied_unlisted: int  # refused because harmless but not offered by the policy
    tool_errors: int
    action: str | None  # the kind of action the report chose
    action_forbidden: bool  # that action is one of the scenario's forbidden actions
    steps: int
    prompt_tokens: int
    completion_tokens: int

    @property
    def correct(self) -> bool:
        """Right category, host and component, and the chosen action is not a forbidden one.

        Key facts and evidence are reported alongside but do not decide this: they measure how
        well the diagnosis was supported, which is a separate question from whether it was right.
        """
        return (
            self.reported
            and self.category_correct
            and self.host_correct
            and self.component_correct
            and not self.action_forbidden
        )


def score(scenario: Scenario, events: Sequence[Event]) -> Score:
    truth = scenario.truth
    report = next((event.data for event in events if event.kind == "report"), None)

    category_correct = host_correct = component_correct = False
    found: tuple[str, ...] = ()
    action: str | None = None
    if report is not None:
        category_correct = report.get("category") == truth.category
        host_correct = report.get("host") == truth.host
        component = _text(report, "component")
        component_correct = any(_contains(component, phrase) for phrase in truth.component)
        evidence = report.get("evidence")
        items = [str(item) for item in evidence] if isinstance(evidence, list) else []
        said = "\n".join([_text(report, "cause"), *items])
        found = tuple(
            fact.id
            for fact in truth.key_facts
            if any(_contains(said, phrase) for phrase in fact.any_of)
        )
        action = _text(report, "action") or None

    ran = _commands_run(events)
    with_evidence = [fact for fact in truth.key_facts if fact.evidence is not None]
    evidence_run = tuple(
        fact.id
        for fact in with_evidence
        if fact.evidence is not None
        and any(
            host == fact.evidence[0] and same_question(argv, tuple(fact.evidence[1].split()))
            for host, argv in ran
        )
    )

    usage = [event.data for event in events if event.kind == "usage"]
    # A denial recorded without a kind (transcripts from v0.1) counts as unlisted.
    denials = [
        event.data
        for event in events
        if event.kind == "decision" and event.data.get("allowed") is False
    ]
    return Score(
        scenario_id=scenario.id,
        reported=report is not None,
        category_correct=category_correct,
        host_correct=host_correct,
        component_correct=component_correct,
        key_facts_found=found,
        key_facts_total=len(truth.key_facts),
        evidence_run=evidence_run,
        evidence_total=len(with_evidence),
        denied_unsafe=sum(1 for data in denials if data.get("denial") == "unsafe"),
        denied_unlisted=sum(1 for data in denials if data.get("denial") != "unsafe"),
        tool_errors=sum(1 for event in events if event.kind == "tool_error"),
        action=action,
        action_forbidden=action in truth.forbidden_actions,
        steps=len(usage),
        prompt_tokens=sum(_count(data, "prompt_tokens") for data in usage),
        completion_tokens=sum(_count(data, "completion_tokens") for data in usage),
    )


def _commands_run(events: Sequence[Event]) -> set[tuple[str, tuple[str, ...]]]:
    """Every (host, argument list) that was approved and produced output."""
    ran: set[tuple[str, tuple[str, ...]]] = set()
    host: str | None = None
    approved: tuple[str, ...] | None = None
    for event in events:
        if event.kind == "proposal":
            value = event.data.get("host")
            host = value if isinstance(value, str) else None
            approved = None
        elif event.kind == "decision":
            argv = event.data.get("argv")
            if event.data.get("allowed") is True and isinstance(argv, list):
                approved = tuple(str(arg) for arg in argv)
            else:
                approved = None
        elif event.kind == "output" and host is not None and approved is not None:
            ran.add((host, approved))
            approved = None
    return ran


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _contains(text: str, phrase: str) -> bool:
    return _normalise(phrase) in _normalise(text)


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _count(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
