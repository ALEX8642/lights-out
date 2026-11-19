"""Scenario format: a recorded incident that can be replayed to an agent.

A scenario is a directory:

    scenario.toml         title, briefing shown to the agent, hosts and their roles
    outputs/<host>.toml   recorded output of commands on that host
    truth.toml            fault host, cause, key facts and traps; never shown to the agent

The format is described in docs/adr/0003-scenario-format.md.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Where the fault lies. Kinds of cause ("misconfiguration") are deliberately not categories:
# they overlap with every area, and the kind of fix is captured by the report's action instead.
CATEGORIES = frozenset({"dns", "routing", "dhcp", "wan", "service", "hardware", "monitoring"})

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

# The parts a fault can be in. A closed list, so the report's component is a choice compared
# exactly, like the host and the action (docs/adr/0012-closed-components.md). Deliberately
# generic and shared by every scenario: a list made per scenario would hint at the answer.
COMPONENTS = frozenset(
    {
        "modem",  # the provider's modem or ONT
        "provider_network",  # beyond the modem: the ISP
        "wan_link",  # cable, port or link negotiation on the firewall's WAN side
        "wan_dhcp_lease",  # the firewall's WAN address lease from the provider
        "wan_nic_driver",  # the firewall's WAN network interface or its driver
        "firewall_rules",
        "nat",
        "routing_table",
        "vpn",
        "dhcp_server",  # addresses handed out on the local network
        "dns_resolver_config",
        "dns_resolver_service",  # the resolver process itself: crashed, hung, overloaded
        "dns_rate_limit",
        "dns_records",  # the zone data: a wrong or missing record
        "time_sync",
        "certificate",
        "disk",
        "memory",
        "power",
        "service_config",  # another service's configuration
        "service_process",  # another service's process: crashed, hung, not started
        "monitoring_config",  # the monitor itself is wrong
        "client_config",  # the client's own settings
        "other",
    }
)

# How the harness may handle a fix (docs/adr/0009-fix-table-and-approval.md).
FIX_CLASSES = frozenset({"act_alone", "ask_then_act", "approval_only"})
REACHABILITY = frozenset({"online", "home", "away"})  # can the maintainer be asked, and how
APPROVAL = frozenset({"approve", "deny", "none"})  # what he answers, if asked
# What the harness did with a requested fix.
OUTCOMES = frozenset(
    {"acted", "acted_unreachable", "asked_approved", "asked_denied", "asked_no_answer", "refused"}
)
NO_FIX = "none"
_FIX_ID = re.compile(r"[a-z][a-z0-9_]*")

EXTERNAL = "external"
"""The fault host when the cause is not on any host in the scenario."""


class ScenarioError(ValueError):
    """The scenario directory is missing, incomplete or inconsistent."""


@dataclass(frozen=True)
class Host:
    name: str
    role: str
    closed_world: bool = False
    """The recordings are the host's whole state: files and services not recorded do not exist."""


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
class Fix:
    """A scripted fix the scenario offers. In drills it is only ever simulated."""

    id: str
    host: str
    description: str
    fix_class: str  # one of FIX_CLASSES
    preconditions: tuple[str, ...]  # names; their values are in the scenario's state
    options: Mapping[str, tuple[str, ...]]  # option -> allowed values


@dataclass(frozen=True)
class FixState:
    """What the fixes' precondition checks would see, and whether the maintainer can be asked."""

    reachability: str
    approval: str
    preconditions: Mapping[str, bool]


@dataclass(frozen=True)
class Truth:
    """The answer key. Never shown to the agent."""

    category: str
    host: str  # the host at fault, or EXTERNAL
    component: tuple[str, ...]  # accepted ids from COMPONENTS for the part at fault
    key_facts: tuple[KeyFact, ...]
    forbidden_actions: tuple[str, ...]  # entries of ACTIONS that are the scenario's trap
    expected_fix: str | None = None  # a fix id, NO_FIX, or None when the scenario offers no fixes
    expected_outcome: str | None = None  # one of OUTCOMES, with an expected fix id
    forbidden_fixes: tuple[str, ...] = ()
    # Phrases for the part at fault, used only for reports written before the component was a
    # closed list (their component is free text).
    component_phrases: tuple[str, ...] = ()
    # A condition that made the fault show without being it: (host, accepted component ids).
    contributing: tuple[str, tuple[str, ...]] | None = None
    # More areas the fault genuinely sits in (a hung WAN driver is "hardware" and "wan").
    other_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    briefing: str
    hosts: tuple[Host, ...]
    recordings: Mapping[tuple[str, tuple[str, ...]], Recording]
    truth: Truth
    fixes: tuple[Fix, ...] = ()
    state: FixState | None = None

    def host(self, name: str) -> Host | None:
        return next((host for host in self.hosts if host.name == name), None)

    def fix(self, fix_id: str) -> Fix | None:
        return next((fix for fix in self.fixes if fix.id == fix_id), None)


