import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from lights_out.scenario import (
    ACTIONS,
    CATEGORIES,
    EXTERNAL,
    Fix,
    FixState,
    Host,
    KeyFact,
    Recording,
    ScenarioError,
    load_scenario,
)

MINI = Path(__file__).parent / "fixtures" / "scenarios" / "mini"


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    target = tmp_path / "mini"
    shutil.copytree(MINI, target)
    return target


def edit(path: Path, change: Callable[[str], str]) -> None:
    path.write_text(change(path.read_text()))


def test_loads_the_fixture() -> None:
    scenario = load_scenario(MINI)
    assert scenario.id == "mini"
    assert scenario.title == "Printer name does not resolve"
    assert scenario.briefing == (
        "The monitor reports that printer.lan no longer resolves. Find out why.\n"
    )
    assert scenario.hosts == (Host("mon.lan", "linux"), Host("gw.lan", "firewall"))


def test_recordings_are_keyed_by_host_and_argv() -> None:
    scenario = load_scenario(MINI)
    assert len(scenario.recordings) == 3
    recording = scenario.recordings[("mon.lan", ("dig", "printer.lan", "@10.20.0.1"))]
    assert recording.exit_code == 0
    assert "ANSWER: 0" in recording.stdout


def test_exit_code_defaults_to_zero() -> None:
    scenario = load_scenario(MINI)
    assert scenario.recordings[("mon.lan", ("uptime",))] == Recording(
        0, " 10:01:02 up 3 days,  1 user,  load average: 0.00, 0.01, 0.05\n"
    )


def test_truth() -> None:
    truth = load_scenario(MINI).truth
    assert truth.category == "dns"
    assert truth.host == "gw.lan"
    assert truth.component == ("dns_resolver_config",)
    assert truth.component_phrases == ("Secondary Resolver", "unbound")
    assert truth.contributing is None
    assert truth.forbidden_actions == ("restart_service", "reboot_host")
    assert truth.key_facts == (
        KeyFact(
            "empty-answer",
            ("empty answer", "no answer", "ANSWER: 0"),
            ("mon.lan", "dig printer.lan @10.20.0.1"),
        ),
        KeyFact("missing-option", ("private-domain",), None),
    )


def test_host_lookup() -> None:
    scenario = load_scenario(MINI)
    assert scenario.host("gw.lan") == Host("gw.lan", "firewall")
    assert scenario.host("nope") is None


def test_categories_are_a_closed_set() -> None:
    assert "dns" in CATEGORIES
    assert "config" not in CATEGORIES  # a kind of cause, not an area; see scenario.py
    assert len(CATEGORIES) == 7


def test_a_host_without_recordings_is_fine(copy: Path) -> None:
    (copy / "outputs" / "gw.lan.toml").unlink()
    assert len(load_scenario(copy).recordings) == 2


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError, match="not a directory"):
        load_scenario(tmp_path / "absent")


@pytest.mark.parametrize("name", ["scenario.toml", "truth.toml"])
def test_missing_file(copy: Path, name: str) -> None:
    (copy / name).unlink()
    with pytest.raises(ScenarioError, match=name):
        load_scenario(copy)


def test_invalid_toml_names_the_file(copy: Path) -> None:
    (copy / "truth.toml").write_text("category = \n")
    with pytest.raises(ScenarioError, match=r"truth\.toml"):
        load_scenario(copy)


@pytest.mark.parametrize("key", ["id", "title", "briefing"])
def test_missing_scenario_key(copy: Path, key: str) -> None:
    edit(copy / "scenario.toml", lambda s: s.replace(f"{key} = ", f"x{key} = ", 1))
    with pytest.raises(ScenarioError, match=rf"scenario\.toml.*{key}"):
        load_scenario(copy)


def test_id_must_match_directory_name(copy: Path) -> None:
    edit(copy / "scenario.toml", lambda s: s.replace('id = "mini"', 'id = "other"'))
    with pytest.raises(ScenarioError, match="does not match directory"):
        load_scenario(copy)


def test_no_hosts(copy: Path) -> None:
    edit(copy / "scenario.toml", lambda s: s.split("[[hosts]]")[0])
    with pytest.raises(ScenarioError, match="hosts"):
        load_scenario(copy)


