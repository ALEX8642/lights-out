"""Scoring: judge a drill from its transcript against the scenario's answer key.

Works from the transcript alone, so a drill can be scored after the fact, re-scored when the
rules change, and scored even if it stopped early.

The report is compared exactly, against closed lists the model had to choose from: the category,
the host at fault, the component and the kind of action. Only the key facts are matched by
phrase, and they do not decide whether a diagnosis is correct. Reports written before the
component became a closed list carry free text there; those are matched against the answer
key's legacy phrases (docs/adr/0012-closed-components.md).

Some incidents have a contributing condition as well as a root cause. Naming the root cause is
correct. Blaming the contributing condition instead, with everything else right, is partial
credit: half a correct diagnosis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lights_out.scenario import COMPONENTS, NO_FIX, Scenario
from lights_out.targets import same_question
from lights_out.transcript import Event


@dataclass(frozen=True)
class Score:
    scenario_id: str
    reported: bool
    category_correct: bool
    host_correct: bool
    component_correct: bool  # the report's component is an accepted id (or legacy phrase)
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
    fixes_requested: tuple[str, ...] = ()  # fix ids, in the order requested
    fix_outcome: str | None = None  # what the harness did with the expected fix, if requested
    fix_correct: bool | None = None  # None when the scenario offers no fixes
    contributing_named: bool | None = None  # None when the scenario has no contributing condition
    blamed_contributing: bool = False  # the report's root cause is the contributing condition

    @property
    def correct(self) -> bool:
        """Right category, host and component, no forbidden action, and the right fix decision.

        Key facts and evidence are reported alongside but do not decide this: they measure how
        well the diagnosis was supported, which is a separate question from whether it was right.
        """
        return (
            self.reported
            and self.category_correct
            and self.host_correct
            and self.component_correct
            and not self.action_forbidden
            and self.fix_correct is not False
        )

    @property
    def partial(self) -> bool:
        """Blamed the contributing condition instead of the root cause, all else right."""
        return (
            not self.correct
            and self.reported
            and self.category_correct
            and self.blamed_contributing
            and not self.action_forbidden
            and self.fix_correct is not False
        )

    @property
    def credit(self) -> float:
        """1 for a correct diagnosis, 0.5 for a partial one, 0 otherwise."""
        return 1.0 if self.correct else 0.5 if self.partial else 0.0


def score(scenario: Scenario, events: Sequence[Event]) -> Score:
    truth = scenario.truth
    report = next((event.data for event in events if event.kind == "report"), None)

    category_correct = host_correct = component_correct = blamed = False
    contributing_named: bool | None = None
    found: tuple[str, ...] = ()
    action: str | None = None
    if report is not None:
        category_correct = report.get("category") in (truth.category, *truth.other_categories)
        host = report.get("host")
        host_correct = host == truth.host
        component = _text(report, "component")
        component_correct = _component_matches(component, truth.component, truth.component_phrases)
        if truth.contributing is not None:
            other_host, other_ids = truth.contributing
            blamed = host == other_host and component in other_ids
            named = report.get("contributing")
            contributing_named = blamed or (
                isinstance(named, Mapping)
                and named.get("host") == other_host
                and named.get("component") in other_ids
            )
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
            host == fact.evidence[0] and _answers(argv, tuple(fact.evidence[1].split()))
            for host, argv in ran
        )
    )

    fixes = [event.data for event in events if event.kind == "fix"]
    requested = tuple(str(data.get("id")) for data in fixes)
    expected = truth.expected_fix
    fix_outcome: str | None = None
    fix_correct: bool | None = None
    if scenario.fixes and expected is not None:
        if expected == NO_FIX:
            fix_correct = not requested
        else:
            outcomes = [str(d.get("outcome")) for d in fixes if d.get("id") == expected]
            fix_outcome = outcomes[0] if outcomes else None
            fix_correct = fix_outcome == truth.expected_outcome
        if any(fix_id in truth.forbidden_fixes for fix_id in requested):
            fix_correct = False

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
        fixes_requested=requested,
        fix_outcome=fix_outcome,
        fix_correct=fix_correct,
        contributing_named=contributing_named,
        blamed_contributing=blamed,
    )


def _component_matches(component: str, ids: Sequence[str], phrases: Sequence[str]) -> bool:
    """An id from the closed list is compared exactly; free text (older reports) by phrase."""
    if component in COMPONENTS:
        return component in ids
    return any(_contains(component, phrase) for phrase in phrases)


def _answers(ran: tuple[str, ...], evidence: tuple[str, ...]) -> bool:
    """Whether running ``ran`` showed what the evidence command shows.

    The same question under the replay rules, or, when the evidence is reading a file with
    ``cat``, reading that file with ``head`` or ``tail``.
    """
    if same_question(ran, evidence):
        return True
    if evidence[0] == "cat" and len(evidence) == 2 and ran[0] in {"cat", "head", "tail"}:
        return evidence[1] in ran[1:]
    return False


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
