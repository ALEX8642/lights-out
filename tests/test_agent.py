import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from lights_out.agent import (
    MAX_CALLS_PER_TURN,
    TOOLS,
    DrillResult,
    Message,
    ModelTurn,
    Report,
    ScriptedModel,
    ToolCall,
    run_drill,
    system_prompt,
)
from lights_out.policy import Allow, Decision, Policy
from lights_out.scenario import ACTIONS, KeyFact, Scenario, Truth, load_scenario
from lights_out.targets import CommandResult, ReplayTarget
from lights_out.transcript import Event, Transcript, read_transcript

ROOT = Path(__file__).resolve().parent.parent
MINI = load_scenario(ROOT / "tests" / "fixtures" / "scenarios" / "mini")
POLICY = Policy.from_toml(ROOT / "policies" / "default.toml")

GOOD_REPORT = {
    "category": "dns",
    "host": "gw.lan",
    "component": "secondary resolver",
    "cause": "The resolver returns an empty answer for local names.",
    "evidence": ["dig printer.lan @10.20.0.1 on mon.lan shows ANSWER: 0"],
    "action": "change_config",
    "recommendation": "Add the missing option to the resolver configuration.",
}


class SpyTarget:
    """A replay target that remembers what it was asked to run."""

    def __init__(self, scenario: Scenario) -> None:
        self.inner = ReplayTarget(scenario)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, host: str, argv: tuple[str, ...]) -> CommandResult:
        self.calls.append((host, argv))
        return self.inner.run(host, argv)


def run(host: object, command: object, call_id: str = "c1") -> ToolCall:
    return ToolCall(call_id, "run", {"host": host, "command": command})


def report(call_id: str = "c9", **changes: object) -> ToolCall:
    return ToolCall(call_id, "report", {**GOOD_REPORT, **changes})


def turn(*calls: ToolCall, text: str = "", tokens: tuple[int, int] = (0, 0)) -> ModelTurn:
    return ModelTurn(calls, text, tokens[0], tokens[1])


class Drill:
    def __init__(
        self,
        tmp_path: Path,
        turns: Sequence[ModelTurn],
        scenario: Scenario = MINI,
        max_steps: int = 20,
    ) -> None:
        self.model = ScriptedModel(turns)
        self.target = SpyTarget(scenario)
        self.path = tmp_path / "t.jsonl"
        self.result: DrillResult = run_drill(
            scenario, POLICY, self.target, self.model, Transcript(self.path), max_steps=max_steps
        )
        self.events: list[Event] = read_transcript(self.path)[0]

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]

    def of(self, kind: str) -> list[Mapping[str, object]]:
        return [event.data for event in self.events if event.kind == kind]

    def tool_replies(self, call: int = -1) -> list[str]:
        """Content of the tool messages in the messages given to model call number ``call``."""
        return [str(m["content"]) for m in self.model.seen[call] if m["role"] == "tool"]


def test_allowed_command_then_report(tmp_path: Path) -> None:
    drill = Drill(
        tmp_path,
        [turn(run("mon.lan", "dig printer.lan @10.20.0.1"), tokens=(100, 20)), turn(report())],
    )
    assert drill.result == DrillResult(
        report=Report(
            category="dns",
            host="gw.lan",
            component="secondary resolver",
            cause="The resolver returns an empty answer for local names.",
            evidence=("dig printer.lan @10.20.0.1 on mon.lan shows ANSWER: 0",),
            action="change_config",
            recommendation="Add the missing option to the resolver configuration.",
        ),
        steps=2,
        denied=0,
        tool_errors=0,
        stop_reason="report",
    )
    assert drill.target.calls == [("mon.lan", ("dig", "printer.lan", "@10.20.0.1"))]
    assert drill.kinds() == [
        "start", "usage", "proposal", "decision", "output", "usage", "report", "end",
    ]  # fmt: skip
    assert [event.step for event in drill.events] == [0, 1, 1, 1, 1, 2, 2, 2]


def test_transcript_contents(tmp_path: Path) -> None:
    drill = Drill(
        tmp_path,
        [
            turn(run("mon.lan", "dig printer.lan @10.20.0.1"), text="Checking.", tokens=(100, 20)),
            turn(report()),
        ],
    )
    assert drill.of("start") == [{"scenario": "mini", "max_steps": 20}]
    assert drill.of("usage")[0] == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "text": "Checking.",
    }
    assert drill.of("proposal") == [{"host": "mon.lan", "command": "dig printer.lan @10.20.0.1"}]
    assert drill.of("decision") == [
        {"allowed": True, "argv": ["dig", "printer.lan", "@10.20.0.1"], "reason": None}
    ]
    output = drill.of("output")[0]
    assert output["exit_code"] == 0 and output["recorded"] is True
    assert "ANSWER: 0" in str(output["stdout"])
    assert drill.of("report") == [GOOD_REPORT]
    assert drill.of("end") == [{"stop_reason": "report", "steps": 2, "denied": 0, "tool_errors": 0}]


def test_command_output_is_returned_to_the_model(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("mon.lan", "uptime")), turn(report())])
    (reply,) = drill.tool_replies()
    assert reply.startswith("exit code 0\n")
    assert "load average" in reply


