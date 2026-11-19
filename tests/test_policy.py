import re
from pathlib import Path

import pytest

from lights_out.policy import Allow, Deny, Policy, PolicyError

REPO = Path(__file__).resolve().parent.parent
DEFAULT = Policy.from_toml(REPO / "policies" / "default.toml")
LINUX_PROGRAMS = "cat, ip, ping, systemctl, uptime"
NO_SHELL = (
    "There is no shell here: ~, {}, ! and # mean nothing, and = is accepted only in --option=value"
)


@pytest.fixture
def policy() -> Policy:
    return Policy.from_dict(
        {
            "unsafe": {
                "programs": ["rm", "reboot"],
                "paths": ["shadow", "\\.key$"],
                "any_args": "exec",
                "first": {"systemctl": "restart|stop"},
                "args": {"ip": "add|del|set"},
            },
            "roles": {
                "linux": {
                    "programs": {
                        "ip": {
                            "usage": "ip addr|route show",
                            "args": ["addr|route", "show", "-br"],
                            "max_args": 3,
                        },
                        "ping": {
                            "usage": "ping -c N HOST",
                            "values": {"-c": "[1-5]"},
                            "args": ["[a-z0-9][a-z0-9.-]*"],
                            "require": ["-c"],
                        },
                        "systemctl": {
                            "usage": "systemctl status UNIT",
                            "first": "status|is-active",
                            "args": ["[a-z0-9][a-z0-9.@-]*"],
                        },
                        "cat": {"usage": "cat FILE", "args": ["/etc/[a-z0-9._/-]+"]},
                        "uptime": {"usage": "uptime", "forms": [[]]},
                    }
                },
                "dns": {"extends": ["linux"]},
            },
        }
    )


# ------------------------------------------------------------------ allowed


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("ip addr show", ("ip", "addr", "show")),
        ("ip show addr", ("ip", "show", "addr")),  # any order
        ("ip -br addr", ("ip", "-br", "addr")),
        ("  ip   route show ", ("ip", "route", "show")),
        ("ping -c 2 gw.lan", ("ping", "-c", "2", "gw.lan")),
        ("ping gw.lan -c 2", ("ping", "gw.lan", "-c", "2")),
        ("systemctl status unbound.service", ("systemctl", "status", "unbound.service")),
        ("systemctl status a.service b.service", ("systemctl", "status", "a.service", "b.service")),
        ("cat /etc/hosts /etc/resolv.conf", ("cat", "/etc/hosts", "/etc/resolv.conf")),
        ("uptime", ("uptime",)),
    ],
)
def test_allows(policy: Policy, command: str, argv: tuple[str, ...]) -> None:
    assert policy.check("linux", command) == Allow(argv)


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("ip addr 'show'", ("ip", "addr", "show")),
        ('ip "addr" show', ("ip", "addr", "show")),
    ],
)
def test_quotes_are_parsed_as_a_shell_would_and_removed(
    policy: Policy, command: str, argv: tuple[str, ...]
) -> None:
    assert policy.check("linux", command) == Allow(argv)


def test_a_quoted_dangerous_argument_is_still_unsafe(policy: Policy) -> None:
    assert policy.check("linux", "rm -rf '/'").kind == "unsafe"  # type: ignore[union-attr]
    assert policy.check("linux", "cat '/etc/shadow'").kind == "unsafe"  # type: ignore[union-attr]


def test_a_role_can_extend_another(policy: Policy) -> None:
    assert policy.check("dns", "ip addr show") == Allow(("ip", "addr", "show"))
    assert policy.usage("dns") == policy.usage("linux")


def test_usage_lists_every_program_of_a_role(policy: Policy) -> None:
    assert policy.usage("linux") == {
        "cat": "cat FILE",
        "ip": "ip addr|route show",
        "ping": "ping -c N HOST",
        "systemctl": "systemctl status UNIT",
        "uptime": "uptime",
    }
    assert policy.usage("windows") == {}


