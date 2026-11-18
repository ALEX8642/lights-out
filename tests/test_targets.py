import dataclasses
from pathlib import Path

import pytest

from lights_out.scenario import Host, Recording, Scenario, load_scenario
from lights_out.targets import (
    MAX_OUTPUT_CHARS,
    NO_RECORDING,
    TRUNCATION_MARKER,
    CommandResult,
    ReplayTarget,
    Target,
    same_question,
)

MINI = Path(__file__).parent / "fixtures" / "scenarios" / "mini"


def with_recording(stdout: str, exit_code: int = 0) -> Scenario:
    scenario = load_scenario(MINI)
    recordings = dict(scenario.recordings)
    recordings[("mon.lan", ("df", "-h"))] = Recording(exit_code, stdout)
    return dataclasses.replace(scenario, recordings=recordings)


def test_recorded_command_returns_its_recording() -> None:
    target = ReplayTarget(load_scenario(MINI))
    result = target.run("mon.lan", ("uptime",))
    assert result == CommandResult(
        0, " 10:01:02 up 3 days,  1 user,  load average: 0.00, 0.01, 0.05\n", True, "exact"
    )


def test_exit_code_is_replayed() -> None:
    target = ReplayTarget(with_recording("df: cannot read table of mounted file systems\n", 1))
    assert target.run("mon.lan", ("df", "-h")).exit_code == 1


def test_unrecorded_command_says_so() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("mon.lan", ("ss", "-tulpn")) == CommandResult(127, NO_RECORDING, False, None)


def test_lookup_is_per_host() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("gw.lan", ("uptime",)).recorded is False


def test_lookup_needs_the_exact_argument_list() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("mon.lan", ("dig", "printer.lan")).recorded is False
    assert target.run("mon.lan", ("dig", "printer.lan", "@10.20.0.1")).recorded is True


def test_unknown_host_is_unrecorded_not_an_error() -> None:
    target = ReplayTarget(load_scenario(MINI))
    assert target.run("ghost.lan", ("uptime",)) == CommandResult(127, NO_RECORDING, False, None)


def test_long_output_is_truncated_with_a_marker() -> None:
    target = ReplayTarget(with_recording("x" * 9000))
    result = target.run("mon.lan", ("df", "-h"))
    assert result.stdout == "x" * MAX_OUTPUT_CHARS + TRUNCATION_MARKER
    assert result.recorded is True


def test_output_at_the_limit_is_unchanged() -> None:
    target = ReplayTarget(with_recording("x" * MAX_OUTPUT_CHARS))
    assert target.run("mon.lan", ("df", "-h")).stdout == "x" * MAX_OUTPUT_CHARS


def test_limit_is_8000_characters() -> None:
    assert MAX_OUTPUT_CHARS == 8000


def test_replay_target_satisfies_the_target_protocol() -> None:
    target: Target = ReplayTarget(load_scenario(MINI))
    assert target.run("mon.lan", ("uptime",)).recorded is True


# ------------------------------------------------------------------ equivalent commands


def scenario_with(*recordings: tuple[tuple[str, ...], str]) -> Scenario:
    scenario = load_scenario(MINI)
    table = {("mon.lan", argv): Recording(0, out) for argv, out in recordings}
    return dataclasses.replace(scenario, recordings=table)


DIG = (("dig", "nas.lan", "@10.20.0.3"), "ANSWER: 1 nas.lan 10.20.0.50")


@pytest.mark.parametrize(
    "argv",
    [
        ("dig", "@10.20.0.3", "nas.lan"),  # order
        ("dig", "@10.20.0.3", "nas.lan", "+short"),  # presentation flag
        ("dig", "@10.20.0.3", "nas.lan", "+short", "+noall", "+answer"),
        ("dig", "nas.lan", "A", "@10.20.0.3", "+stats"),  # default record type
        ("dig", "-t", "A", "nas.lan", "@10.20.0.3"),
    ],
)
def test_equivalent_dig_commands_get_the_recording(argv: tuple[str, ...]) -> None:
    result = ReplayTarget(scenario_with(DIG)).run("mon.lan", argv)
    assert result.recorded is True
    assert result.matched == "equivalent"
    assert "10.20.0.50" in result.stdout


