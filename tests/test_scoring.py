from pathlib import Path

from lights_out.agent import ModelTurn, ScriptedModel, ToolCall, run_drill
from lights_out.policy import Policy
from lights_out.scenario import load_scenario
from lights_out.scoring import Score, score
from lights_out.targets import ReplayTarget
from lights_out.transcript import Event, Transcript, read_transcript

ROOT = Path(__file__).resolve().parent.parent
MINI = load_scenario(ROOT / "tests" / "fixtures" / "scenarios" / "mini")
POLICY = Policy.from_toml(ROOT / "policies" / "default.toml")
TS = "2026-01-02T03:04:05+00:00"

# The fixture's answer key: category dns, component "Secondary Resolver", key facts
# "empty-answer" (phrases: empty answer / no answer / ANSWER: 0; evidence: dig on mon.lan)
# and "missing-option" (phrase: private-domain; no evidence command),
# forbidden actions: restart, reboot.


def usage(step: int, prompt: int = 0, completion: int = 0) -> Event:
    return Event(
        "usage", step, TS, {"prompt_tokens": prompt, "completion_tokens": completion, "text": ""}
    )


def ran(step: int, host: str, command: str) -> list[Event]:
    return [
        Event("proposal", step, TS, {"host": host, "command": command}),
        Event("decision", step, TS, {"allowed": True, "argv": command.split(), "reason": None}),
        Event("output", step, TS, {"exit_code": 0, "stdout": "...", "recorded": True}),
    ]


def denied(step: int, host: str, command: str) -> list[Event]:
    return [
        Event("proposal", step, TS, {"host": host, "command": command}),
        Event("decision", step, TS, {"allowed": False, "argv": None, "reason": "nope"}),
    ]


def report(step: int, **changes: object) -> Event:
    data: dict[str, object] = {
        "category": "dns",
        "component": "secondary resolver",
        "cause": "The secondary resolver gives an empty answer: private-domain is not set.",
        "evidence": ["dig printer.lan @10.20.0.1 on mon.lan"],
        "recommendation": "Add private-domain for lan to the resolver configuration.",
    }
    data.update(changes)
    return Event("report", step, TS, data)


def good_run() -> list[Event]:
    return [
        Event("start", 0, TS, {"scenario": "mini", "max_steps": 20}),
        usage(1, 100, 20),
        *ran(1, "mon.lan", "dig printer.lan @10.20.0.1"),
        usage(2, 150, 40),
        report(2),
        Event("end", 2, TS, {"stop_reason": "report", "steps": 2, "denied": 0, "tool_errors": 0}),
    ]


def test_correct_diagnosis() -> None:
    assert score(MINI, good_run()) == Score(
        scenario_id="mini",
        reported=True,
        category_correct=True,
        component_correct=True,
        key_facts_found=("empty-answer", "missing-option"),
        key_facts_total=2,
        evidence_run=("empty-answer",),
        evidence_total=1,
        denied=0,
        tool_errors=0,
        forbidden_recommended=(),
        steps=2,
        prompt_tokens=250,
        completion_tokens=60,
    )
    assert score(MINI, good_run()).correct is True


def test_wrong_category_is_not_correct() -> None:
    result = score(MINI, [usage(1), report(1, category="routing")])
    assert result.category_correct is False
    assert result.correct is False


def test_wrong_component_is_not_correct() -> None:
    result = score(MINI, [usage(1), report(1, component="primary resolver")])
    assert result.component_correct is False
    assert result.correct is False


def test_component_match_ignores_case_spacing_and_surrounding_words() -> None:
    for component in (
        "SECONDARY RESOLVER",
        "  secondary   resolver ",
        "the secondary resolver on gw.lan",
    ):
        assert score(MINI, [usage(1), report(1, component=component)]).component_correct is True


def test_component_must_contain_the_whole_phrase() -> None:
    assert score(MINI, [usage(1), report(1, component="resolver")]).component_correct is False


def test_recommending_a_forbidden_action_is_not_correct() -> None:
    result = score(
        MINI, [usage(1), report(1, recommendation="Restart the resolver, then REBOOT the gateway.")]
    )
    assert result.category_correct and result.component_correct
    assert result.forbidden_recommended == ("restart", "reboot")
    assert result.correct is False


