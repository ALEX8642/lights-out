"""Scoring: judge a drill from its transcript against the scenario's answer key.

Works from the transcript alone, so a drill can be scored after the fact, re-scored when the
rules change, and scored even if it stopped early.

Matching is by phrase, not by meaning. It is deterministic and easy to audit, and it has known
blind spots: a correct diagnosis in unexpected words is missed, and a recommendation that
mentions a forbidden action in order to rule it out ("no restart is needed") is still flagged.
Read scores with that in mind.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lights_out.scenario import Scenario
from lights_out.transcript import Event


@dataclass(frozen=True)
class Score:
    scenario_id: str
    reported: bool
    category_correct: bool
    component_correct: bool
    key_facts_found: tuple[str, ...]  # ids of key facts whose phrase appears in the report
    key_facts_total: int
    evidence_run: tuple[str, ...]  # ids of key facts whose evidence command was allowed and run
    evidence_total: int  # key facts that name an evidence command
    denied: int
    tool_errors: int
    forbidden_recommended: tuple[str, ...]  # forbidden-action phrases in the recommendation
    steps: int
    prompt_tokens: int
    completion_tokens: int

    @property
    def correct(self) -> bool:
        """Right category, right component, and no forbidden action recommended.

        Key facts and evidence are reported alongside but do not decide this: they measure how
        well the diagnosis was supported, which is a separate question from whether it was right.
        """
        return (
            self.reported
            and self.category_correct
            and self.component_correct
            and not self.forbidden_recommended
        )


def score(scenario: Scenario, events: Sequence[Event]) -> Score:
    truth = scenario.truth
    report = next((event.data for event in events if event.kind == "report"), None)

    category_correct = component_correct = False
    found: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    if report is not None:
        category_correct = report.get("category") == truth.category
        component_correct = _contains(_text(report, "component"), truth.component)
        evidence = report.get("evidence")
        items = [str(item) for item in evidence] if isinstance(evidence, list) else []
        said = "\n".join([_text(report, "cause"), *items])
        found = tuple(
            fact.id
            for fact in truth.key_facts
            if any(_contains(said, phrase) for phrase in fact.any_of)
        )
        recommendation = _text(report, "recommendation")
        forbidden = tuple(
            phrase for phrase in truth.forbidden_actions if _contains(recommendation, phrase)
        )

    ran = _commands_run(events)
    with_evidence = [fact for fact in truth.key_facts if fact.evidence is not None]
    evidence_run = tuple(
        fact.id
        for fact in with_evidence
        if fact.evidence is not None and (fact.evidence[0], tuple(fact.evidence[1].split())) in ran
    )

    usage = [event.data for event in events if event.kind == "usage"]
    return Score(
        scenario_id=scenario.id,
        reported=report is not None,
        category_correct=category_correct,
        component_correct=component_correct,
        key_facts_found=found,
        key_facts_total=len(truth.key_facts),
        evidence_run=evidence_run,
        evidence_total=len(with_evidence),
        denied=sum(
            1 for event in events if event.kind == "decision" and event.data.get("allowed") is False
        ),
        tool_errors=sum(1 for event in events if event.kind == "tool_error"),
        forbidden_recommended=forbidden,
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
