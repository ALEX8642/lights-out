"""Scenario format: a recorded incident that can be replayed to an agent.

A scenario is a directory:

    scenario.toml         title, briefing shown to the agent, hosts and their roles
    outputs/<host>.toml   recorded output of commands on that host
    truth.toml            fault host, cause, key facts and traps; never shown to the agent

The format is described in docs/adr/0003-scenario-format.md.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = frozenset(
    {"dns", "routing", "dhcp", "wan", "service", "hardware", "config", "monitoring"}
)

# The kinds of action a report can recommend. A closed list, so that a recommendation is a
# choice that can be compared exactly instead of prose that has to be interpreted.
ACTIONS = frozenset(
    {
        "none",  # nothing to do: it heals by itself, or is not a fault
        "change_config",
        "restart_service",
        "reboot_host",
        "replace_hardware",
        "contact_provider",  # the fault is outside the network, for example the ISP
        "investigate_further",  # the evidence available does not settle it
    }
)

EXTERNAL = "external"
"""The fault host when the cause is not on any host in the scenario."""


class ScenarioError(ValueError):
    """The scenario directory is missing, incomplete or inconsistent."""


@dataclass(frozen=True)
class Host:
    name: str
    role: str


@dataclass(frozen=True)
class Recording:
    """What a command printed, and how it exited, when the incident was recorded."""

    exit_code: int
    stdout: str


@dataclass(frozen=True)
class KeyFact:
    """Something a correct diagnosis has to establish.

    The fact counts as found when any phrase in ``any_of`` appears in the agent's report.
    ``evidence`` names the (host, command) that reveals it, when there is one.
    """

    id: str
    any_of: tuple[str, ...]
    evidence: tuple[str, str] | None


@dataclass(frozen=True)
class Truth:
    """The answer key. Never shown to the agent."""

    category: str
    host: str  # the host at fault, or EXTERNAL
    component: tuple[str, ...]  # accepted phrases for the part at fault
    key_facts: tuple[KeyFact, ...]
    forbidden_actions: tuple[str, ...]  # entries of ACTIONS that are the scenario's trap


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    briefing: str
    hosts: tuple[Host, ...]
    recordings: Mapping[tuple[str, tuple[str, ...]], Recording]
    truth: Truth

    def host(self, name: str) -> Host | None:
        return next((host for host in self.hosts if host.name == name), None)


def load_scenario(path: Path) -> Scenario:
    """Load and validate the scenario in directory ``path``."""
    if not path.is_dir():
        raise ScenarioError(f"{path}: not a directory")

    where = "scenario.toml"
    data = _read(path / where, where)
    _only_keys(data, {"id", "title", "briefing", "hosts"}, where)
    scenario_id = _text(data, "id", where)
    if scenario_id != path.name:
        raise ScenarioError(f"{where}: id {scenario_id!r} does not match directory {path.name!r}")
    hosts = _hosts(data.get("hosts"), where)
    names = {host.name for host in hosts}

    recordings: dict[tuple[str, tuple[str, ...]], Recording] = {}
    outputs = path / "outputs"
    for file in sorted(outputs.glob("*.toml")) if outputs.is_dir() else []:
        where_file = f"outputs/{file.name}"
        host = file.stem
        if host not in names:
            raise ScenarioError(f"{where_file}: unknown host: {host}")
        for argv, recording in _recordings(_read(file, where_file), where_file):
            if (host, argv) in recordings:
                raise ScenarioError(f"{where_file}: duplicate command: {' '.join(argv)}")
            recordings[(host, argv)] = recording

    return Scenario(
        id=scenario_id,
        title=_text(data, "title", where),
        briefing=_text(data, "briefing", where),
        hosts=hosts,
        recordings=recordings,
        truth=_truth(_read(path / "truth.toml", "truth.toml"), names),
    )


def _read(file: Path, where: str) -> Mapping[str, object]:
    try:
        with file.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise ScenarioError(f"{where}: file is missing") from None
    except tomllib.TOMLDecodeError as error:
        raise ScenarioError(f"{where}: {error}") from error


def _only_keys(table: Mapping[str, object], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ScenarioError(f"{where}: unknown key(s): {', '.join(unknown)}")


def _text(table: Mapping[str, object], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{where}: {key} must be a non-empty string")
    return value


def _texts(
    table: Mapping[str, object], key: str, where: str, *, allow_empty: bool
) -> tuple[str, ...]:
    value = table.get(key, [] if allow_empty else None)
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item for item in value)
        or (not value and not allow_empty)
    ):
        need = "a list of strings" if allow_empty else "a non-empty list of non-empty strings"
        raise ScenarioError(f"{where}: {key} must be {need}")
    return tuple(value)


def _tables(value: object, key: str, where: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not value or not all(isinstance(v, Mapping) for v in value):
        raise ScenarioError(f"{where}: {key} must be a non-empty list of tables")
    return value


def _hosts(value: object, where: str) -> tuple[Host, ...]:
    hosts: list[Host] = []
    for table in _tables(value, "hosts", where):
        _only_keys(table, {"name", "role"}, f"{where}: hosts")
        host = Host(_text(table, "name", where), _text(table, "role", where))
        if host.name == EXTERNAL:
            raise ScenarioError(f"{where}: host name {EXTERNAL!r} is reserved")
        if any(host.name == other.name for other in hosts):
            raise ScenarioError(f"{where}: duplicate host: {host.name}")
        hosts.append(host)
    return tuple(hosts)


def _recordings(data: Mapping[str, object], where: str) -> list[tuple[tuple[str, ...], Recording]]:
    _only_keys(data, {"command"}, where)
    recordings: list[tuple[tuple[str, ...], Recording]] = []
    for table in _tables(data.get("command"), "command", where):
        _only_keys(table, {"argv", "exit_code", "stdout"}, f"{where}: command")
        argv = _texts(table, "argv", where, allow_empty=False)
        stdout = table.get("stdout")
        exit_code = table.get("exit_code", 0)
        if not isinstance(stdout, str):
            raise ScenarioError(f"{where}: stdout must be a string ({' '.join(argv)})")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise ScenarioError(f"{where}: exit_code must be an integer ({' '.join(argv)})")
        recordings.append((argv, Recording(exit_code, stdout)))
    return recordings


def _truth(data: Mapping[str, object], hosts: set[str]) -> Truth:
    where = "truth.toml"
    _only_keys(data, {"category", "host", "component", "forbidden_actions", "key_facts"}, where)
    category = _text(data, "category", where)
    if category not in CATEGORIES:
        raise ScenarioError(
            f"{where}: category {category!r} is not one of {', '.join(sorted(CATEGORIES))}"
        )
    fault_host = _text(data, "host", where)
    if fault_host != EXTERNAL and fault_host not in hosts:
        raise ScenarioError(f"{where}: host: unknown host: {fault_host}")
    forbidden = _texts(data, "forbidden_actions", where, allow_empty=True)
    unknown = [action for action in forbidden if action not in ACTIONS]
    if unknown:
        raise ScenarioError(
            f"{where}: forbidden_actions: {', '.join(unknown)} not in {', '.join(sorted(ACTIONS))}"
        )
    facts: list[KeyFact] = []
    for table in _tables(data.get("key_facts"), "key_facts", where):
        _only_keys(table, {"id", "any_of", "evidence"}, f"{where}: key_facts")
        fact_id = _text(table, "id", where)
        if any(fact_id == other.id for other in facts):
            raise ScenarioError(f"{where}: duplicate key fact: {fact_id}")
        evidence = table.get("evidence")
        pair: tuple[str, str] | None = None
        if evidence is not None:
            if not isinstance(evidence, Mapping):
                raise ScenarioError(f"{where}: evidence of {fact_id} must be a table")
            _only_keys(evidence, {"host", "command"}, f"{where}: evidence of {fact_id}")
            pair = (_text(evidence, "host", where), _text(evidence, "command", where))
            if pair[0] not in hosts:
                raise ScenarioError(f"{where}: evidence of {fact_id}: unknown host: {pair[0]}")
        facts.append(KeyFact(fact_id, _texts(table, "any_of", where, allow_empty=False), pair))
    return Truth(
        category=category,
        host=fault_host,
        component=_texts(data, "component", where, allow_empty=False),
        key_facts=tuple(facts),
        forbidden_actions=forbidden,
    )