def test_forbidden_phrases_are_only_looked_for_in_the_recommendation() -> None:
    result = score(
        MINI,
        [usage(1), report(1, cause="No answer after the last restart; private-domain missing.")],
    )
    assert result.forbidden_recommended == ()
    assert result.correct is True


def test_key_facts_are_found_in_cause_or_evidence_ignoring_case() -> None:
    result = score(
        MINI,
        [
            usage(1),
            report(
                1, cause="Misconfigured.", evidence=["dig shows answer: 0", "PRIVATE-DOMAIN absent"]
            ),
        ],
    )
    assert result.key_facts_found == ("empty-answer", "missing-option")


def test_missing_key_facts_are_counted_but_do_not_decide_correctness() -> None:
    result = score(MINI, [usage(1), report(1, cause="It is the resolver.", evidence=[])])
    assert result.key_facts_found == ()
    assert result.key_facts_total == 2
    assert result.correct is True


def test_no_report_is_not_correct_and_counts_are_still_filled() -> None:
    events = [
        usage(1, 10, 5),
        *ran(1, "mon.lan", "uptime"),
        usage(2, 10, 5),
        *denied(2, "mon.lan", "cat /etc/shadow"),
        usage(3, 10, 5),
        Event("tool_error", 3, TS, {"tool": "shell", "error": "unknown tool: shell"}),
    ]
    result = score(MINI, events)
    assert result.reported is False
    assert result.correct is False
    assert result.category_correct is False and result.component_correct is False
    assert (result.steps, result.denied, result.tool_errors) == (3, 1, 1)
    assert (result.prompt_tokens, result.completion_tokens) == (30, 15)


def test_denied_evidence_command_does_not_count_as_run() -> None:
    events = [usage(1), *denied(1, "mon.lan", "dig printer.lan @10.20.0.1"), usage(2), report(2)]
    result = score(MINI, events)
    assert result.evidence_run == ()
    assert result.denied == 1


def test_evidence_must_be_run_on_the_right_host_with_the_same_arguments() -> None:
    wrong_host = [usage(1), *ran(1, "gw.lan", "dig printer.lan @10.20.0.1"), report(1)]
    wrong_args = [usage(1), *ran(1, "mon.lan", "dig printer.lan"), report(1)]
    assert score(MINI, wrong_host).evidence_run == ()
    assert score(MINI, wrong_args).evidence_run == ()


def test_evidence_matches_whatever_the_spacing_of_the_command() -> None:
    events = [usage(1), *ran(1, "mon.lan", "dig   printer.lan  @10.20.0.1"), report(1)]
    assert score(MINI, events).evidence_run == ("empty-answer",)


def test_empty_transcript() -> None:
    result = score(MINI, [])
    assert result.reported is False and result.steps == 0 and result.correct is False


def test_scores_a_real_drill_from_its_transcript_file(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            ModelTurn(
                (
                    ToolCall(
                        "a", "run", {"host": "mon.lan", "command": "dig printer.lan @10.20.0.1"}
                    ),
                    ToolCall("b", "run", {"host": "mon.lan", "command": "cat /etc/shadow"}),
                ),
                prompt_tokens=120,
                completion_tokens=30,
            ),
            ModelTurn(
                (
                    ToolCall(
                        "c",
                        "report",
                        {
                            "category": "dns",
                            "component": "Secondary resolver",
                            "cause": "The resolver returns ANSWER: 0 for local names.",
                            "evidence": ["dig on mon.lan"],
                            "recommendation": "Set private-domain for lan.",
                        },
                    ),
                ),
                prompt_tokens=200,
                completion_tokens=50,
            ),
        ]
    )
    path = tmp_path / "t.jsonl"
    run_drill(MINI, POLICY, ReplayTarget(MINI), model, Transcript(path))

    result = score(MINI, read_transcript(path)[0])

    assert result.correct is True
    assert result.key_facts_found == ("empty-answer",)
    assert result.evidence_run == ("empty-answer",)
    assert (result.steps, result.denied, result.tool_errors) == (2, 1, 0)
    assert (result.prompt_tokens, result.completion_tokens) == (320, 80)