def test_messages_follow_the_chat_tool_protocol(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("mon.lan", "uptime", "abc")), turn(report())])
    first, second = drill.model.seen
    assert [m["role"] for m in first] == ["system", "user"]
    assert first[1]["content"] == MINI.briefing
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assistant, tool = second[2], second[3]
    assert assistant["tool_calls"] == [
        {
            "id": "abc",
            "type": "function",
            "function": {
                "name": "run",
                "arguments": json.dumps({"host": "mon.lan", "command": "uptime"}),
            },
        }
    ]
    assert tool["tool_call_id"] == "abc"


def test_denied_command_never_reaches_the_target(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("mon.lan", "ip addr show; uptime")), turn(report())])
    assert drill.target.calls == []
    assert drill.result.denied == 1
    assert drill.tool_replies() == ["DENIED: forbidden character: ;"]
    assert drill.kinds() == ["start", "usage", "proposal", "decision", "usage", "report", "end"]
    assert drill.of("decision") == [
        {"allowed": False, "argv": None, "reason": "forbidden character: ;"}
    ]


def test_target_receives_exactly_what_the_policy_approved(tmp_path: Path) -> None:
    class Rewriting(Policy):
        """Approves anything, as an argument list unlike the command, to expose shortcuts."""

        def check(self, role: str, command: str) -> Decision:
            return Allow(("approved", "by", "policy"))

    model = ScriptedModel([turn(run("mon.lan", "uptime")), turn(report())])
    target = SpyTarget(MINI)
    run_drill(MINI, Rewriting({}), target, model, Transcript(tmp_path / "t.jsonl"))
    assert target.calls == [("mon.lan", ("approved", "by", "policy"))]


def test_policy_is_applied_with_the_role_of_the_named_host(tmp_path: Path) -> None:
    # pfctl is allowed on the firewall role only; mon.lan is a linux host.
    drill = Drill(
        tmp_path,
        [turn(run("mon.lan", "pfctl -s rules", "a"), run("gw.lan", "pfctl -s rules", "b"))],
    )
    assert drill.target.calls == [("gw.lan", ("pfctl", "-s", "rules"))]
    assert drill.result.denied == 1


def test_unrecorded_command_is_reported_as_such(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("mon.lan", "ss -tulpn")), turn(report())])
    assert drill.of("output")[0]["recorded"] is False
    assert "no output recorded" in drill.tool_replies()[0]


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (ToolCall("c1", "run", {"command": "uptime"}), "host must be a string"),
        (ToolCall("c1", "run", {"host": "mon.lan"}), "command must be a string"),
        (run("mon.lan", 5), "command must be a string"),
        (run(["mon.lan"], "uptime"), "host must be a string"),
        (run("ghost.lan", "uptime"), "unknown host: ghost.lan"),
        (ToolCall("c1", "shell", {"command": "uptime"}), "unknown tool: shell"),
        (report("c1", category=None), "category must be one of"),
        (report("c1", category="gremlins"), "category must be one of"),
        (report("c1", component=""), "component must be a non-empty string"),
        (report("c1", cause=7), "cause must be a non-empty string"),
        (report("c1", evidence="dig"), "evidence must be a list of strings"),
        (report("c1", evidence=[1]), "evidence must be a list of strings"),
        (report("c1", recommendation=None), "recommendation must be a string"),
        (report("c1", host="ghost.lan"), "host must be one of: mon.lan, gw.lan, external"),
        (report("c1", host=None), "host must be one of"),
        (report("c1", action="turn it off and on"), "action must be one of"),
        (report("c1", action=None), "action must be one of"),
    ],
)
def test_malformed_tool_call_is_an_error_reply_and_the_drill_continues(
    tmp_path: Path, call: ToolCall, error: str
) -> None:
    drill = Drill(tmp_path, [turn(call), turn(report())])
    assert drill.result.tool_errors == 1
    assert drill.result.stop_reason == "report"
    assert drill.result.steps == 2
    assert drill.target.calls == []
    (reply,) = drill.tool_replies()
    assert reply.startswith("ERROR: ") and error in reply
    assert drill.of("tool_error")[0]["tool"] == call.name
    assert error in str(drill.of("tool_error")[0]["error"])


def test_unknown_host_error_lists_the_hosts(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("ghost.lan", "uptime")), turn(report())])
    assert "mon.lan, gw.lan" in drill.tool_replies()[0]


def test_empty_recommendation_is_allowed(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(report(recommendation=""))])
    assert drill.result.report is not None and drill.result.report.recommendation == ""


def test_fault_host_may_be_external(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(report(host="external", action="contact_provider"))])
    assert drill.result.report is not None
    assert (drill.result.report.host, drill.result.report.action) == (
        "external",
        "contact_provider",
    )


def test_report_tool_offers_exactly_the_action_list() -> None:
    schema = TOOLS[1]["function"]["parameters"]["properties"]  # type: ignore[index]
    assert schema["action"]["enum"] == sorted(ACTIONS)
    assert set(TOOLS[1]["function"]["parameters"]["required"]) == {  # type: ignore[index]
        "category", "host", "component", "cause", "evidence", "action", "recommendation",
    }  # fmt: skip


