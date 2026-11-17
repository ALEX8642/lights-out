"""Command line: check a command against a policy, run a drill, score a transcript.

Exit codes: 0 for yes (allowed, reported, correct), 1 for no, 2 for a usage or input error.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from lights_out import __version__
from lights_out.agent import ScriptedModel, run_drill
from lights_out.policy import Allow, Policy, PolicyError
from lights_out.scenario import ScenarioError, load_scenario
from lights_out.scoring import Score, score
from lights_out.targets import ReplayTarget
from lights_out.transcript import Transcript, read_transcript

DEFAULT_POLICY = Path("policies/default.toml")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (ScenarioError, PolicyError, OSError, ValueError) as error:
        print(f"lights-out: {error}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lights-out",
        description="Guardrailed ops agent for a self-hosted LLM, tested on replayed incidents.",
    )
    parser.add_argument("--version", action="version", version=f"lights-out {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    policy = commands.add_parser("policy", help="work with a command policy")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    check = policy_commands.add_parser("check", help="say whether a command would be allowed")
    check.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="policy file")
    check.add_argument("--role", required=True, help="host role, for example linux")
    check.add_argument("text", metavar="COMMAND", help="the command, as one quoted string")
    check.set_defaults(handler=_policy_check)

    drill = commands.add_parser("drill", help="replay a scenario to a model")
    drill.add_argument("scenario", type=Path, help="scenario directory")
    drill.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="policy file")
    drill.add_argument(
        "--script", type=Path, required=True, help="JSON file of model turns to replay"
    )
    drill.add_argument("--out", type=Path, required=True, help="transcript file to write")
    drill.add_argument("--max-steps", type=int, default=20, help="model turns allowed (default 20)")
    drill.set_defaults(handler=_drill)

    score_parser = commands.add_parser("score", help="score a transcript against a scenario")
    score_parser.add_argument("scenario", type=Path, help="scenario directory")
    score_parser.add_argument("transcript", type=Path, help="transcript file from a drill")
    score_parser.add_argument("--json", action="store_true", help="print the score as JSON")
    score_parser.set_defaults(handler=_score)
    return parser


def _policy_check(args: argparse.Namespace) -> int:
    decision = Policy.from_toml(args.policy).check(args.role, args.text)
    if isinstance(decision, Allow):
        print(f"allow: {' '.join(decision.argv)}")
        return 0
    print(f"deny: {decision.reason}")
    return 1


def _drill(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    policy = Policy.from_toml(args.policy)
    model = ScriptedModel.from_json(args.script)
    result = run_drill(
        scenario,
        policy,
        ReplayTarget(scenario),
        model,
        Transcript(args.out),
        max_steps=args.max_steps,
    )
    print(
        f"stopped: {result.stop_reason} after {result.steps} steps "
        f"({result.denied} denied, {result.tool_errors} tool errors)"
    )
    print(f"transcript: {args.out}")
    return 0 if result.stop_reason == "report" else 1


def _score(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    events, truncated = read_transcript(args.transcript)
    if truncated:
        print(
            "lights-out: warning: transcript ends mid-line; scoring what is there", file=sys.stderr
        )
    result = score(scenario, events)
    if args.json:
        print(json.dumps({**dataclasses.asdict(result), "correct": result.correct}, indent=2))
    else:
        print(_summary(result))
    return 0 if result.correct else 1


def _summary(result: Score) -> str:
    def mark(ok: bool) -> str:
        return "ok" if ok else "WRONG"

    if result.action is None:
        action = "none given"
    elif result.action_forbidden:
        action = f"{result.action} (FORBIDDEN in this scenario)"
    else:
        action = result.action
    lines = [
        f"{result.scenario_id}: {'CORRECT' if result.correct else 'NOT CORRECT'}",
        f"  report made       {'yes' if result.reported else 'NO'}",
        f"  category          {mark(result.category_correct)}",
        f"  fault host        {mark(result.host_correct)}",
        f"  component         {mark(result.component_correct)}",
        f"  action            {action}",
        f"  key facts found   {len(result.key_facts_found)} of {result.key_facts_total}",
        f"  evidence run      {len(result.evidence_run)} of {result.evidence_total}",
        f"  refused, unsafe   {result.denied_unsafe}",
        f"  refused, unlisted {result.denied_unlisted}",
        f"  tool errors       {result.tool_errors}",
        f"  steps             {result.steps}",
        f"  tokens            {result.prompt_tokens} prompt, {result.completion_tokens} completion",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
