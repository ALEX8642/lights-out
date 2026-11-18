"""Targets: where an allowed command is run.

A target receives an argument list the policy has already approved and returns what the command
printed. ``ReplayTarget`` answers from a scenario's recordings. A target for real hosts comes
later and must keep the same contract: it is never given anything the policy did not approve.

Replay matching (docs/adr/0008-replay-matching.md). A recording answers a command when:

1. the argument lists are identical ("exact"), or
2. both reduce to the same question ("equivalent"): arguments in any order; presentation-only
   options removed (``dig +short``, ``journalctl --no-pager -n 50``, ``ls -l``); default values
   removed (``dig ... A``); synonyms unified (``ip a`` = ``ip addr show``); ``.service`` dropped
   from unit names; combined short flags compared as letter sets (``ss -tulpn`` = ``ss -nlptu``).
   For socket listings a recording that shows more answers a narrower question.

``cat`` of several files joins the recordings of each file. Everything is deterministic: the
first equivalent recording in file order wins.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from lights_out.derive import HostState, derive
from lights_out.scenario import Recording, Scenario

MAX_OUTPUT_CHARS = 8000
TRUNCATION_MARKER = "\n[lights-out: output truncated]"
NO_RECORDING = "lights-out: no output recorded for this command in this scenario"

type Matched = Literal["exact", "equivalent", "derived"]


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    recorded: bool  # False when the scenario has no recording, so gaps are visible
    matched: Matched | None = None  # how the recording was found


class Target(Protocol):
    def run(self, host: str, argv: tuple[str, ...]) -> CommandResult: ...


@dataclass(frozen=True)
class _Rules:
    """How to reduce one program's arguments to the question they ask."""

    drop: frozenset[str] = frozenset()  # options that only change presentation
    drop_with_value: frozenset[str] = frozenset()  # such options that take a value
    drop_pairs: frozenset[tuple[str, str]] = frozenset()  # an option with its default value
    synonyms: Mapping[str, str] = field(default_factory=dict)  # word -> canonical word
    drop_all_flags: bool = False  # every option only changes presentation (ls)
    letter_flags: bool = False  # combined short flags compare as letter sets
    superset_answers: bool = False  # a recording showing more answers a narrower question
    strip_unit_suffix: bool = False  # "x.service" is "x"


_DIG_PRESENTATION = frozenset(
    "+short +noall +all +answer +noanswer +stats +nostats +cmd +nocmd +comments +nocomments "
    "+multiline +question +noquestion +authority +additional +identify -4 A".split()
)

RULES: Mapping[str, _Rules] = {
    "dig": _Rules(drop=_DIG_PRESENTATION, drop_pairs=frozenset({("-t", "A")})),
    "drill": _Rules(drop=frozenset({"A", "-4"}), drop_pairs=frozenset({("-t", "A")})),
    "host": _Rules(drop=frozenset({"-v", "-4"}), drop_pairs=frozenset({("-t", "A")})),
    "journalctl": _Rules(
        drop=frozenset({"--no-pager", "-q", "-x"}),
        drop_with_value=frozenset({"-n", "--lines", "-o", "--output"}),
        synonyms={"--unit": "-u", "--priority": "-p", "--reverse": "-r", "--boot": "-b"},
        strip_unit_suffix=True,
    ),
    "systemctl": _Rules(
        drop=frozenset({"--no-pager", "-l", "--full", "--plain", "--no-legend"}),
        synonyms={"-t": "--type"},
        strip_unit_suffix=True,
    ),
    "service": _Rules(synonyms={"onestatus": "status"}),
    "ls": _Rules(drop_all_flags=True),
    "grep": _Rules(drop=frozenset({"-n", "-H", "-h"})),
    "head": _Rules(drop_with_value=frozenset({"-n"})),
    "tail": _Rules(drop_with_value=frozenset({"-n"})),
    "ping": _Rules(drop=frozenset({"-q", "-n"}), drop_with_value=frozenset({"-c", "-W", "-t"})),
    "traceroute": _Rules(drop=frozenset({"-n"}), drop_with_value=frozenset({"-m", "-w", "-q"})),
    "tracepath": _Rules(drop=frozenset({"-n"}), drop_with_value=frozenset({"-m"})),
    "ip": _Rules(
        drop=frozenset({"-br", "-brief", "show", "list", "lst", "sh"}),
        synonyms={"a": "addr", "address": "addr", "r": "route", "l": "link", "n": "neigh"},
    ),
    "docker": _Rules(
        drop=frozenset({"--no-stream", "-t", "--timestamps"}),
        drop_with_value=frozenset({"--tail", "-n"}),
        synonyms={"--all": "-a"},
    ),
    "ss": _Rules(letter_flags=True, superset_answers=True),
    "netstat": _Rules(
        letter_flags=True, superset_answers=True, drop_pairs=frozenset({("-f", "inet")})
    ),
    "arp": _Rules(letter_flags=True, superset_answers=True),
    "dmesg": _Rules(drop=frozenset({"-a", "-T", "-x", "-H"})),
    "sockstat": _Rules(letter_flags=True, superset_answers=True),
    "df": _Rules(letter_flags=True),
    "free": _Rules(letter_flags=True),
    "uname": _Rules(letter_flags=True),
}