def test_an_exact_match_says_so() -> None:
    result = ReplayTarget(scenario_with(DIG)).run("mon.lan", DIG[0])
    assert result.matched == "exact"


@pytest.mark.parametrize(
    "argv",
    [
        ("dig", "nas.lan", "@10.20.0.1"),  # a different server is a different question
        ("dig", "nas.lan"),  # no server: the client's resolver, not this one
        ("dig", "nas.lan", "AAAA", "@10.20.0.3"),  # a different record type
        ("dig", "-x", "10.20.0.50", "@10.20.0.3"),
    ],
)
def test_different_questions_do_not_match(argv: tuple[str, ...]) -> None:
    result = ReplayTarget(scenario_with(DIG)).run("mon.lan", argv)
    assert result.recorded is False
    assert result.matched is None


def test_journalctl_presentation_options_are_ignored() -> None:
    rec = (("journalctl", "--no-pager", "-n", "50", "-u", "monitor.service"), "log lines")
    target = ReplayTarget(scenario_with(rec))
    for argv in [
        ("journalctl", "-u", "monitor.service"),
        ("journalctl", "-u", "monitor.service", "-n", "200", "-o", "short-iso"),
        ("journalctl", "-u", "monitor", "--no-pager"),  # unit without .service
    ]:
        assert target.run("mon.lan", argv).recorded is True, argv
    assert target.run("mon.lan", ("journalctl", "-u", "cron.service")).recorded is False


def test_systemctl_unit_suffix_and_pager_options_are_ignored() -> None:
    rec = (("systemctl", "status", "primary-dns.service"), "active (running)")
    target = ReplayTarget(scenario_with(rec))
    assert target.run("mon.lan", ("systemctl", "status", "primary-dns")).recorded is True
    assert target.run(
        "mon.lan", ("systemctl", "status", "primary-dns", "--no-pager", "-l")
    ).recorded
    assert target.run("mon.lan", ("systemctl", "is-active", "primary-dns")).recorded is False


def test_combined_short_flags_match_in_any_letter_order() -> None:
    rec = (("ss", "-tulpn"), "sockets")
    target = ReplayTarget(scenario_with(rec))
    assert target.run("mon.lan", ("ss", "-nlptu")).recorded is True
    assert target.run("mon.lan", ("ss", "-t", "-u", "-l", "-p", "-n")).recorded is True


def test_socket_listings_answer_a_narrower_question() -> None:
    rec = (("ss", "-tulpn"), "sockets")
    target = ReplayTarget(scenario_with(rec))
    assert target.run("mon.lan", ("ss", "-lnup")).recorded is True  # udp only: a subset
    assert target.run("mon.lan", ("ss", "-tulpna")).recorded is False  # asks for more


def test_ls_flags_do_not_matter() -> None:
    rec = (("ls", "/etc/unbound"), "unbound.conf")
    target = ReplayTarget(scenario_with(rec))
    assert target.run("mon.lan", ("ls", "-la", "/etc/unbound")).recorded is True
    assert target.run("mon.lan", ("ls", "-l", "/etc/unbound/")).recorded is True


def test_cat_of_several_files_joins_their_recordings() -> None:
    target = ReplayTarget(
        scenario_with((("cat", "/etc/hosts"), "HOSTS\n"), (("cat", "/etc/resolv.conf"), "RESOLV\n"))
    )
    result = target.run("mon.lan", ("cat", "/etc/hosts", "/etc/resolv.conf"))
    assert result == CommandResult(0, "HOSTS\nRESOLV\n", True, "equivalent")
    partial = target.run("mon.lan", ("cat", "/etc/hosts", "/etc/nope"))
    assert partial.recorded is True
    assert partial.stdout.startswith("HOSTS\n")
    assert "/etc/nope" in partial.stdout and "no output recorded" in partial.stdout


def test_equivalence_never_crosses_hosts() -> None:
    target = ReplayTarget(scenario_with(DIG))
    assert target.run("gw.lan", ("dig", "@10.20.0.3", "nas.lan")).recorded is False


