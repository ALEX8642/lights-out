import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from lights_out.scenario import (
    ACTIONS,
    CATEGORIES,
    EXTERNAL,
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
    assert truth.component == ("Secondary Resolver", "unbound")
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


@pytest.mark.parametrize("value", ['"Secondary Resolver"', "[]", '[""]', "[1]"])
def test_component_must_be_a_list_of_phrases(copy: Path, value: str) -> None:
    edit(
        copy / "truth.toml",
        lambda s: s.replace(
            'component = ["Secondary Resolver", "unbound"]', f"component = {value}"
        ),
    )
    with pytest.raises(ScenarioError, match=r"truth\.toml.*component"):
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