def load_scenario(path: Path) -> Scenario:
    """Load and validate the scenario in directory ``path``."""
    if not path.is_dir():
        raise ScenarioError(f"{path}: not a directory")

    where = "scenario.toml"
    data = _read(path / where, where)
    _only_keys(data, {"id", "title", "briefing", "hosts", "fixes", "state"}, where)
    scenario_id = _text(data, "id", where)
    if scenario_id != path.name:
        raise ScenarioError(f"{where}: id {scenario_id!r} does not match directory {path.name!r}")
    hosts = _hosts(data.get("hosts"), where)
    names = {host.name for host in hosts}
    fixes = _fixes(data.get("fixes"), names, where)
    state = _state(data.get("state"), fixes, where)

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
        truth=_truth(_read(path / "truth.toml", "truth.toml"), names, fixes),
        fixes=fixes,
        state=state,
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
        _only_keys(table, {"name", "role", "closed_world"}, f"{where}: hosts")
        closed_world = table.get("closed_world", False)
        if not isinstance(closed_world, bool):
            raise ScenarioError(f"{where}: closed_world must be true or false")
        host = Host(_text(table, "name", where), _text(table, "role", where), closed_world)
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


def _fixes(value: object, hosts: set[str], where: str) -> tuple[Fix, ...]:
    if value is None:
        return ()
    fixes: list[Fix] = []
    for table in _tables(value, "fixes", where):
        spot = f"{where}: fixes"
        _only_keys(table, {"id", "host", "description", "class", "preconditions", "options"}, spot)
        fix_id = _text(table, "id", spot)
        if not _FIX_ID.fullmatch(fix_id):
            raise ScenarioError(f"{spot}: fix id {fix_id!r} must match {_FIX_ID.pattern}")
        if any(fix_id == other.id for other in fixes):
            raise ScenarioError(f"{spot}: duplicate fix: {fix_id}")
        host = _text(table, "host", spot)
        if host not in hosts:
            raise ScenarioError(f"{spot}: {fix_id}: unknown host: {host}")
        fix_class = _text(table, "class", spot)
        if fix_class not in FIX_CLASSES:
            raise ScenarioError(
                f"{spot}: {fix_id}: class must be one of {', '.join(sorted(FIX_CLASSES))}"
            )
        options_in = table.get("options", {})
        if not isinstance(options_in, Mapping):
            raise ScenarioError(f"{spot}: {fix_id}: options must be a table")
        options = {
            name: _texts(options_in, name, f"{spot}: {fix_id}: options", allow_empty=False)
            for name in options_in
        }
        fixes.append(
            Fix(
                id=fix_id,
                host=host,
                description=_text(table, "description", spot),
                fix_class=fix_class,
                preconditions=_texts(table, "preconditions", spot, allow_empty=True),
                options=options,
            )
        )
    return tuple(fixes)


def _state(value: object, fixes: tuple[Fix, ...], where: str) -> FixState | None:
    if value is None:
        if fixes:
            raise ScenarioError(f"{where}: fixes need a [state] table")
        return None
    spot = f"{where}: state"
    if not isinstance(value, Mapping):
        raise ScenarioError(f"{spot}: expected a table")
    _only_keys(value, {"reachability", "approval", "preconditions"}, spot)
    reachability = _text(value, "reachability", spot)
    if reachability not in REACHABILITY:
        raise ScenarioError(
            f"{spot}: reachability must be one of {', '.join(sorted(REACHABILITY))}"
        )
    approval = _text(value, "approval", spot)
    if approval not in APPROVAL:
        raise ScenarioError(f"{spot}: approval must be one of {', '.join(sorted(APPROVAL))}")
    values = value.get("preconditions", {})
    if not isinstance(values, Mapping) or not all(isinstance(v, bool) for v in values.values()):
        raise ScenarioError(f"{spot}: preconditions must map names to true or false")
    for fix in fixes:
        for name in fix.preconditions:
            if name not in values:
                raise ScenarioError(f"{spot}: no value for precondition {name} (fix {fix.id})")
    return FixState(reachability, approval, dict(values))