def test_the_first_equivalent_recording_wins_deterministically() -> None:
    target = ReplayTarget(
        scenario_with(
            (("dig", "nas.lan", "@10.20.0.3"), "FIRST"),
            (("dig", "nas.lan", "@10.20.0.3", "+stats"), "SECOND"),
        )
    )
    assert target.run("mon.lan", ("dig", "@10.20.0.3", "nas.lan", "+short")).stdout == "FIRST"


# ------------------------------------------------------------------ closed world


CONF = (
    "server:\n  interface: 10.20.0.1\n  private-address: 10.0.0.0/8\nforward-zone:\n  name: lan\n"
)


def closed(*recordings: tuple[tuple[str, ...], str], closed_world: bool = True) -> ReplayTarget:
    scenario = load_scenario(MINI)
    host = Host("mon.lan", "linux", closed_world=closed_world)
    table = {("mon.lan", argv): Recording(0, out) for argv, out in recordings}
    return ReplayTarget(dataclasses.replace(scenario, hosts=(host,), recordings=table))


FILES = (
    (("cat", "/etc/unbound/unbound.conf"), CONF),
    (("cat", "/etc/hosts"), "127.0.0.1 localhost\n10.20.0.9 mon.lan\n"),
)
UNITS = (
    (
        ("systemctl", "list-units", "--type", "service"),
        "  cron.service  loaded active\n  primary-dns.service loaded active\n",
    ),
)


def test_an_open_world_host_never_invents_answers() -> None:
    target = closed(*FILES, closed_world=False)
    assert target.run("mon.lan", ("cat", "/etc/named.conf")).recorded is False


def test_a_missing_file_is_reported_as_missing() -> None:
    result = closed(*FILES).run("mon.lan", ("cat", "/etc/named.conf"))
    assert result == CommandResult(
        1, "cat: /etc/named.conf: No such file or directory\n", True, "derived"
    )


def test_cat_of_a_known_directory() -> None:
    assert closed(*FILES).run("mon.lan", ("cat", "/etc/unbound")).stdout == (
        "cat: /etc/unbound: Is a directory\n"
    )


def test_ls_lists_the_recorded_files_below_a_directory() -> None:
    target = closed(*FILES)
    assert target.run("mon.lan", ("ls", "-la", "/etc")).stdout == "hosts\nunbound\n"
    assert target.run("mon.lan", ("ls", "/etc/unbound/")).stdout == "unbound.conf\n"
    missing = target.run("mon.lan", ("ls", "/var/named"))
    assert (missing.exit_code, missing.stdout) == (
        2,
        "ls: cannot access '/var/named': No such file or directory\n",
    )


@pytest.mark.parametrize(
    ("argv", "stdout", "exit_code"),
    [
        (("grep", "private", "/etc/unbound/unbound.conf"), "  private-address: 10.0.0.0/8\n", 0),
        (("grep", "-n", "lan", "/etc/unbound/unbound.conf"), "5:  name: lan\n", 0),
        (("grep", "-c", "address", "/etc/unbound/unbound.conf"), "1\n", 0),
        (("grep", "-i", "SERVER", "/etc/unbound/unbound.conf"), "server:\n", 0),
        (("grep", "private-domain", "/etc/unbound/unbound.conf"), "", 1),
        (("grep", "-v", ":", "/etc/hosts"), "127.0.0.1 localhost\n10.20.0.9 mon.lan\n", 0),
        (
            ("grep", "mon", "/etc/hosts", "/etc/unbound/unbound.conf"),
            "/etc/hosts:10.20.0.9 mon.lan\n",
            0,
        ),
        (("grep", "x", "/etc/nope"), "grep: /etc/nope: No such file or directory\n", 2),
    ],
)
def test_grep_is_computed_from_the_recorded_file(
    argv: tuple[str, ...], stdout: str, exit_code: int
) -> None:
    result = closed(*FILES).run("mon.lan", argv)
    assert (result.exit_code, result.stdout, result.matched) == (exit_code, stdout, "derived")


def test_head_tail_and_wc_are_computed_from_the_recorded_file() -> None:
    target = closed(*FILES)
    path = "/etc/unbound/unbound.conf"
    assert target.run("mon.lan", ("head", "-n", "2", path)).stdout == (
        "server:\n  interface: 10.20.0.1\n"
    )
    assert target.run("mon.lan", ("tail", "-n", "1", path)).stdout == "  name: lan\n"
    assert target.run("mon.lan", ("wc", "-l", path)).stdout == f"5 {path}\n"