def test_no_tool_call_stops_the_drill(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(text="I think it is DNS.")])
    assert drill.result == DrillResult(None, 1, 0, 0, "no_tool_call")
    assert drill.kinds() == ["start", "usage", "end"]
    assert drill.of("usage")[0]["text"] == "I think it is DNS."


def test_step_budget_stops_a_model_that_never_reports(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("mon.lan", "uptime"))] * 10, max_steps=3)
    assert drill.result == DrillResult(None, 3, 0, 0, "step_budget")
    assert len(drill.target.calls) == 3
    assert drill.kinds()[-1] == "end"


def test_model_failure_is_recorded_and_the_transcript_is_closed(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(run("mon.lan", "uptime"))])  # script runs out on turn 2
    assert drill.result == DrillResult(None, 1, 0, 0, "model_error")
    end = drill.of("end")[0]
    assert end["stop_reason"] == "model_error"
    assert "IndexError" in str(end["error"])


def test_calls_after_a_report_in_the_same_turn_are_ignored(tmp_path: Path) -> None:
    drill = Drill(tmp_path, [turn(report("a"), run("mon.lan", "uptime", "b"))])
    assert drill.result.stop_reason == "report"
    assert drill.target.calls == []


def test_several_commands_in_one_turn_are_one_step(tmp_path: Path) -> None:
    drill = Drill(
        tmp_path,
        [turn(run("mon.lan", "uptime", "a"), run("gw.lan", "uptime", "b")), turn(report())],
    )
    assert drill.result.steps == 2
    assert len(drill.target.calls) == 2
    assert len(drill.tool_replies()) == 2


def test_too_many_calls_in_one_turn_are_refused_beyond_the_cap(tmp_path: Path) -> None:
    calls = [run("mon.lan", "uptime", f"c{i}") for i in range(MAX_CALLS_PER_TURN + 3)]
    drill = Drill(tmp_path, [turn(*calls), turn(report())])
    assert len(drill.target.calls) == MAX_CALLS_PER_TURN
    assert drill.result.tool_errors == 3
    assert len(drill.tool_replies()) == MAX_CALLS_PER_TURN + 3  # every call gets a reply
    assert "too many tool calls" in drill.tool_replies()[-1]


def test_nothing_from_the_answer_key_reaches_the_model(tmp_path: Path) -> None:
    markers = [
        "ibex-component",
        "zebra-component",
        "quokka phrase",
        "okapi phrase",
        "wombat action",
        "numbat-fact",
    ]
    secret = Truth(
        category="dns",
        host="gw.lan",
        component=("zebra-component", "ibex-component"),
        key_facts=(KeyFact("numbat-fact", ("quokka phrase", "okapi phrase"), None),),
        forbidden_actions=("wombat action",),
    )
    scenario = dataclasses.replace(MINI, truth=secret)
    drill = Drill(
        tmp_path,
        [
            turn(run("mon.lan", "uptime", "a"), run("mon.lan", "shutdown now", "b")),
            turn(ToolCall("c", "nonsense", {})),
            turn(report()),
        ],
        scenario=scenario,
    )
    everything = json.dumps([list(seen) for seen in drill.model.seen]) + json.dumps(list(TOOLS))
    for marker in markers:
        assert marker not in everything
    assert "printer.lan" in everything  # the check can see the briefing, so it is not vacuous


def test_system_prompt_describes_the_hosts_and_the_rules() -> None:
    prompt = system_prompt(MINI)
    assert "mon.lan (linux)" in prompt and "gw.lan (firewall)" in prompt
    assert "DENIED" in prompt
    assert all(phrase not in prompt for phrase in MINI.truth.component)


def test_tool_schemas_are_run_and_report() -> None:
    names = [tool["function"]["name"] for tool in TOOLS]  # type: ignore[index]
    assert names == ["run", "report"]


def test_scripted_model_from_json(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {
                    "text": "Looking.",
                    "tool_calls": [
                        {"name": "run", "arguments": {"host": "mon.lan", "command": "uptime"}}
                    ],
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                },
                {"tool_calls": [{"name": "report", "arguments": GOOD_REPORT}]},
            ]
        )
    )
    model = ScriptedModel.from_json(script)
    messages: list[Message] = [{"role": "user", "content": "hi"}]
    first = model.complete(messages, TOOLS)
    assert first == ModelTurn(
        (ToolCall("call_1", "run", {"host": "mon.lan", "command": "uptime"}),), "Looking.", 7, 3
    )
    assert model.complete(messages, TOOLS).tool_calls[0].id == "call_2"
    with pytest.raises(IndexError):
        model.complete(messages, TOOLS)


def test_scripted_model_keeps_a_copy_of_what_it_was_given() -> None:
    model = ScriptedModel([turn(report())])
    messages: list[Message] = [{"role": "user", "content": "hi"}]
    model.complete(messages, TOOLS)
    messages.append({"role": "user", "content": "later"})
    assert len(model.seen[0]) == 1
