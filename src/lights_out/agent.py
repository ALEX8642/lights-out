"""The drill loop: a model investigates a scenario through two tools.

``run(host, command)`` asks for an inspection command. The policy decides; only an approved
argument list reaches the target, and a denial is returned to the model as text. ``report(...)``
ends the drill with a structured diagnosis. Everything is written to the transcript.

The model sees the briefing, the hosts, tool results and its own messages. It never sees
anything from the scenario's answer key.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from lights_out.policy import Allow, Policy
from lights_out.scenario import ACTIONS, CATEGORIES, EXTERNAL, Scenario
from lights_out.targets import Target
from lights_out.transcript import Transcript

MAX_CALLS_PER_TURN = 8

type Message = Mapping[str, object]
"""A chat message in the OpenAI-compatible shape."""

StopReason = Literal["report", "step_budget", "no_tool_call", "model_error"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ModelTurn:
    """One reply from the model: tool calls, any plain text, and token counts."""

    tool_calls: tuple[ToolCall, ...]
    text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ModelClient(Protocol):
    def complete(
        self, messages: Sequence[Message], tools: Sequence[Mapping[str, object]]
    ) -> ModelTurn: ...


@dataclass(frozen=True)
class Report:
    category: str
    host: str  # the host at fault, or "external"
    component: str
    cause: str
    evidence: tuple[str, ...]
    action: str  # the kind of action recommended, from ACTIONS
    recommendation: str  # free text for the operator; not scored


@dataclass(frozen=True)
class DrillResult:
    report: Report | None
    steps: int
    denied: int
    tool_errors: int
    stop_reason: StopReason


TOOLS: tuple[Mapping[str, object], ...] = (
    {
        "type": "function",
        "function": {
            "name": "run",
            "description": (
                "Run one read-only inspection command on a host. No shell: no pipes, "
                "redirection, quotes or variables. Returns the exit code and output, or "
                "DENIED with the reason if the command is not allowed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "A host name from the list."},
                    "command": {"type": "string", "description": "Program and arguments."},
                },
                "required": ["host", "command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report",
            "description": "Give the diagnosis and end the investigation. Call exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": sorted(CATEGORIES)},
                    "host": {
                        "type": "string",
                        "description": (
                            f"The host where the fault is, from the list, or '{EXTERNAL}' if "
                            "the cause is outside these hosts."
                        ),
                    },
                    "component": {
                        "type": "string",
                        "description": "The part at fault, in a few words.",
                    },
                    "cause": {"type": "string", "description": "The root cause in one sentence."},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "What you observed, each with the command that showed it.",
                    },
                    "action": {
                        "type": "string",
                        "enum": sorted(ACTIONS),
                        "description": (
                            "The kind of action the operator should take. Choose 'none' if "
                            "nothing needs doing, and the least disruptive kind that fixes "
                            "the cause otherwise."
                        ),
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "The action in your own words. You cannot do it yourself.",
                    },
                },
                "required": [
                    "category",
                    "host",
                    "component",
                    "cause",
                    "evidence",
                    "action",
                    "recommendation",
                ],
                "additionalProperties": False,
            },
        },
    },
)


def system_prompt(scenario: Scenario) -> str:
    """The standing instructions. Built from the hosts only; never from the answer key."""
    hosts = "\n".join(f"- {host.name} ({host.role})" for host in scenario.hosts)
    return (
        "You are diagnosing a problem on a small network. You can inspect; you cannot change "
        "anything.\n\n"
        f"Hosts:\n{hosts}\n\n"
        "Use the run tool to inspect a host. Commands are checked against a read-only "
        "allowlist for that kind of host. A command that is not allowed returns DENIED with "
        "the reason; choose a different command rather than rephrasing the same one. Some "
        "commands have no recorded output in this exercise and say so.\n\n"
        "Look at the evidence before you conclude. When you know the cause, or have learned "
        "all you can, call the report tool once. State only what the output you saw supports, "
        "and recommend the smallest action that would fix the cause."
    )


class ScriptedModel:
    """A model client that replays a fixed list of turns. For tests and demonstrations."""

    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = list(turns)
        self._next = 0
        self.seen: list[list[Message]] = []

    @classmethod
    def from_json(cls, path: Path) -> ScriptedModel:
        """Load turns from a JSON list. Tool call ids are assigned as call_1, call_2, ..."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        turns: list[ModelTurn] = []
        count = 0
        for item in raw:
            calls: list[ToolCall] = []
            for call in item.get("tool_calls", []):
                count += 1
                calls.append(ToolCall(f"call_{count}", call["name"], call.get("arguments", {})))
            turns.append(
                ModelTurn(
                    tuple(calls),
                    item.get("text", ""),
                    item.get("prompt_tokens", 0),
                    item.get("completion_tokens", 0),
                )
            )
        return cls(turns)

    def complete(
        self, messages: Sequence[Message], tools: Sequence[Mapping[str, object]]
    ) -> ModelTurn:
        self.seen.append(list(messages))
        turn = self._turns[self._next]  # IndexError when the script is exhausted
        self._next += 1
        return turn


class _ToolError(Exception):
    """A tool call the harness cannot act on. The message is returned to the model."""