def test_a_unit_missing_from_the_listing_does_not_exist() -> None:
    target = closed(*UNITS)
    status = target.run("mon.lan", ("systemctl", "status", "named"))
    assert (status.exit_code, status.stdout) == (4, "Unit named.service could not be found.\n")
    active = target.run("mon.lan", ("systemctl", "is-active", "named.service"))
    assert (active.exit_code, active.stdout) == (3, "inactive\n")
    logs = target.run("mon.lan", ("journalctl", "-u", "named", "-n", "50"))
    assert (logs.exit_code, logs.stdout) == (0, "-- No entries --\n")


def test_a_listed_unit_without_a_recording_is_still_unrecorded() -> None:
    assert closed(*UNITS).run("mon.lan", ("systemctl", "status", "cron")).recorded is False


def test_without_a_listing_nothing_is_said_about_units() -> None:
    assert closed(*FILES).run("mon.lan", ("systemctl", "status", "named")).recorded is False


def test_a_bsd_service_missing_from_the_listing_does_not_exist() -> None:
    target = closed((("service", "-e"), "/usr/local/etc/rc.d/unbound\n/etc/rc.d/sshd\n"))
    result = target.run("mon.lan", ("service", "dnsmasq", "status"))
    assert result.exit_code == 1
    assert "dnsmasq does not exist" in result.stdout
    assert target.run("mon.lan", ("service", "unbound", "status")).recorded is False


def test_same_question_is_public_and_symmetric() -> None:
    a, b = ("dig", "nas.lan", "@10.20.0.3"), ("dig", "@10.20.0.3", "nas.lan", "+short")
    assert same_question(a, b) and same_question(b, a)
    assert not same_question(a, ("dig", "nas.lan", "@10.20.0.1"))


def test_dmesg_and_arp_and_netstat_variants() -> None:
    target = ReplayTarget(
        scenario_with(
            (("dmesg",), "kernel log"),
            (("arp", "-an"), "arp table"),
            (("netstat", "-rn"), "routes"),
        )
    )
    for argv in [
        ("dmesg", "-a"),
        ("arp", "-a"),
        ("arp", "-n", "-a"),
        ("netstat", "-rn", "-f", "inet"),
    ]:
        assert target.run("mon.lan", argv).recorded is True, argv


@pytest.mark.parametrize(
    ("argv", "stdout", "exit_code"),
    [
        (("find", "/etc", "-name", "unbound.conf"), "/etc/unbound/unbound.conf\n", 0),
        (("find", "/etc", "-name", "unbound", "-type", "d"), "/etc/unbound\n", 0),
        (("find", "/etc", "-name", "unbound", "-type", "f"), "", 0),
        (("find", "/etc", "-maxdepth", "1", "-name", "unbound.conf"), "", 0),
        (("find", "/etc", "-name", "nope"), "", 0),
        (
            ("find", "/var/named", "-name", "x"),
            "find: '/var/named': No such file or directory\n",
            1,
        ),
    ],
)
def test_find_is_computed_from_the_recorded_files(
    argv: tuple[str, ...], stdout: str, exit_code: int
) -> None:
    result = closed(*FILES).run("mon.lan", argv)
    assert (result.exit_code, result.stdout, result.matched) == (exit_code, stdout, "derived")


def test_file_says_text_directory_or_missing() -> None:
    result = closed(*FILES).run("mon.lan", ("file", "/etc/hosts", "/etc/unbound", "/etc/nope"))
    assert (result.exit_code, result.stdout, result.matched) == (
        0,
        "/etc/hosts: ASCII text\n/etc/unbound: directory\n"
        "/etc/nope: cannot open `/etc/nope' (No such file or directory)\n",
        "derived",
    )


def test_ls_recursive_lists_every_directory_below() -> None:
    assert closed(*FILES).run("mon.lan", ("ls", "-lR", "/etc")).stdout == (
        "/etc:\nhosts\nunbound\n\n/etc/unbound:\nunbound.conf\n"
    )