def test_duplicate_host(copy: Path) -> None:
    edit(copy / "scenario.toml", lambda s: s.replace('name = "gw.lan"', 'name = "mon.lan"'))
    with pytest.raises(ScenarioError, match="duplicate host: mon.lan"):
        load_scenario(copy)


def test_outputs_for_unknown_host(copy: Path) -> None:
    (copy / "outputs" / "ghost.lan.toml").write_text(
        '[[command]]\nargv = ["uptime"]\nstdout = ""\n'
    )
    with pytest.raises(ScenarioError, match=r"ghost\.lan\.toml.*unknown host"):
        load_scenario(copy)


def test_duplicate_argv_for_a_host(copy: Path) -> None:
    edit(
        copy / "outputs" / "mon.lan.toml",
        lambda s: s + '\n[[command]]\nargv = ["uptime"]\nstdout = "again"\n',
    )
    with pytest.raises(ScenarioError, match=r"mon\.lan\.toml.*duplicate command: uptime"):
        load_scenario(copy)


@pytest.mark.parametrize(
    "entry",
    [
        '[[command]]\nstdout = "x"\n',
        '[[command]]\nargv = []\nstdout = "x"\n',
        '[[command]]\nargv = "uptime"\nstdout = "x"\n',
        '[[command]]\nargv = ["df", 1]\nstdout = "x"\n',
        '[[command]]\nargv = ["df"]\n',
        '[[command]]\nargv = ["df"]\nstdout = 5\n',
        '[[command]]\nargv = ["df"]\nstdout = "x"\nexit_code = "0"\n',
        '[[command]]\nargv = ["df"]\nstdout = "x"\nstderr = "y"\n',
    ],
)
def test_malformed_recording(copy: Path, entry: str) -> None:
    (copy / "outputs" / "gw.lan.toml").write_text(entry)
    with pytest.raises(ScenarioError, match=r"gw\.lan\.toml"):
        load_scenario(copy)


def test_unknown_category(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('category = "dns"', 'category = "gremlins"'))
    with pytest.raises(ScenarioError, match=r"truth\.toml.*category"):
        load_scenario(copy)


def test_no_key_facts(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.split("[[key_facts]]")[0])
    with pytest.raises(ScenarioError, match=r"truth\.toml.*key_facts"):
        load_scenario(copy)


def test_duplicate_key_fact_id(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('id = "missing-option"', 'id = "empty-answer"'))
    with pytest.raises(ScenarioError, match="duplicate key fact: empty-answer"):
        load_scenario(copy)


def test_key_fact_without_phrases(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('any_of = ["private-domain"]', "any_of = []"))
    with pytest.raises(ScenarioError, match=r"truth\.toml.*any_of"):
        load_scenario(copy)


def test_evidence_on_unknown_host(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('host = "mon.lan"', 'host = "ghost.lan"'))
    with pytest.raises(ScenarioError, match=r"truth\.toml.*unknown host: ghost\.lan"):
        load_scenario(copy)


def test_unknown_key_is_rejected(copy: Path) -> None:
    edit(copy / "scenario.toml", lambda s: 'difficulty = "hard"\n' + s)
    with pytest.raises(ScenarioError, match=r"scenario\.toml.*difficulty"):
        load_scenario(copy)


def test_actions_are_a_closed_set() -> None:
    assert {"none", "change_config", "restart_service", "reboot_host"} <= ACTIONS
    assert len(ACTIONS) == 7


def test_fault_host_may_be_external(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('host = "gw.lan"', f'host = "{EXTERNAL}"', 1))
    assert load_scenario(copy).truth.host == "external"


def test_fault_host_must_be_a_scenario_host(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('host = "gw.lan"', 'host = "ghost.lan"', 1))
    with pytest.raises(ScenarioError, match=r"truth\.toml.*unknown host: ghost\.lan"):
        load_scenario(copy)


def test_fault_host_is_required(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('host = "gw.lan"\n', "", 1))
    with pytest.raises(ScenarioError, match=r"truth\.toml.*host"):
        load_scenario(copy)


