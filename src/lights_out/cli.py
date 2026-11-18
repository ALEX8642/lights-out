"""Command line: check a command against a policy, run a drill, score a transcript.

Exit codes: 0 for yes (allowed, reported, correct), 1 for no, 2 for a usage or input error.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from lights_out import __version__
from lights_out.agent import ModelClient, ScriptedModel, run_drill
from lights_out.batch import GroupSummary, ModelFactory, RunRecord, run_batch, summarise
from lights_out.endpoint import OpenAIClient
from lights_out.energy import CsvSource, DrillEnergy, HomeAssistantSource, PowerSource, price_batch
from lights_out.policy import Allow, Policy, PolicyError
from lights_out.review import collect_refusals
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
    drill.add_argument("--script", type=Path, help="JSON file of model turns to replay")
    drill.add_argument(
        "--endpoint", help="OpenAI-compatible base URL, for example http://host:8000/v1"
    )
    drill.add_argument("--model", help="model name to request from --endpoint")
    drill.add_argument(
        "--api-key-env", metavar="VAR", help="environment variable holding the endpoint's API key"
    )
    drill.add_argument(
        "--temperature", type=float, default=0.0, help="sampling temperature (default 0)"
    )
    drill.add_argument("--out", type=Path, required=True, help="transcript file to write")
    drill.add_argument("--max-steps", type=int, default=20, help="model turns allowed (default 20)")
    drill.set_defaults(handler=_drill)

    batch = commands.add_parser("batch", help="run many drills and summarise them")
    batch.add_argument("scenarios", type=Path, nargs="+", metavar="SCENARIO", help="scenario dirs")
    batch.add_argument(
        "--out", type=Path, required=True, help="directory for transcripts and summary"
    )
    batch.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="policy file")
    batch.add_argument(
        "--scripted", action="store_true", help="use each scenario's own script.json"
    )
    batch.add_argument("--endpoint", help="OpenAI-compatible base URL")
    batch.add_argument("--model", help="model name to request from --endpoint")
    batch.add_argument("--api-key-env", metavar="VAR", help="variable holding the API key")
    batch.add_argument("--repeats", type=int, default=5, help="drills per scenario and temperature")
    batch.add_argument(
        "--temperatures", default="0", help="comma-separated, for example 0,0.7 (default 0)"
    )
    batch.add_argument("--max-steps", type=int, default=20, help="model turns allowed per drill")
    batch.set_defaults(handler=_batch)

    energy = commands.add_parser("energy", help="price a batch's drills from wall-plug power")
    energy.add_argument("batch_dir", type=Path, metavar="BATCH_DIR", help="a batch --out directory")
    energy.add_argument("--csv", type=Path, help="power readings: CSV with time,watts columns")
    energy.add_argument("--ha-url", help="Home Assistant base URL")
    energy.add_argument("--entity", help="Home Assistant power sensor entity id")
    energy.add_argument("--token-env", metavar="VAR", help="variable holding the HA token")
    energy.add_argument(
        "--idle-watts", type=float, required=True, help="baseline measured in a quiet window"
    )
    energy.set_defaults(handler=_energy)
    refusals = commands.add_parser("refusals", help="group refused commands for review")
    refusals.add_argument("dirs", type=Path, nargs="+", metavar="DIR", help="transcript dirs")
    refusals.add_argument(
        "--scenarios", type=Path, default=Path("scenarios"), help="where scenario dirs live"
    )
    refusals.set_defaults(handler=_refusals)

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
    if (args.script is None) == (args.endpoint is None):
        print("lights-out: give exactly one of --script or --endpoint", file=sys.stderr)
        return 2
    model: ModelClient
    meta: dict[str, object]
    if args.endpoint is not None:
        if not args.model:
            print("lights-out: --endpoint needs --model", file=sys.stderr)
            return 2
        api_key = None
        if args.api_key_env:
            api_key = os.environ.get(args.api_key_env)
            if not api_key:
                print(f"lights-out: {args.api_key_env} is not set", file=sys.stderr)
                return 2
        model = OpenAIClient(
            args.endpoint, args.model, api_key=api_key, temperature=args.temperature
        )
        meta = {"model": args.model, "endpoint": args.endpoint, "temperature": args.temperature}
    else:
        model = ScriptedModel.from_json(args.script)
        meta = {"model": "scripted", "script": str(args.script)}
    scenario = load_scenario(args.scenario)
    policy = Policy.from_toml(args.policy)
    result = run_drill(
        scenario,
        policy,
        ReplayTarget(scenario),
        model,
        Transcript(args.out),
        max_steps=args.max_steps,
        meta={**meta, "lights_out": __version__},
    )
    print(
        f"stopped: {result.stop_reason} after {result.steps} steps "
        f"({result.denied} denied, {result.tool_errors} tool errors)"
    )
    print(f"transcript: {args.out}")
    return 0 if result.stop_reason == "report" else 1


def _batch(args: argparse.Namespace) -> int:
    if args.scripted == (args.endpoint is not None):
        print("lights-out: give exactly one of --scripted or --endpoint", file=sys.stderr)
        return 2
    if args.repeats < 1:
        print("lights-out: --repeats must be at least 1", file=sys.stderr)
        return 2
    temperatures = tuple(float(t) for t in args.temperatures.split(","))
    meta: dict[str, object]
    make_model: ModelFactory
    if args.scripted:
        meta = {"model": "scripted"}

        def make_model(scenario: Path, temperature: float) -> ModelClient:
            return ScriptedModel.from_json(scenario / "script.json")

    else:
        if not args.model:
            print("lights-out: --endpoint needs --model", file=sys.stderr)
            return 2
        api_key = None
        if args.api_key_env:
            api_key = os.environ.get(args.api_key_env)
            if not api_key:
                print(f"lights-out: {args.api_key_env} is not set", file=sys.stderr)
                return 2
        endpoint, model_name = args.endpoint, args.model
        meta = {"model": model_name, "endpoint": endpoint}

        def make_model(scenario: Path, temperature: float) -> ModelClient:
            return OpenAIClient(endpoint, model_name, api_key=api_key, temperature=temperature)

    def progress(record: RunRecord) -> None:
        mark = "correct" if record.correct else "NOT correct"
        print(
            f"{record.scenario} t={record.temperature:g} #{record.repeat}: {mark} "
            f"({record.steps} steps, {record.wall_seconds:.0f} s)",
            flush=True,
        )

    records = run_batch(
        args.scenarios,
        Policy.from_toml(args.policy),
        make_model,
        args.out,
        repeats=args.repeats,
        temperatures=temperatures,
        max_steps=args.max_steps,
        meta={**meta, "lights_out": __version__},
        on_record=progress,
    )
    print()
    print(_batch_table(summarise(records)))
    return 0


def _batch_table(groups: list[GroupSummary]) -> str:
    header = (
        f"{'scenario':<22} {'temp':>4} {'correct':>8} {'95% interval':>14} {'fix ok':>7} "
        f"{'unsafe':>6} {'unlisted':>8} {'steps':>6} {'seconds':>8} {'tokens':>8}"
    )
    lines = [header]
    for g in groups:
        low, high = g.correct_interval
        fix = f"{g.fix_correct}/{g.fix_scored}" if g.fix_scored else "-"
        lines.append(
            f"{g.scenario:<22} {g.temperature:>4g} {f'{g.correct}/{g.runs}':>8} "
            f"{f'{low:.0%}–{high:.0%}':>14} {fix:>7} {g.denied_unsafe:>6} {g.denied_unlisted:>8} "
            f"{g.mean_steps:>6.1f} {g.mean_wall_seconds:>8.0f} {g.mean_tokens:>8.0f}"
        )
    return "\n".join(lines)


def _energy(args: argparse.Namespace) -> int:
    if (args.csv is None) == (args.ha_url is None):
        print("lights-out: give exactly one of --csv or --ha-url", file=sys.stderr)
        return 2
    source: PowerSource
    if args.csv is not None:
        source = CsvSource(args.csv)
    else:
        if not args.entity or not args.token_env:
            print("lights-out: --ha-url needs --entity and --token-env", file=sys.stderr)
            return 2
        token = os.environ.get(args.token_env)
        if not token:
            print(f"lights-out: {args.token_env} is not set", file=sys.stderr)
            return 2
        source = HomeAssistantSource(args.ha_url, args.entity, token)
    priced = price_batch(args.batch_dir / "summary.jsonl", source, args.idle_watts)
    with (args.batch_dir / "energy.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([f.name for f in dataclasses.fields(DrillEnergy)])
        for row in priced:
            writer.writerow(dataclasses.astuple(row))
    groups: dict[tuple[str, float], list[DrillEnergy]] = {}
    for row in priced:
        groups.setdefault((row.scenario, row.temperature), []).append(row)
    print(
        f"{'scenario':<22} {'temp':>4} {'correct':>8} {'Wh/drill':>9} {'above idle':>11} "
        f"{'Wh/correct':>11}"
    )
    for (scenario, temperature), rows in groups.items():
        known = [r for r in rows if r.wh is not None]
        total = sum(r.wh or 0.0 for r in known)
        above = sum(r.wh_above_idle or 0.0 for r in known)
        correct = sum(r.correct for r in rows)
        per_correct = f"{total / correct:.2f}" if correct else "-"
        mean = total / len(known) if known else float("nan")
        mean_above = above / len(known) if known else float("nan")
        print(
            f"{scenario:<22} {temperature:>4g} {f'{correct}/{len(rows)}':>8} {mean:>9.2f} "
            f"{mean_above:>11.2f} {per_correct:>11}"
        )
    print(
        f"\nidle baseline {args.idle_watts:g} W; per-drill rows in {args.batch_dir / 'energy.csv'}"
    )
    return 0


def _refusals(args: argparse.Namespace) -> int:
    groups = collect_refusals(args.dirs, args.scenarios)
    print(f"{'kind':<9} {'role':<9} {'program':<12} {'count':>5} {'drills':>6}  reason / examples")
    for g in groups:
        print(f"{g.kind:<9} {g.role:<9} {g.program:<12} {g.count:>5} {g.drills:>6}  {g.reason}")
        for example in g.examples:
            print(f"{'':<46}  {example}")
    return 0


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


def _fix_line(result: Score) -> list[str]:
    if result.fix_correct is None:
        return []
    asked = ", ".join(result.fixes_requested) or "none"
    verdict = "ok" if result.fix_correct else "WRONG"
    outcome = f" -> {result.fix_outcome}" if result.fix_outcome else ""
    return [f"  fix decision      {verdict}: requested {asked}{outcome}"]


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
        *_fix_line(result),
        f"  steps             {result.steps}",
        f"  tokens            {result.prompt_tokens} prompt, {result.completion_tokens} completion",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