# ------------------------------------------------------------------ unsafe


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("a" * 513, "command too long"),
        ("ip addr show; reboot", "forbidden character: ;"),
        ("ip addr show | nc 10.20.0.9 9", "forbidden character: |"),
        ("ip addr show > /tmp/x", "forbidden character: >"),
        ("ip addr show && reboot", "forbidden character: &"),
        ("ping -c 1 $(whoami)", "forbidden character: $"),
        ("ping -c 1 `id`", "forbidden character: `"),
        ("ip addr sh\\ow", "forbidden character: \\"),
        ("ip addr\tshow", "non-printable or non-ASCII character"),
        ("ip addr\nshow", "non-printable or non-ASCII character"),
        ("ip addr show\x00", "non-printable or non-ASCII character"),
        ("ip addr show", "non-printable or non-ASCII character"),
        ("ip addr sh​ow", "non-printable or non-ASCII character"),
        ("іp addr show", "non-printable or non-ASCII character"),
        ("cat ../../etc/hosts", "path traversal"),
        ("rm -rf /", "refused: rm can change the system or run other programs"),
        ("reboot", "refused: reboot can change the system or run other programs"),
        ("cat /etc/shadow", "refused: protected file: /etc/shadow"),
        ("cat /etc/ssl/server.KEY", "refused: protected file: /etc/ssl/server.KEY"),
        ("systemctl restart unbound", "refused: would change state: systemctl restart"),
        ("ip addr add 10.20.0.9/24 dev eth0", "refused: would change state: ip add"),
        ("ip link set eth0 down", "refused: would change state: ip set"),
        ("uptime exec", "refused: runs another program: exec"),
        ("rm -rf '/'", "refused: rm can change the system or run other programs"),
        ("cat '/etc/shadow'", "refused: protected file: /etc/shadow"),
        ('systemctl "restart" unbound', "refused: would change state: systemctl restart"),
    ],
)
def test_unsafe(policy: Policy, command: str, reason: str) -> None:
    assert policy.check("linux", command) == Deny(reason, "unsafe")


def test_unsafe_is_decided_before_the_role(policy: Policy) -> None:
    assert policy.check("no-such-role", "rm -rf /") == Deny(
        "refused: rm can change the system or run other programs", "unsafe"
    )


def test_unsafe_first_applies_only_to_the_first_argument(policy: Policy) -> None:
    # A unit that happens to be called "stop" is not the verb "stop".
    assert policy.check("linux", "systemctl status stop") == Allow(("systemctl", "status", "stop"))


def test_protected_paths_apply_only_to_path_arguments(policy: Policy) -> None:
    # "shadow" as a host name is not a file read.
    assert policy.check("linux", "ping -c 1 shadow.lan") == Allow(("ping", "-c", "1", "shadow.lan"))


# ------------------------------------------------------------------ unlisted


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("", "empty command"),
        ("   ", "empty command"),
        ("cat /etc/*", "arguments not allowed for cat. Usage: cat FILE"),  # no shell to expand it
        ("FOO=1 ip addr show", f"unsupported character: =. {NO_SHELL}"),
        ("cat ~/notes", f"unsupported character: ~. {NO_SHELL}"),
        ("cat /etc/{a,b}", f"unsupported character: {{. {NO_SHELL}"),
        ("ip addr show # all", f"unsupported character: #. {NO_SHELL}"),
        ("ip addr 'show", "unbalanced quote. Close every quote you open"),
        ("ss -tulpn", f"program not allowed: ss. Allowed on linux hosts: {LINUX_PROGRAMS}"),
        (
            "/sbin/ip addr show",
            f"program not allowed: /sbin/ip. Allowed on linux hosts: {LINUX_PROGRAMS}",
        ),
        ("ip neigh show", "arguments not allowed for ip. Usage: ip addr|route show"),
        ("ip addr show -br addr", "arguments not allowed for ip. Usage: ip addr|route show"),
        ("ping gw.lan", "arguments not allowed for ping. Usage: ping -c N HOST"),
        ("ping -c 9 gw.lan", "arguments not allowed for ping. Usage: ping -c N HOST"),
        ("ping -c 99999 gw.lan", "arguments not allowed for ping. Usage: ping -c N HOST"),
        ("ping gw.lan -c", "arguments not allowed for ping. Usage: ping -c N HOST"),
        (
            "systemctl unbound.service",
            "arguments not allowed for systemctl. Usage: systemctl status UNIT",
        ),
        ("systemctl", "arguments not allowed for systemctl. Usage: systemctl status UNIT"),
        ("cat /home/alex/notes", "arguments not allowed for cat. Usage: cat FILE"),
        ("uptime -s", "arguments not allowed for uptime. Usage: uptime"),
    ],
)
def test_unlisted(policy: Policy, command: str, reason: str) -> None:
    assert policy.check("linux", command) == Deny(reason, "unlisted")