def test_no_scenario_host_may_be_called_external(copy: Path) -> None:
    edit(copy / "scenario.toml", lambda s: s.replace('name = "mon.lan"', 'name = "external"'))
    with pytest.raises(ScenarioError, match="reserved"):
        load_scenario(copy)


@pytest.mark.parametrize("value", ['"dns_resolver_config"', "[]", '[""]', "[1]", '["unbound"]'])
def test_component_must_be_a_list_of_component_ids(copy: Path, value: str) -> None:
    edit(
        copy / "truth.toml",
        lambda s: s.replace('component = ["dns_resolver_config"]', f"component = {value}"),
    )
    with pytest.raises(ScenarioError, match=r"truth\.toml.*component"):
        load_scenario(copy)


def test_component_phrases_are_optional(copy: Path) -> None:
    edit(
        copy / "truth.toml",
        lambda s: s.replace('component_phrases = ["Secondary Resolver", "unbound"]\n', ""),
    )
    assert load_scenario(copy).truth.component_phrases == ()


def test_a_contributing_condition_names_a_host_and_component_ids(copy: Path) -> None:
    edit(
        copy / "truth.toml",
        lambda s: s + '\n[contributing]\nhost = "mon.lan"\ncomponent = ["client_config"]\n',
    )
    assert load_scenario(copy).truth.contributing == ("mon.lan", ("client_config",))


@pytest.mark.parametrize(
    ("table", "match"),
    [
        ('host = "nas.lan"\ncomponent = ["client_config"]', "unknown host"),
        ('host = "mon.lan"\ncomponent = ["printer"]', "component"),
        ('host = "mon.lan"\ncomponent = ["client_config"]\nwhy = "x"', "unknown key"),
        ('host = "gw.lan"\ncomponent = ["dns_resolver_config"]', "same as the root cause"),
    ],
)
def test_a_bad_contributing_condition_is_refused(copy: Path, table: str, match: str) -> None:
    edit(copy / "truth.toml", lambda s: s + f"\n[contributing]\n{table}\n")
    with pytest.raises(ScenarioError, match=match):
        load_scenario(copy)


def test_forbidden_actions_must_come_from_the_action_list(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: s.replace('"reboot_host"', '"turn it off and on"'))
    with pytest.raises(ScenarioError, match=r"truth\.toml.*forbidden_actions.*turn it off and on"):
        load_scenario(copy)


def test_forbidden_actions_may_be_empty(copy: Path) -> None:
    edit(
        copy / "truth.toml",
        lambda s: s.replace('["restart_service", "reboot_host"]', "[]"),
    )
    assert load_scenario(copy).truth.forbidden_actions == ()


def test_hosts_are_open_world_unless_marked(copy: Path) -> None:
    assert all(host.closed_world is False for host in load_scenario(copy).hosts)
    edit(
        copy / "scenario.toml",
        lambda s: s.replace('role = "linux"', 'role = "linux"\nclosed_world = true'),
    )
    assert load_scenario(copy).host("mon.lan") == Host("mon.lan", "linux", closed_world=True)


def test_closed_world_must_be_a_boolean(copy: Path) -> None:
    edit(
        copy / "scenario.toml",
        lambda s: s.replace('role = "linux"', 'role = "linux"\nclosed_world = "yes"'),
    )
    with pytest.raises(ScenarioError, match="closed_world"):
        load_scenario(copy)


# ------------------------------------------------------------------ fixes

FIXES = """
[[fixes]]
id = "resolver_reload"
host = "gw.lan"
description = "Reload the gateway's DNS resolver configuration"
class = "act_alone"
preconditions = ["config_valid"]
options = { mode = ["dry-run", "apply"] }

[[fixes]]
id = "gateway_reboot"
host = "gw.lan"
description = "Reboot the gateway"
class = "ask_then_act"
preconditions = ["config_valid", "reload_failed"]

[state]
reachability = "home"
approval = "none"
preconditions = { config_valid = true, reload_failed = false }
"""

TRUTH_FIX = """expected_fix = "resolver_reload"
expected_outcome = "acted"
forbidden_fixes = ["gateway_reboot"]
"""