def run_drill(
    scenario: Scenario,
    policy: Policy,
    target: Target,
    model: ModelClient,
    transcript: Transcript,
    *,
    max_steps: int = 20,
) -> DrillResult:
    """Let ``model`` investigate ``scenario`` until it reports or a limit stops it.

    One step is one model turn. Every turn counts, including turns whose calls were refused.
    """
    messages: list[Message] = [
        {"role": "system", "content": system_prompt(scenario)},
        {"role": "user", "content": scenario.briefing},
    ]
    report: Report | None = None
    steps = denied = tool_errors = 0
    stop: StopReason = "step_budget"
    error: str | None = None
    transcript.append("start", 0, scenario=scenario.id, max_steps=max_steps)

    while steps < max_steps and report is None:
        try:
            turn = model.complete(messages, TOOLS)
        except Exception as failure:  # the model is outside our control; record and stop
            stop, error = "model_error", f"{type(failure).__name__}: {failure}"
            break
        steps += 1
        transcript.append(
            "usage",
            steps,
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
            text=turn.text,
        )
        if not turn.tool_calls:
            stop = "no_tool_call"
            break
        messages.append(_assistant_message(turn))

        for position, call in enumerate(turn.tool_calls):
            try:
                if position >= MAX_CALLS_PER_TURN:
                    raise _ToolError(
                        f"too many tool calls in one turn (limit {MAX_CALLS_PER_TURN})"
                    )
                if call.name == "run":
                    reply, was_denied = _run(call, scenario, policy, target, transcript, steps)
                    denied += was_denied
                elif call.name == "report":
                    report = _report(call, scenario)
                    transcript.append("report", steps, **_report_data(report))
                    stop = "report"
                    break
                else:
                    raise _ToolError(f"unknown tool: {call.name}")
            except _ToolError as problem:
                tool_errors += 1
                transcript.append("tool_error", steps, tool=call.name, error=str(problem))
                reply = f"ERROR: {problem}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": reply})

    end: dict[str, object] = {
        "stop_reason": stop,
        "steps": steps,
        "denied": denied,
        "tool_errors": tool_errors,
    }
    if error is not None:
        end["error"] = error
    transcript.append("end", steps, **end)
    return DrillResult(report, steps, denied, tool_errors, stop)


def _report_data(report: Report) -> dict[str, object]:
    return {
        "category": report.category,
        "host": report.host,
        "component": report.component,
        "cause": report.cause,
        "evidence": list(report.evidence),
        "action": report.action,
        "recommendation": report.recommendation,
    }


def _assistant_message(turn: ModelTurn) -> Message:
    return {
        "role": "assistant",
        "content": turn.text,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(dict(call.arguments))},
            }
            for call in turn.tool_calls
        ],
    }


def _run(
    call: ToolCall,
    scenario: Scenario,
    policy: Policy,
    target: Target,
    transcript: Transcript,
    step: int,
) -> tuple[str, bool]:
    """Handle one ``run`` call. Returns the reply for the model and whether it was denied."""
    host_name, command = call.arguments.get("host"), call.arguments.get("command")
    if not isinstance(host_name, str):
        raise _ToolError("host must be a string")
    if not isinstance(command, str):
        raise _ToolError("command must be a string")
    host = scenario.host(host_name)
    if host is None:
        known = ", ".join(h.name for h in scenario.hosts)
        raise _ToolError(f"unknown host: {host_name}. Hosts are: {known}")

    transcript.append("proposal", step, host=host.name, command=command)
    decision = policy.check(host.role, command)
    if not isinstance(decision, Allow):
        transcript.append("decision", step, allowed=False, argv=None, reason=decision.reason)
        return f"DENIED: {decision.reason}", True
    transcript.append("decision", step, allowed=True, argv=list(decision.argv), reason=None)

    # The only place a command reaches a target, and only as the argument list just approved.
    result = target.run(host.name, decision.argv)
    transcript.append(
        "output",
        step,
        exit_code=result.exit_code,
        stdout=result.stdout,
        recorded=result.recorded,
    )
    return f"exit code {result.exit_code}\n{result.stdout}", False


def _report(call: ToolCall, scenario: Scenario) -> Report:
    args = call.arguments
    category = args.get("category")
    if not isinstance(category, str) or category not in CATEGORIES:
        raise _ToolError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    hosts = [host.name for host in scenario.hosts] + [EXTERNAL]
    host = args.get("host")
    if not isinstance(host, str) or host not in hosts:
        raise _ToolError(f"host must be one of: {', '.join(hosts)}")
    component, cause = args.get("component"), args.get("cause")
    if not isinstance(component, str) or not component.strip():
        raise _ToolError("component must be a non-empty string")
    if not isinstance(cause, str) or not cause.strip():
        raise _ToolError("cause must be a non-empty string")
    evidence = args.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise _ToolError("evidence must be a list of strings")
    action = args.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise _ToolError(f"action must be one of: {', '.join(sorted(ACTIONS))}")
    recommendation = args.get("recommendation")
    if not isinstance(recommendation, str):
        raise _ToolError("recommendation must be a string")
    return Report(category, host, component, cause, tuple(evidence), action, recommendation)