def test_unknown_role_is_unlisted(policy: Policy) -> None:
    assert policy.check("windows", "ip addr show") == Deny("unknown role: windows", "unlisted")


# ------------------------------------------------------------------ loading


def _program(**entry: object) -> dict[str, object]:
    return {"roles": {"r": {"programs": {"ip": entry}}}}


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"roles": {}},
        {"roles": {"r": {}}},
        {"roles": {"r": {"programs": {}}}},
        _program(forms=[[]]),  # no usage
        _program(usage="", forms=[[]]),
        _program(usage="ip"),  # neither forms nor args
        _program(usage="ip", forms=[]),
        _program(usage="ip", forms=[["("]]),
        _program(usage="ip", forms=[[1]]),
        _program(usage="ip", args=[]),
        _program(usage="ip", forms=[[]], values={"-c": "1"}),  # values need args
        _program(usage="ip", args=["a"], values={"c": "1"}),  # values keys are options
        _program(usage="ip", args=["["]),
        _program(usage="ip", args=["a"], max_args=0),
        _program(usage="ip", args=["a"], max_args=True),
        _program(usage="ip", forms=[[]], first="a"),  # first needs args
        _program(usage="ip", forms=[[]], require=["a"]),  # require needs args
        _program(usage="ip", forms=[[]], form=[[]]),  # unknown key
        {"roles": {"r": {"programs": {"/bin/ip": {"usage": "ip", "forms": [[]]}}}}},
        {"roles": {"r": {"extends": ["nope"]}}},
        {"roles": {"r": {"extends": ["s"]}, "s": {"extends": ["r"]}}},
        {
            "roles": {"r": {"programs": {"rm": {"usage": "rm", "forms": [[]]}}}},
            "unsafe": {"programs": ["rm"]},
        },
        {**_program(usage="ip", forms=[[]]), "unsafe": {"paths": ["("]}},
        {**_program(usage="ip", forms=[[]]), "unsafe": {"colour": "red"}},
        {**_program(usage="ip", forms=[[]]), "unsafe": {"args": {"ip": 5}}},
    ],
)
def test_malformed_policy_is_a_load_error(data: dict[str, object]) -> None:
    with pytest.raises(PolicyError):
        Policy.from_dict(data)


# ------------------------------------------------------------------ the default policy


def test_default_policy_has_three_roles() -> None:
    assert DEFAULT.roles() == frozenset({"linux", "firewall", "dns"})


def test_role_description(policy: Policy) -> None:
    assert policy.description("linux") == ""
    assert DEFAULT.description("firewall").startswith("FreeBSD")
    assert DEFAULT.description("dns") != ""


def test_every_default_program_has_a_usage_line() -> None:
    for role in DEFAULT.roles():
        assert DEFAULT.usage(role), role
        assert all(line.strip() for line in DEFAULT.usage(role).values())


