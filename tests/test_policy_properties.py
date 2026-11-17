"""Properties the policy must hold for any input, checked against the shipped policy."""

import string
from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from lights_out.policy import (
    FORBIDDEN_CHARS,
    INJECTION_CHARS,
    MAX_COMMAND_LENGTH,
    Allow,
    Deny,
    Policy,
)

POLICY = Policy.from_toml(Path(__file__).resolve().parent.parent / "policies" / "default.toml")
ROLES = sorted(POLICY.roles())
SAFE_ALPHABET = "".join(c for c in string.printable if " " <= c <= "~" and c not in FORBIDDEN_CHARS)

roles = st.sampled_from(ROLES)


def _is_dangerous(char: str) -> bool:
    return char in FORBIDDEN_CHARS or not " " <= char <= "~"


@given(role=roles, command=st.text(max_size=200))
def test_anything_with_a_forbidden_or_non_ascii_character_is_denied(
    role: str, command: str
) -> None:
    assume(any(_is_dangerous(char) for char in command))
    assert isinstance(POLICY.check(role, command), Deny)


@given(role=roles, command=st.text(max_size=200))
def test_injection_or_non_ascii_is_always_unsafe(role: str, command: str) -> None:
    assume(any(char in INJECTION_CHARS or not " " <= char <= "~" for char in command))
    assume(len(command.strip(" ")) > 0)
    decision = POLICY.check(role, command)
    assert isinstance(decision, Deny)
    assert decision.kind == "unsafe"


@given(
    role=roles,
    prefix=st.text(alphabet=SAFE_ALPHABET, max_size=40),
    char=st.sampled_from(sorted(FORBIDDEN_CHARS) + ["\n", "\t", "\x00", " ", "​"]),
    suffix=st.text(alphabet=SAFE_ALPHABET, max_size=40),
)
def test_one_dangerous_character_anywhere_is_enough(
    role: str, prefix: str, char: str, suffix: str
) -> None:
    assert isinstance(POLICY.check(role, prefix + char + suffix), Deny)


@given(role=roles, command=st.text(alphabet=SAFE_ALPHABET, max_size=200))
def test_allow_returns_exactly_the_whitespace_split_argv(role: str, command: str) -> None:
    decision = POLICY.check(role, command)
    if isinstance(decision, Allow):
        assert decision.argv == tuple(command.split())
        assert all(decision.argv)


@given(role=st.one_of(roles, st.text(max_size=20)), command=st.text(max_size=600))
def test_check_never_raises_and_always_decides(role: str, command: str) -> None:
    assert isinstance(POLICY.check(role, command), Allow | Deny)


@given(
    role=roles, command=st.text(min_size=MAX_COMMAND_LENGTH + 1, max_size=MAX_COMMAND_LENGTH + 50)
)
def test_overlong_commands_are_denied(role: str, command: str) -> None:
    assert isinstance(POLICY.check(role, command), Deny)


# Random text almost never forms an allowed command, so the property above would pass even if
# Allow returned the wrong argv. Build commands from real allowed ones to exercise that path.
ALLOWED_EXAMPLES = [
    ("linux", ["ip", "addr", "show"]),
    ("linux", ["ping", "-c", "2", "gw.lan"]),
    ("linux", ["dig", "printer.lan", "AAAA", "@10.20.0.1"]),
    ("firewall", ["pfctl", "-s", "nat"]),
    ("firewall", ["ifconfig"]),
    ("dns", ["systemctl", "is-active", "resolver.service"]),
]


@pytest.mark.parametrize(("role", "argv"), ALLOWED_EXAMPLES)
@given(data=st.data())
def test_allowed_commands_survive_any_spacing(
    role: str, argv: list[str], data: st.DataObject
) -> None:
    gaps = [data.draw(st.integers(min_value=1, max_value=4)) for _ in range(len(argv) - 1)] + [
        data.draw(st.integers(min_value=0, max_value=3))
    ]
    lead = " " * data.draw(st.integers(min_value=0, max_value=3))
    command = lead + "".join(token + " " * gap for token, gap in zip(argv, gaps, strict=True))
    assert POLICY.check(role, command) == Allow(tuple(argv))


UNSAFE_PROGRAMS = ["rm", "reboot", "sh", "curl", "kill", "sudo", "python3", "configctl"]


@given(
    role=roles,
    program=st.sampled_from(UNSAFE_PROGRAMS),
    arguments=st.lists(st.text(alphabet=string.ascii_lowercase + "-/", max_size=12), max_size=4),
)
def test_an_unsafe_program_is_unsafe_whatever_its_arguments(
    role: str, program: str, arguments: list[str]
) -> None:
    decision = POLICY.check(role, " ".join([program, *arguments]))
    assert isinstance(decision, Deny)
    assert decision.kind == "unsafe"