@dataclass(frozen=True)
class _Question:
    program: str
    letters: frozenset[str]
    words: tuple[str, ...]  # sorted

    def answered_by(self, recording: _Question, superset: bool) -> bool:
        if self.program != recording.program or self.words != recording.words:
            return False
        if superset:
            return self.letters <= recording.letters
        return self.letters == recording.letters


def _question(argv: tuple[str, ...]) -> _Question:
    program, arguments = argv[0], list(argv[1:])
    rules = RULES.get(program, _Rules())
    words: list[str] = []
    letters: set[str] = set()
    position = 0
    while position < len(arguments):
        argument = arguments[position]
        following = arguments[position + 1] if position + 1 < len(arguments) else None
        if following is not None and (argument, following) in rules.drop_pairs:
            position += 2
            continue
        if argument in rules.drop_with_value:
            position += 2
            continue
        position += 1
        argument = rules.synonyms.get(argument, argument)
        if argument in rules.drop:
            continue
        is_flag = argument.startswith("-") and len(argument) > 1
        if is_flag and rules.drop_all_flags:
            continue
        if is_flag and rules.letter_flags and not argument.startswith("--"):
            letters.update(argument[1:])
            continue
        if rules.strip_unit_suffix:
            argument = argument.removesuffix(".service")
        if argument.startswith("/") and len(argument) > 1:
            argument = argument.rstrip("/")
        words.append(argument)
    return _Question(program, frozenset(letters), tuple(sorted(words)))


def same_question(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether two argument lists ask the same question under the replay rules."""
    return a == b or _question(a) == _question(b)


class ReplayTarget:
    """Answers commands from a scenario's recordings. Nothing is executed."""

    def __init__(self, scenario: Scenario) -> None:
        self._recordings = scenario.recordings
        self._by_host: dict[str, list[tuple[_Question, Recording]]] = {}
        for (host, argv), recording in scenario.recordings.items():
            self._by_host.setdefault(host, []).append((_question(argv), recording))
        self._closed: dict[str, HostState] = {
            host.name: HostState(
                {
                    argv: rec
                    for (name, argv), rec in scenario.recordings.items()
                    if name == host.name
                }
            )
            for host in scenario.hosts
            if host.closed_world
        }

    def run(self, host: str, argv: tuple[str, ...]) -> CommandResult:
        found = self._find(host, argv)
        if found is None and host in self._closed:
            derived = derive(self._closed[host], argv)
            if derived is not None:
                stdout = _truncate(derived.stdout)
                return CommandResult(derived.exit_code, stdout, True, "derived")
        if found is None and argv[0] == "cat" and len(argv) > 2:
            return self._cat_each(host, argv[1:])
        if found is None:
            return CommandResult(127, NO_RECORDING, False, None)
        recording, matched = found
        return CommandResult(recording.exit_code, _truncate(recording.stdout), True, matched)

    def _find(self, host: str, argv: tuple[str, ...]) -> tuple[Recording, Matched] | None:
        exact = self._recordings.get((host, argv))
        if exact is not None:
            return exact, "exact"
        question = _question(argv)
        superset = RULES.get(argv[0], _Rules()).superset_answers
        for recorded, recording in self._by_host.get(host, []):
            if question.answered_by(recorded, superset):
                return recording, "equivalent"
        return None

    def _cat_each(self, host: str, files: tuple[str, ...]) -> CommandResult:
        parts: list[str] = []
        any_found, all_found = False, True
        for path in files:
            found = self._find(host, ("cat", path))
            if found is None:
                all_found = False
                parts.append(f"lights-out: no output recorded for {path} in this scenario\n")
            else:
                any_found = True
                parts.append(found[0].stdout)
        if not any_found:
            return CommandResult(127, NO_RECORDING, False, None)
        return CommandResult(0 if all_found else 1, _truncate("".join(parts)), True, "equivalent")


def _truncate(stdout: str) -> str:
    if len(stdout) > MAX_OUTPUT_CHARS:
        return stdout[:MAX_OUTPUT_CHARS] + TRUNCATION_MARKER
    return stdout