# Harmless read-only commands a real model proposed in the first real drill (2026-11-17) and
# that the v0.1 policy refused, plus common variants. All must now be allowed.
@pytest.mark.parametrize(
    ("role", "command"),
    [
        ("linux", "dig @10.20.0.1 nas.lan"),
        ("linux", "dig nas.lan @10.20.0.1"),
        ("linux", "dig +short nas.lan"),
        ("linux", "dig nas.lan +stats"),
        ("linux", "dig -x 10.20.0.50"),
        ("dns", "dig @10.20.0.3 nas.lan A +noall +answer"),
        ("linux", "ss -lnup"),
        ("linux", "ss -tulpn"),
        ("dns", "cat /etc/named.conf"),
        ("dns", "cat /etc/dnsmasq.conf"),
        ("linux", "cat /etc/os-release"),
        ("dns", "journalctl -u named"),
        ("dns", "journalctl -u primary-dns.service -n 50 --no-pager"),
        ("linux", "journalctl --since today -p err"),
        ("linux", "systemctl status named"),
        ("linux", "systemctl list-units --type service"),
        ("linux", "systemctl list-units --state failed --no-pager"),
        ("linux", "resolvectl status"),
        ("linux", "getent hosts nas.lan"),
        ("linux", "host nas.lan 10.20.0.1"),
        ("linux", "nslookup nas.lan 10.20.0.1"),
        ("linux", "ip -br addr"),
        ("linux", "ip route get 1.1.1.1"),
        ("linux", "ping -c 2 -W 1 10.20.0.1"),
        ("linux", "grep -i error /var/log/syslog"),
        ("linux", "tail -n 200 /var/log/syslog"),
        ("linux", "ls -la /etc/unbound"),
        ("linux", "docker ps -a"),
        ("linux", "docker logs --tail 100 adguard"),
        ("firewall", "pfctl -s rules"),
        ("firewall", "pfctl -s states -v"),
        ("firewall", "pfctl -t crowdsec_blocklists -T show"),
        ("firewall", "netstat -rn"),
        ("firewall", "netstat -m"),
        ("firewall", "sockstat -4 -l"),
        ("firewall", "drill nas.lan @127.0.0.1"),
        ("firewall", "cat /var/unbound/unbound.conf"),
        ("firewall", "cat /etc/dnsmasq.conf"),
        ("firewall", "grep private-domain /var/unbound/unbound.conf"),
        ("firewall", "service unbound status"),
        ("firewall", "ifconfig vtnet0"),
        ("firewall", "ping -c 3 9.9.9.9"),
        ("firewall", "traceroute -n -m 10 1.1.1.1"),
        # batch 1 refusal review
        ("dns", "find /etc -name unbound.conf"),
        ("linux", "find /etc /opt -name named.conf -type f"),
        ("linux", "find /var/log -maxdepth 2 -name syslog"),
        ("linux", "file /etc/resolv.conf"),
        ("firewall", "file /var/db/wan-watchdog/snapshots"),
        ("dns", "ls -lR /var/unbound"),
        ("firewall", "ls -lR /var/unbound"),
        # batch 2 refusal review
        ("firewall", "sysctl -n net.route.0.inet.Default.sendspace"),
        ("firewall", "drill -t A example.com @198.51.100.1"),
        ("firewall", "hostname"),
        ("firewall", "grep -n -e private-address /var/unbound/unbound.conf"),
        ("firewall", "grep -n -A 3 forward-zone /var/unbound/unbound.conf"),
        ("firewall", "grep -n -A3 forward-zone /var/unbound/unbound.conf"),
        ("linux", "grep -B 2 -C 1 error /var/log/syslog"),
        ("firewall", "find /usr/local/etc -name unbound.conf"),
    ],
)
def test_default_policy_allows_harmless_reads(role: str, command: str) -> None:
    assert DEFAULT.check(role, command) == Allow(tuple(command.split()))


