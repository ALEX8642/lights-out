"""Deny-by-default policy for commands proposed by the model.

A command arrives as one string. It is never passed to a shell. It is refused outright if it
contains anything a shell would interpret, then split on whitespace and allowed only if the
program and its arguments match an allowlist for the host's role. Nothing is rewritten or
escaped: a command is either run exactly as approved or not run.

The grammar is described in docs/adr/0002-policy-grammar.md.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MAX_COMMAND_LENGTH = 512
FORBIDDEN_CHARS = frozenset(";|&$`><()[]{}*?~!#\\\"'=")
_PROGRAM_NAME = re.compile(r"[a-z0-9_.-]+")

type _Form = tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class Allow:
    """The command may run, as exactly this argument vector."""

    argv: tuple[str, ...]


@dataclass(frozen=True)
class Deny:
    """The command must not run."""

    reason: str


type Decision = Allow | Deny


class PolicyError(ValueError):
    """The policy file is malformed."""


class Policy:
    """Allowed programs and argument forms, per host role."""

    def __init__(self, roles: Mapping[str, Mapping[str, tuple[_Form, ...]]]) -> None:
        self._roles = roles

    @classmethod
    def from_toml(cls, path: Path) -> Policy:
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            raise PolicyError(f"{path}: {error}") from error
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Policy:
        roles_in = _table(data.get("roles"), "roles")
        roles: dict[str, dict[str, tuple[_Form, ...]]] = {}
        for role, role_value in roles_in.items():
            role_table = _table(role_value, f"roles.{role}")
            _only_keys(role_table, {"programs"}, f"roles.{role}")
            programs_in = _table(role_table.get("programs"), f"roles.{role}.programs")
            programs: dict[str, tuple[_Form, ...]] = {}
            for program, program_value in programs_in.items():
                where = f"roles.{role}.programs.{program}"
                if not _PROGRAM_NAME.fullmatch(program):
                    raise PolicyError(f"{where}: program name must match {_PROGRAM_NAME.pattern}")
                program_table = _table(program_value, where)
                _only_keys(program_table, {"forms"}, where)
                programs[program] = _forms(program_table.get("forms"), where)
            roles[role] = programs
        return cls(roles)

    def roles(self) -> frozenset[str]:
        return frozenset(self._roles)

    def check(self, role: str, command: str) -> Decision:
        """Decide whether ``command`` may run on a host with this ``role``."""
        if not command.strip(" "):
            return Deny("empty command")
        if len(command) > MAX_COMMAND_LENGTH:
            return Deny("command too long")
        for char in command:
            if not " " <= char <= "~":
                return Deny("non-printable or non-ASCII character")
        for char in command:
            if char in FORBIDDEN_CHARS:
                return Deny(f"forbidden character: {char}")
        if ".." in command:
            return Deny("path traversal")
        programs = self._roles.get(role)
        if programs is None:
            return Deny(f"unknown role: {role}")
        argv = tuple(command.split())  # only spaces can be left to split on
        program, arguments = argv[0], argv[1:]
        forms = programs.get(program)
        if forms is None:
            return Deny(f"program not allowed: {program}")
        for form in forms:
            if len(form) == len(arguments) and all(
                pattern.fullmatch(argument)
                for pattern, argument in zip(form, arguments, strict=True)
            ):
                return Allow(argv)
        return Deny(f"arguments not allowed for {program}")


def _table(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise PolicyError(f"{where}: expected a non-empty table")
    return value


def _only_keys(table: Mapping[str, object], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise PolicyError(f"{where}: unknown key(s): {', '.join(unknown)}")


def _forms(value: object, where: str) -> tuple[_Form, ...]:
    if not isinstance(value, list) or not value:
        raise PolicyError(f"{where}: forms must be a non-empty list of lists")
    forms: list[_Form] = []
    for form in value:
        if not isinstance(form, list):
            raise PolicyError(f"{where}: each form must be a list of patterns")
        patterns: list[re.Pattern[str]] = []
        for pattern in form:
            if not isinstance(pattern, str):
                raise PolicyError(f"{where}: each pattern must be a string")
            try:
                patterns.append(re.compile(pattern))
            except re.error as error:
                raise PolicyError(f"{where}: invalid pattern {pattern!r}: {error}") from error
        forms.append(tuple(patterns))
    return tuple(forms)