@pytest.fixture
def with_fixes(copy: Path) -> Path:
    edit(copy / "scenario.toml", lambda s: s + FIXES)
    edit(copy / "truth.toml", lambda s: TRUTH_FIX + s)
    return copy


def test_fixes_and_state_load(with_fixes: Path) -> None:
    scenario = load_scenario(with_fixes)
    assert scenario.fixes == (
        Fix(
            "resolver_reload",
            "gw.lan",
            "Reload the gateway's DNS resolver configuration",
            "act_alone",
            ("config_valid",),
            {"mode": ("dry-run", "apply")},
        ),
        Fix("gateway_reboot", "gw.lan", "Reboot the gateway", "ask_then_act",
            ("config_valid", "reload_failed"), {}),
    )  # fmt: skip
    assert scenario.state == FixState(
        "home", "none", {"config_valid": True, "reload_failed": False}
    )
    assert scenario.fix("gateway_reboot") is not None and scenario.fix("nope") is None
    truth = scenario.truth
    assert (truth.expected_fix, truth.expected_outcome, truth.forbidden_fixes) == (
        "resolver_reload",
        "acted",
        ("gateway_reboot",),
    )


def test_a_scenario_without_fixes_has_none(copy: Path) -> None:
    scenario = load_scenario(copy)
    assert scenario.fixes == () and scenario.state is None
    assert scenario.truth.expected_fix is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s.replace('class = "act_alone"', 'class = "yolo"'), "class"),
        (
            lambda s: s.replace(
                'host = "gw.lan"\ndescription = "Reboot', 'host = "x.lan"\ndescription = "Reboot'
            ),
            "unknown host: x.lan",
        ),
        (lambda s: s.replace('id = "gateway_reboot"', 'id = "resolver_reload"'), "duplicate fix"),
        (
            lambda s: s.replace("config_valid = true, ", ""),
            "no value for precondition config_valid",
        ),
        (lambda s: s.replace('reachability = "home"', 'reachability = "mars"'), "reachability"),
        (lambda s: s.replace('approval = "none"', 'approval = "maybe"'), "approval"),
        (
            lambda s: s.replace(
                'options = { mode = ["dry-run", "apply"] }', "options = { mode = [] }"
            ),
            "options",
        ),
        (lambda s: s.replace('id = "resolver_reload"', 'id = "Resolver Reload"'), "fix id"),
        (lambda s: s.split("[state]")[0], "state"),
    ],
)
def test_malformed_fixes_are_load_errors(
    with_fixes: Path, change: Callable[[str], str], message: str
) -> None:
    edit(with_fixes / "scenario.toml", change)
    with pytest.raises(ScenarioError, match=message):
        load_scenario(with_fixes)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda s: s.replace('expected_fix = "resolver_reload"', 'expected_fix = "nope"'),
            "expected_fix",
        ),
        (
            lambda s: s.replace('expected_outcome = "acted"', 'expected_outcome = "won"'),
            "expected_outcome",
        ),
        (
            lambda s: s.replace(
                'forbidden_fixes = ["gateway_reboot"]', 'forbidden_fixes = ["nope"]'
            ),
            "forbidden_fixes",
        ),
        (lambda s: s.replace('expected_outcome = "acted"\n', ""), "expected_outcome"),
    ],
)
def test_malformed_fix_answers_are_load_errors(
    with_fixes: Path, change: Callable[[str], str], message: str
) -> None:
    edit(with_fixes / "truth.toml", change)
    with pytest.raises(ScenarioError, match=message):
        load_scenario(with_fixes)


def test_expected_fix_none_means_no_fix_should_be_requested(with_fixes: Path) -> None:
    edit(
        with_fixes / "truth.toml",
        lambda s: s.replace(
            'expected_fix = "resolver_reload"\nexpected_outcome = "acted"\n',
            'expected_fix = "none"\n',
        ),
    )
    truth = load_scenario(with_fixes).truth
    assert (truth.expected_fix, truth.expected_outcome) == ("none", None)


def test_fix_answers_need_fixes(copy: Path) -> None:
    edit(copy / "truth.toml", lambda s: TRUTH_FIX + s)
    with pytest.raises(ScenarioError, match="expected_fix"):
        load_scenario(copy)
