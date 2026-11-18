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
from lights_out.scenario import ACTIONS, CATEGORIES, COMPONENTS, EXTERNAL, Fix, FixState, Scenario
from lights_out.targets import Target
from lights_out.transcript import Transcript

MAX_CALLS_PER_TURN = 8
INVALID_ARGUMENTS = "__invalid_json__"
"""Key a model client uses for tool-call arguments it could not decode, so the model is told."""

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
    component: str  # from COMPONENTS
    cause: str
    evidence: tuple[str, ...]
    action: str  # the kind of action recommended, from ACTIONS
    recommendation: str  # free text for the operator; not scored
    detail: str = ""  # the part at fault in the model's own words; not scored
    contributing: tuple[str, str] | None = None  # (host, component) that made it show, if any


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
                        "enum": sorted(COMPONENTS),
                        "description": "The part at fault on that host.",
                    },
                    "detail": {
                        "type": "string",
                        "description": "The part at fault in your own words, a few words.",
                    },
                    "contributing": {
                        "type": "object",
                        "description": (
                            "Optional. A second condition that made the fault show without "
                            "being its cause, if you found one."
                        ),
                        "properties": {
                            "host": {"type": "string"},
                            "component": {"type": "string", "enum": sorted(COMPONENTS)},
                        },
                        "required": ["host", "component"],
                        "additionalProperties": False,
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


_CLASS_WORDS = {
    "act_alone": "acts at once",
    "ask_then_act": (
        "asks the maintainer first; if they cannot be reached and its conditions hold, it runs "
        "anyway"
    ),
    "approval_only": "needs the maintainer's approval",
}


def tools_for(scenario: Scenario) -> tuple[Mapping[str, object], ...]:
    """The tools offered in this scenario: run and report, plus fix when it offers fixes."""
    if not scenario.fixes:
        return TOOLS
    fix_tool: Mapping[str, object] = {
        "type": "function",
        "function": {
            "name": "fix",
            "description": (
                "Request one of the listed fixes. You cannot run it yourself: the harness checks "
                "its preconditions and decides whether it runs at once, asks the maintainer "
                "first, or is refused, and tells you what happened. Each fix can be requested "
                "once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": [fix.id for fix in scenario.fixes]},
                    "reason": {"type": "string", "description": "Why this fix, from the evidence."},
                    "options": {
                        "type": "object",
                        "description": "Only the options the fix lists, with one of its values.",
                    },
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        },
    }
    return (*TOOLS, fix_tool)


def decide_fix(fix: Fix, state: FixState) -> tuple[str, str]:
    """What the harness does with a requested fix, and what it tells the model.

    Decided from the fix's class, its preconditions and whether the maintainer can be reached,
    never from anything the model says. In drills a fix is always simulated.
    """
    for name in fix.preconditions:
        if not state.preconditions.get(name, False):
            return (
                "refused",
                f"{fix.id}: refused: precondition not met: {name}. The fix did not run.",
            )
    simulated = "(This is a drill: nothing was changed.)"
    if fix.fix_class == "act_alone":
        return "acted", f"{fix.id}: preconditions met; the fix ran. {simulated}"
    if state.reachability in {"online", "home"}:
        if state.approval == "approve":
            return (
                "asked_approved",
                f"{fix.id}: asked the maintainer; he approved; the fix ran. {simulated}",
            )
        if state.approval == "deny":
            return (
                "asked_denied",
                f"{fix.id}: asked the maintainer; he declined. The fix did not run.",
            )
        return (
            "asked_no_answer",
            f"{fix.id}: asked the maintainer; no answer, so the fix did not run.",
        )
    if fix.fix_class == "ask_then_act":
        return (
            "acted_unreachable",
            f"{fix.id}: the maintainer cannot be reached and the conditions for acting without an "
            f"answer hold; the fix ran once. {simulated}",
        )
    return (
        "asked_no_answer",
        f"{fix.id}: needs the maintainer's approval and he cannot be reached; queued for when "
        "contact returns. The fix did not run.",
    )


def system_prompt(scenario: Scenario, policy: Policy) -> str:
    """The standing instructions: hosts, the commands each kind of host allows, and the rules.

    Built from the scenario's hosts and the policy only; never from the answer key.
    """
    hosts = "\n".join(f"- {host.name} ({host.role})" for host in scenario.hosts)
    by_role: dict[str, list[str]] = {}
    for host in scenario.hosts:
        by_role.setdefault(host.role, []).append(host.name)
    menus = []
    shown: dict[tuple[str, ...], str] = {}  # command list -> first role it was printed for
    for role, names in by_role.items():
        description = policy.description(role)
        heading = f"{role} hosts ({', '.join(names)})" + (f": {description}" if description else "")
        usages = tuple(policy.usage(role).values())
        if usages in shown:
            menus.append(f"{heading}\n  same commands as {shown[usages]} hosts")
            continue
        shown[usages] = role
        lines = "\n".join(f"  {usage}" for usage in usages)
        menus.append(f"{heading}\n{lines}")
    fixes = ""
    if scenario.fixes:
        fix_lines = []
        for fix in scenario.fixes:
            options = "; ".join(
                f"{name} = {' | '.join(values)}" for name, values in fix.options.items()
            )
            extra = f" options: {options}." if options else ""
            words = _CLASS_WORDS[fix.fix_class]
            fix_lines.append(f"- {fix.id} ({fix.host}): {fix.description}; {words}.{extra}")
        fixes = (
            "If a fix is needed and one of these fits, you may request it with the fix tool. "
            "You cannot change anything yourself. Request a fix only when the evidence supports "
            "it, and still finish with the report tool. Requesting a fix is safe: the harness "
            "checks its conditions and whether the maintainer can be asked, and decides; it "
            "tells you what it did. So do not hold back a fix the evidence supports because "
            "the maintainer is away: request it, and the rules decide.\n"
            + "\n".join(fix_lines)
            + "\n\n"
        )
    return (
        "You are diagnosing a problem on a small network. You can inspect; you cannot change "
        "anything.\n\n"
        f"Hosts:\n{hosts}\n\n"
        "Use the run tool to inspect a host. Each command is checked against what that kind "
        "of host allows, listed below. Arguments may be given in any order. Use plain "
        "space-separated arguments: no pipes, redirection, quotes, wildcards or '='.\n\n"
        f"{chr(10).join(menus)}\n\n"
        f"{fixes}"
        "A command that is not allowed returns DENIED with the reason. Choose a different "
        "command rather than repeating it. Some commands have no recorded output in this "
        "exercise and say so; that is not evidence about the network.\n\n"
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
    meta: Mapping[str, object] | None = None,
) -> DrillResult:
    """Let ``model`` investigate ``scenario`` until it reports or a limit stops it.

    One step is one model turn. Every turn counts, including turns whose calls were refused.
    """
    messages: list[Message] = [
        {"role": "system", "content": system_prompt(scenario, policy)},
        {"role": "user", "content": scenario.briefing},
    ]
    report: Report | None = None
    steps = denied = tool_errors = 0
    stop: StopReason = "step_budget"
    error: str | None = None
    refused: set[tuple[str, str]] = set()  # (host, command) already denied in this drill
    requested_fixes: set[str] = set()
    tools = tools_for(scenario)
    transcript.append("start", 0, scenario=scenario.id, max_steps=max_steps, **dict(meta or {}))

    while steps < max_steps and report is None:
        try:
            turn = model.complete(messages, tools)
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
                if INVALID_ARGUMENTS in call.arguments:
                    raise _ToolError("the arguments are not valid JSON; send a JSON object")
                if call.name == "run":
                    reply, was_denied = _run(
                        call, scenario, policy, target, transcript, steps, refused
                    )
                    denied += was_denied
                elif call.name == "report":
                    report = _report(call, scenario)
                    transcript.append("report", steps, **_report_data(report))
                    stop = "report"
                    break
                elif call.name == "fix" and scenario.fixes:
                    reply = _fix(call, scenario, transcript, steps, requested_fixes)
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


def _fix(
    call: ToolCall,
    scenario: Scenario,
    transcript: Transcript,
    step: int,
    requested: set[str],
) -> str:
    """Handle one ``fix`` call: validate it, let the harness decide, record and reply."""
    fix_id, reason = call.arguments.get("id"), call.arguments.get("reason")
    if not isinstance(fix_id, str):
        raise _ToolError("id must be a string")
    fix = scenario.fix(fix_id)
    if fix is None:
        offered = ", ".join(f.id for f in scenario.fixes)
        raise _ToolError(f"unknown fix: {fix_id}. Offered: {offered}")
    if not isinstance(reason, str) or not reason.strip():
        raise _ToolError("reason must be a non-empty string")
    given = call.arguments.get("options", {})
    if not isinstance(given, Mapping):
        raise _ToolError("options must be an object")
    options: dict[str, str] = {}
    for name in given:
        if name not in fix.options:
            raise _ToolError(f"unknown option for {fix.id}: {name}")
    for name, allowed in fix.options.items():
        value = given.get(name, allowed[0])
        if value not in allowed:
            raise _ToolError(f"{name} must be one of: {', '.join(allowed)}")
        options[name] = str(value)
    if fix.id in requested:
        raise _ToolError(f"{fix.id} was already requested in this drill")
    requested.add(fix.id)
    assert scenario.state is not None  # loader guarantees state when fixes exist
    outcome, message = decide_fix(fix, scenario.state)
    transcript.append("fix", step, id=fix.id, reason=reason, options=options, outcome=outcome)
    return message


def _report_data(report: Report) -> dict[str, object]:
    data: dict[str, object] = {
        "category": report.category,
        "host": report.host,
        "component": report.component,
        "cause": report.cause,
        "evidence": list(report.evidence),
        "action": report.action,
        "recommendation": report.recommendation,
        "detail": report.detail,
    }
    if report.contributing is not None:
        data["contributing"] = {"host": report.contributing[0], "component": report.contributing[1]}
    return data


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
    refused: set[tuple[str, str]],
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
        key = (host.name, " ".join(command.split()))
        repeat = key in refused
        refused.add(key)
        transcript.append(
            "decision",
            step,
            allowed=False,
            argv=None,
            reason=decision.reason,
            denial=decision.kind,
            repeat=repeat,
        )
        if repeat:
            return (
                "DENIED again: you already asked for this and it was refused "
                f"({decision.reason}). Choose a different command."
            ), True
        return f"DENIED: {decision.reason}", True
    transcript.append(
        "decision",
        step,
        allowed=True,
        argv=list(decision.argv),
        reason=None,
        denial=None,
        repeat=False,
    )

    # The only place a command reaches a target, and only as the argument list just approved.
    result = target.run(host.name, decision.argv)
    transcript.append(
        "output",
        step,
        exit_code=result.exit_code,
        stdout=result.stdout,
        recorded=result.recorded,
        matched=result.matched,
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
    if not isinstance(component, str) or component not in COMPONENTS:
        raise _ToolError(f"component must be one of: {', '.join(sorted(COMPONENTS))}")
    detail = args.get("detail", "")
    if not isinstance(detail, str):
        raise _ToolError("detail must be a string")
    contributing = _contributing(args.get("contributing"), hosts)
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
    return Report(
        category, host, component, cause, tuple(evidence), action, recommendation, detail,
        contributing,
    )  # fmt: skip


def _contributing(value: object, hosts: Sequence[str]) -> tuple[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _ToolError("contributing must be an object with host and component")
    if set(value) - {"host", "component"}:
        raise _ToolError("contributing takes only host and component")
    host, component = value.get("host"), value.get("component")
    if not isinstance(host, str) or host not in hosts:
        raise _ToolError(f"contributing host must be one of: {', '.join(hosts)}")
    if not isinstance(component, str) or component not in COMPONENTS:
        raise _ToolError(f"contributing component must be one of: {', '.join(sorted(COMPONENTS))}")
    return (host, component)