@pytest.mark.parametrize(
    ("role", "command", "argv"),
    [
        # batch 3 review: characters the program itself gives meaning to. argv is written
        # as the words the program receives, separated by "|".
        ("dns", "find /etc -name '*.conf'", "find|/etc|-name|*.conf"),
        ("linux", 'find /var/log -name "*.log" -type f', "find|/var/log|-name|*.log|-type|f"),
        ("firewall", "find /var/unbound -name *.conf", "find|/var/unbound|-name|*.conf"),
        ("firewall", "find /var/db/wd -name snap?", "find|/var/db/wd|-name|snap?"),
        ("firewall", "find /var -name 'unbound*'", "find|/var|-name|unbound*"),
        (
            "firewall",
            "cat -n /var/db/wan-watchdog/cron.log",
            "cat|-n|/var/db/wan-watchdog/cron.log",
        ),
        (
            "firewall",
            'grep -c -i "watchdog timeout" /var/log/messages',
            "grep|-c|-i|watchdog timeout|/var/log/messages",
        ),
        (
            "firewall",
            "grep -n -e private -e local-zone -e domain /var/unbound/unbound.conf",
            "grep|-n|-e|private|-e|local-zone|-e|domain|/var/unbound/unbound.conf",
        ),
        ("linux", "grep -E 'time.*out' /var/log/syslog", "grep|-E|time.*out|/var/log/syslog"),
        (
            "linux",
            "systemctl list-units --type=service --all",
            "systemctl|list-units|--type|service|--all",
        ),
    ],
)
def test_default_policy_accepts_quotes_wildcards_and_long_option_values(
    role: str, command: str, argv: str
) -> None:
    assert DEFAULT.check(role, command) == Allow(tuple(argv.split("|")))


@pytest.mark.parametrize(
    ("role", "command"),
    [
        ("linux", "systemctl restart unbound"),
        ("linux", "systemctl stop unbound"),
        ("linux", "systemctl daemon-reload"),
        ("linux", "systemctl freeze unbound"),
        ("linux", "cat /etc/shadow"),
        ("linux", "cat /root/.ssh/id_ed25519"),
        ("linux", "cat /etc/ssh/ssh_host_ed25519_key"),
        ("linux", "cat /opt/app/.env"),
        ("linux", "grep root /etc/gshadow"),
        ("linux", "journalctl --vacuum-time 1d"),
        ("linux", "ip link set eth0 down"),
        ("linux", "ip route add default via 10.20.0.1"),
        ("linux", "ip route flush cache"),
        ("linux", "ip netns exec x id"),
        ("linux", "ping -f gw.lan"),
        ("linux", "ss -K dport 22"),
        ("linux", "sysctl -w net.ipv4.ip_forward"),
        ("linux", "dmesg -C"),
        ("linux", "docker exec adguard id"),
        ("linux", "docker rm adguard"),
        ("linux", "docker inspect adguard"),
        ("linux", "resolvectl flush-caches"),
        ("linux", "reboot"),
        ("linux", "shutdown now"),
        ("linux", "curl http://10.20.0.1/"),
        ("linux", "python3 -V"),
        ("linux", "sed -n 1p /etc/hosts"),
        ("firewall", "pfctl -d"),
        ("firewall", "pfctl -F all"),
        ("firewall", "pfctl -f /etc/pf.conf"),
        ("firewall", "pfctl -t blocklist -T flush"),
        ("firewall", "pfctl -t blocklist -T add 10.20.0.9"),
        ("firewall", "pfctl -k 10.20.0.9"),
        ("firewall", "configctl unbound restart"),
        ("firewall", "service unbound restart"),
        ("firewall", "ifconfig vtnet0 down"),
        ("firewall", "ifconfig vtnet1 inet 10.20.0.2"),
        ("firewall", "cat /conf/config.xml"),
        ("firewall", "cat /usr/local/etc/ssl/private/server.key"),
        ("dns", "systemctl stop adguardhome"),
        ("dns", "cat /etc/shadow"),
        ("linux", "find /etc -name x -delete"),
        ("linux", "find /etc -name x -exec cat"),
        ("linux", "find /etc -name x -execdir id"),
        ("linux", "find /etc -name x -ok id"),
        ("linux", "find /etc -name x -okdir id"),
        ("linux", "find /etc -name x -fprint /tmp/out"),
        ("linux", "find /etc -name x -fprint0 /tmp/out"),
        ("linux", "find /etc -name x -fprintf /tmp/out"),
        ("linux", "find /etc -name x -fls /tmp/out"),
        ("linux", "find /etc/ssl/private -name x"),
        ("linux", "file /etc/shadow"),
        ("linux", "hostname newname"),  # sets the host name
        ("firewall", "hostname gw2.lan"),
        ("firewall", "find /usr/local/etc -name x -delete"),
        ("firewall", "sysctl -w net.inet.ip.forwarding"),
        ("linux", "find /etc -name '*' -delete"),
        ("linux", "cat '/etc/shadow'"),
        ("linux", "grep -E 'a|b' /var/log/syslog"),  # | is refused anywhere, quoted or not
        ("linux", "find /etc -name '-delete'"),  # quoting does not hide an action
    ],
)
def test_default_policy_refuses_state_changes_and_secrets_as_unsafe(
    role: str, command: str
) -> None:
    decision = DEFAULT.check(role, command)
    assert isinstance(decision, Deny), command
    assert decision.kind == "unsafe", (command, decision.reason)