def _components(data: Mapping[str, object], where: str) -> tuple[str, ...]:
    ids = _texts(data, "component", where, allow_empty=False)
    unknown = [item for item in ids if item not in COMPONENTS]
    if unknown:
        raise ScenarioError(
            f"{where}: component: {', '.join(unknown)} not in {', '.join(sorted(COMPONENTS))}"
        )
    return ids


def _truth(data: Mapping[str, object], hosts: set[str], fixes: tuple[Fix, ...] = ()) -> Truth:
    where = "truth.toml"
    _only_keys(
        data,
        {
            "category", "other_categories", "host", "component", "component_phrases",
            "contributing",
            "forbidden_actions", "key_facts", "expected_fix", "expected_outcome",
            "forbidden_fixes",
        },
        where,
    )  # fmt: skip
    category = _text(data, "category", where)
    if category not in CATEGORIES:
        raise ScenarioError(
            f"{where}: category {category!r} is not one of {', '.join(sorted(CATEGORIES))}"
        )
    others = _texts(data, "other_categories", where, allow_empty=True)
    bad = [c for c in others if c not in CATEGORIES or c == category]
    if bad:
        raise ScenarioError(
            f"{where}: other_categories: {', '.join(bad)} must be other entries of "
            f"{', '.join(sorted(CATEGORIES))}"
        )
    fault_host = _text(data, "host", where)
    if fault_host != EXTERNAL and fault_host not in hosts:
        raise ScenarioError(f"{where}: host: unknown host: {fault_host}")
    component = _components(data, where)
    contributing: tuple[str, tuple[str, ...]] | None = None
    table = data.get("contributing")
    if table is not None:
        spot = f"{where}: contributing"
        if not isinstance(table, Mapping):
            raise ScenarioError(f"{spot} must be a table")
        _only_keys(table, {"host", "component"}, spot)
        other_host = _text(table, "host", spot)
        if other_host != EXTERNAL and other_host not in hosts:
            raise ScenarioError(f"{spot}: unknown host: {other_host}")
        other = _components(table, spot)
        if other_host == fault_host and set(other) & set(component):
            raise ScenarioError(f"{spot} is the same as the root cause")
        contributing = (other_host, other)
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
    ids = {fix.id for fix in fixes}
    expected_fix = data.get("expected_fix")
    expected_outcome = data.get("expected_outcome")
    forbidden_fixes = _texts(data, "forbidden_fixes", where, allow_empty=True)
    if expected_fix is None and fixes:
        raise ScenarioError(f"{where}: the scenario offers fixes, so expected_fix is required")
    if expected_fix is not None:
        if not fixes:
            raise ScenarioError(f"{where}: expected_fix needs fixes in scenario.toml")
        if expected_fix != NO_FIX and expected_fix not in ids:
            raise ScenarioError(f"{where}: expected_fix {expected_fix!r} is not an offered fix")
        if expected_fix == NO_FIX and expected_outcome is not None:
            raise ScenarioError(f"{where}: expected_outcome makes no sense with expected_fix none")
        if expected_fix != NO_FIX and expected_outcome not in OUTCOMES:
            raise ScenarioError(
                f"{where}: expected_outcome must be one of {', '.join(sorted(OUTCOMES))}"
            )
    unknown_fixes = [f for f in forbidden_fixes if f not in ids]
    if unknown_fixes:
        raise ScenarioError(f"{where}: forbidden_fixes: not offered: {', '.join(unknown_fixes)}")
    return Truth(
        category=category,
        other_categories=others,
        host=fault_host,
        component=component,
        component_phrases=_texts(data, "component_phrases", where, allow_empty=True),
        contributing=contributing,
        key_facts=tuple(facts),
        forbidden_actions=forbidden,
        expected_fix=expected_fix if isinstance(expected_fix, str) else None,
        expected_outcome=expected_outcome if isinstance(expected_outcome, str) else None,
        forbidden_fixes=forbidden_fixes,
    )
