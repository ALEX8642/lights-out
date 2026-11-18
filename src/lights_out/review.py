"""Review loop: what models asked for and were refused, grouped for a person to decide on.

Reads every transcript under the given directories. Each refusal is grouped by its kind
(unsafe or unlisted), the role of the host it was aimed at, the program, and the reason with its
details removed. Unlisted groups are candidates for the allowlist; unsafe groups are attempts to
watch. Nothing is added to any list automatically: a person reviews each group (README, "How the
lists grow").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lights_out.scenario import ScenarioError, load_scenario
from lights_out.transcript import read_transcript

MAX_EXAMPLES = 3


@dataclass(frozen=True)
class RefusalGroup:
    kind: str
    role: str
    program: str
    reason: str
    count: int
    drills: int
    examples: tuple[str, ...]


def _reason_class(reason: str) -> str:
    """The reason without its details, for grouping."""
    head = reason.split(". ", 1)[0]
    match = re.match(r"(refused: [a-z ]+|[a-z][a-z ]*?)(:| for |$)", head)
    return match.group(1).strip() if match else head


def collect_refusals(directories: Sequence[Path], scenarios_root: Path) -> list[RefusalGroup]:
    roles: dict[str, dict[str, str]] = {}

    def role_of(scenario_id: str, host: str) -> str:
        if scenario_id not in roles:
            try:
                scenario = load_scenario(scenarios_root / scenario_id)
                roles[scenario_id] = {h.name: h.role for h in scenario.hosts}
            except ScenarioError:
                roles[scenario_id] = {}
        return roles[scenario_id].get(host, "?")

    counts: dict[tuple[str, str, str, str], list[str]] = {}
    drills: dict[tuple[str, str, str, str], set[Path]] = {}
    for directory in directories:
        for path in sorted(directory.rglob("*.jsonl")):
            if path.name == "summary.jsonl":
                continue
            events, _ = read_transcript(path)
            scenario_id = str(events[0].data.get("scenario", "")) if events else ""
            proposal: dict[str, object] = {}
            for event in events:
                if event.kind == "proposal":
                    proposal = dict(event.data)
                elif event.kind == "decision" and event.data.get("allowed") is False:
                    command = str(proposal.get("command", ""))
                    key = (
                        str(event.data.get("denial") or "unlisted"),
                        role_of(scenario_id, str(proposal.get("host", ""))),
                        command.split()[0] if command.split() else "",
                        _reason_class(str(event.data.get("reason", ""))),
                    )
                    counts.setdefault(key, []).append(command)
                    drills.setdefault(key, set()).add(path)
    groups = [
        RefusalGroup(
            kind=key[0],
            role=key[1],
            program=key[2],
            reason=key[3],
            count=len(commands),
            drills=len(drills[key]),
            examples=tuple(dict.fromkeys(commands))[:MAX_EXAMPLES],
        )
        for key, commands in counts.items()
    ]
    return sorted(groups, key=lambda g: (g.kind != "unlisted", -g.count, g.role, g.program))
