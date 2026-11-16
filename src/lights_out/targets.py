"""Targets: where an allowed command is run.

A target receives an argument list the policy has already approved and returns what the command
printed. ``ReplayTarget`` answers from a scenario's recordings. A target for real hosts comes
later and must keep the same contract: it is never given anything the policy did not approve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lights_out.scenario import Scenario

MAX_OUTPUT_CHARS = 8000
TRUNCATION_MARKER = "\n[lights-out: output truncated]"
NO_RECORDING = "lights-out: no output recorded for this command in this scenario"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    recorded: bool  # False when the scenario has no recording, so gaps are visible


class Target(Protocol):
    def run(self, host: str, argv: tuple[str, ...]) -> CommandResult: ...


class ReplayTarget:
    """Answers commands from a scenario's recordings. Nothing is executed."""

    def __init__(self, scenario: Scenario) -> None:
        self._recordings = scenario.recordings

    def run(self, host: str, argv: tuple[str, ...]) -> CommandResult:
        recording = self._recordings.get((host, argv))
        if recording is None:
            return CommandResult(127, NO_RECORDING, False)
        stdout = recording.stdout
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + TRUNCATION_MARKER
        return CommandResult(recording.exit_code, stdout, True)