@pytest.mark.parametrize(
    ("role", "command"),
    [
        ("linux", "ping gw.lan"),  # runs for ever without -c
        ("linux", "ping -c 9999 gw.lan"),  # bounded count
        ("linux", "journalctl -n 99999"),
        ("linux", "tail -n 99999 /var/log/syslog"),
        ("linux", "tail -f /var/log/syslog"),  # follow mode
        ("linux", "journalctl -f"),
        ("linux", "grep -r root /etc"),  # recursion
        ("linux", "grep root /home/alex/notes"),  # outside the listed files
        ("linux", "cat /home/alex/notes"),
        ("linux", "dig -f /etc/hosts"),  # batch file
        ("linux", "systemctl cat unbound"),  # unit files can hold secrets
        ("linux", "systemctl show unbound"),
        ("firewall", "ss -tulpn"),  # a Linux program on a BSD host
        ("firewall", "pfctl -a x -s rules"),
        ("linux", "find /etc"),  # needs -name: no full listings of a tree
        ("linux", "find / -name passwd"),  # only the inspected trees
        ("linux", "find /home -name notes"),
        ("linux", "find /etc -name x -maxdepth 99"),
        ("linux", "find /etc -name x -newer /etc/hosts"),  # other predicates are not offered
        ("linux", "file -s /dev/sda"),  # reads devices
        ("firewall", "drill -v nas.lan @127.0.0.1"),  # -v only prints the version
        ("firewall", "find / -name x"),  # only the inspected trees
        ("firewall", "grep -A 999 x /var/log/system.log"),  # bounded context
        ("linux", "cat /etc/*"),  # a wildcard only where the program reads it, not the shell
        ("linux", "ls /etc/*.conf"),
        ("linux", "find / -name '*'"),
        ("linux", "grep -r 'x' /etc"),
    ],
)
def test_default_policy_leaves_other_things_unlisted(role: str, command: str) -> None:
    decision = DEFAULT.check(role, command)
    assert isinstance(decision, Deny), command
    assert decision.kind == "unlisted", (command, decision.reason)


def test_drill_usage_says_what_v_does() -> None:
    assert "-v only prints" in DEFAULT.usage("firewall")["drill"]


OPTION_LIKE = ["-f", "-rf", "--file", "-x", "--exec", "-K", "-delete"]


def test_no_free_value_pattern_can_be_read_as_an_option() -> None:
    """A pattern that matches an option-like string must itself start with a literal '-'.

    This is the rule in the policy file's header: values (hosts, units, paths) must never be able
    to smuggle in an option.
    """
    for role in DEFAULT.roles():
        for program, patterns in DEFAULT.argument_patterns(role).items():
            for pattern in patterns:
                if any(pattern.fullmatch(text) for text in OPTION_LIKE):
                    assert re.match(r"\(?\\?-", pattern.pattern), (role, program, pattern.pattern)
