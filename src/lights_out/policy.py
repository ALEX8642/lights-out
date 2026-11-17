"""Policy for commands proposed by the model: refuse what is unsafe, allow what is listed.

A command arrives as one string. It is never passed to a shell. It is refused outright if it
contains anything a shell would interpret, then split on spaces and checked in two layers:

1. **Unsafe.** Programs that change the system or run other programs, arguments that would
   change state, and path arguments that name protected files. Checked first, for every role.
2. **Allowlist.** The program must be listed for the host's role and its arguments must match
   the program's patterns. Anything else is refused as unlisted, with the program's usage line.

Nothing is rewritten or escaped: a command is either run exactly as approved or not run.
The grammar is described in docs/adr/0007-policy-v2.md (which amends 0002).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MAX_COMMAND_LENGTH = 512
# Characters that chain, redirect or substitute commands in a shell: refused as unsafe.
INJECTION_CHARS = frozenset(";|&$`><()\\")
# Characters this policy does not support (quoting, globs, assignments): refused as unlisted.
UNSUPPORTED_CHARS = frozenset("[]{}*?~!#\"'=")
FORBIDDEN_CHARS = INJECTION_CHARS | UNSUPPORTED_CHARS
DEFAULT_MAX_ARGS = 8
_PROGRAM_NAME = re.compile(r"[a-z0-9_.-]+")

type DenyKind = Literal["unsafe", "unlisted"]


@dataclass(frozen=True)
class Allow:
    """The command may run, as exactly this argument vector."""

    argv: tuple[str, ...]


@dataclass(frozen=True)
class Deny:
    """The command must not run.

    ``kind`` is "unsafe" when the command would change state, run another program, read a
    protected file or uses shell syntax; "unlisted" when it is harmless but not offered.
    """

    reason: str
    kind: DenyKind


type Decision = Allow | Deny


class PolicyError(ValueError):
    """The policy file is malformed."""


@dataclass(frozen=True)
class _Program:
    usage: str
    forms: tuple[tuple[re.Pattern[str], ...], ...]
    args: tuple[re.Pattern[str], ...] | None
    values: Mapping[str, re.Pattern[str]]  # option -> pattern its following argument must match
    max_args: int
    first: re.Pattern[str] | None
    require: tuple[re.Pattern[str], ...]

    def accepts(self, arguments: Sequence[str]) -> bool:
        for form in self.forms:
            if len(form) == len(arguments) and all(
                pattern.fullmatch(argument)
                for pattern, argument in zip(form, arguments, strict=True)
            ):
                return True
        if self.args is None or len(arguments) > self.max_args:
            return False
        rest = list(arguments)
        if self.first is not None:
            if not rest or not self.first.fullmatch(rest[0]):
                return False
            rest = rest[1:]
        position = 0
        while position < len(rest):
            argument = rest[position]
            value = self.values.get(argument)
            if value is not None:
                if position + 1 >= len(rest) or not value.fullmatch(rest[position + 1]):
                    return False
                position += 2
                continue
            if not any(p.fullmatch(argument) for p in self.args):
                return False
            position += 1
        return all(any(p.fullmatch(a) for a in arguments) for p in self.require)


@dataclass(frozen=True)
class _Unsafe:
    programs: frozenset[str]
    paths: tuple[re.Pattern[str], ...]
    any_args: re.Pattern[str] | None
    first: Mapping[str, re.Pattern[str]]
    args: Mapping[str, re.Pattern[str]]

    def reason(self, program: str, arguments: Sequence[str]) -> str | None:
        if program in self.programs:
            return f"refused: {program} can change the system or run other programs"
        for argument in arguments:
            if "/" in argument and any(p.search(argument) for p in self.paths):
                return f"refused: protected file: {argument}"
        verb = self.first.get(program)
        if verb is not None and arguments and verb.fullmatch(arguments[0]):
            return f"refused: would change state: {program} {arguments[0]}"
        changing = self.args.get(program)
        for argument in arguments:
            if changing is not None and changing.fullmatch(argument):
                return f"refused: would change state: {program} {argument}"
            if self.any_args is not None and self.any_args.fullmatch(argument):
                return f"refused: runs another program: {argument}"
        return None


class Policy:
    """Unsafe rules for every role, and allowed programs per host role."""

    def __init__(
        self,
        unsafe: _Unsafe,
        roles: Mapping[str, Mapping[str, _Program]],
        descriptions: Mapping[str, str] | None = None,
    ) -> None:
        self._unsafe = unsafe
        self._roles = roles
        self._descriptions = dict(descriptions or {})

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
        _only_keys(data, {"roles", "unsafe"}, "policy")
        unsafe = _load_unsafe(data.get("unsafe", {}))
        roles_in = _table(data.get("roles"), "roles")
        own: dict[str, dict[str, _Program]] = {}
        extends: dict[str, list[str]] = {}
        descriptions: dict[str, str] = {}
        for role, role_value in roles_in.items():
            where = f"roles.{role}"
            table = _table(role_value, where)
            _only_keys(table, {"programs", "extends", "description"}, where)
            description = table.get("description", "")
            if not isinstance(description, str):
                raise PolicyError(f"{where}: description must be a string")
            descriptions[role] = description
            parents = table.get("extends", [])
            if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
                raise PolicyError(f"{where}: extends must be a list of role names")
            extends[role] = list(parents)
            programs_in = table.get("programs", {})
            if not isinstance(programs_in, Mapping) or (not programs_in and not parents):
                raise PolicyError(f"{where}: needs programs or extends")
            programs: dict[str, _Program] = {}
            for program, value in programs_in.items():
                spot = f"{where}.programs.{program}"
                if not _PROGRAM_NAME.fullmatch(program):
                    raise PolicyError(f"{spot}: program name must match {_PROGRAM_NAME.pattern}")
                if program in unsafe.programs:
                    raise PolicyError(f"{spot}: {program} is also listed as unsafe")
                programs[program] = _load_program(_table(value, spot), spot)
            own[role] = programs
        roles = {role: _resolve(role, own, extends, ()) for role in own}
        return cls(unsafe, roles, descriptions)

    def roles(self) -> frozenset[str]:
        return frozenset(self._roles)

    def description(self, role: str) -> str:
        """What kind of host ``role`` is, in a few words, or "" if the policy does not say."""
        return self._descriptions.get(role, "")

    def usage(self, role: str) -> dict[str, str]:
        """Each program allowed on ``role`` and its usage line, for telling the model."""
        return {name: p.usage for name, p in sorted(self._roles.get(role, {}).items())}

    def argument_patterns(self, role: str) -> dict[str, list[re.Pattern[str]]]:
        """Every argument pattern per program on ``role``. For checks on the policy itself."""
        out: dict[str, list[re.Pattern[str]]] = {}
        for name, program in self._roles.get(role, {}).items():
            patterns = [p for form in program.forms for p in form]
            patterns += list(program.args or ()) + list(program.require)
            patterns += list(program.values.values())
            if program.first is not None:
                patterns.append(program.first)
            out[name] = patterns
        return out

    def check(self, role: str, command: str) -> Decision:
        """Decide whether ``command`` may run on a host with this ``role``."""
        if not command.strip(" "):
            return Deny("empty command", "unlisted")
        if len(command) > MAX_COMMAND_LENGTH:
            return Deny("command too long", "unsafe")
        for char in command:
            if not " " <= char <= "~":
                return Deny("non-printable or non-ASCII character", "unsafe")
        for char in command:
            if char in INJECTION_CHARS:
                return Deny(f"forbidden character: {char}", "unsafe")
        if ".." in command:
            return Deny("path traversal", "unsafe")
        argv = tuple(command.split())  # only spaces can be left to split on
        program_name, arguments = argv[0], argv[1:]
        # Unsafe intent is judged before unsupported syntax, so that quoting a dangerous
        # command does not turn it into a merely unlisted one.
        refused = self._unsafe.reason(program_name, [a.strip("'\"") for a in arguments])
        if refused is not None:
            return Deny(refused, "unsafe")
        for char in command:
            if char in UNSUPPORTED_CHARS:
                return Deny(
                    f"unsupported character: {char}. Use plain space-separated arguments, "
                    "without quotes, wildcards or '='",
                    "unlisted",
                )
        programs = self._roles.get(role)
        if programs is None:
            return Deny(f"unknown role: {role}", "unlisted")
        program = programs.get(program_name)
        if program is None:
            listed = ", ".join(sorted(programs))
            return Deny(
                f"program not allowed: {program_name}. Allowed on {role} hosts: {listed}",
                "unlisted",
            )
        if program.accepts(arguments):
            return Allow(argv)
        return Deny(f"arguments not allowed for {program_name}. Usage: {program.usage}", "unlisted")


def _resolve(
    role: str,
    own: Mapping[str, Mapping[str, _Program]],
    extends: Mapping[str, Sequence[str]],
    seen: tuple[str, ...],
) -> dict[str, _Program]:
    if role in seen:
        raise PolicyError(f"roles.{role}: extends forms a cycle: {' -> '.join((*seen, role))}")
    programs: dict[str, _Program] = {}
    for parent in extends[role]:
        if parent not in own:
            raise PolicyError(f"roles.{role}: extends unknown role: {parent}")
        programs.update(_resolve(parent, own, extends, (*seen, role)))
    programs.update(own[role])
    return programs


def _load_unsafe(value: object) -> _Unsafe:
    where = "unsafe"
    if not isinstance(value, Mapping):
        raise PolicyError(f"{where}: expected a table")
    _only_keys(value, {"programs", "paths", "any_args", "first", "args"}, where)
    programs = value.get("programs", [])
    if not isinstance(programs, list) or not all(isinstance(p, str) for p in programs):
        raise PolicyError(f"{where}.programs: expected a list of program names")
    paths = value.get("paths", [])
    if not isinstance(paths, list):
        raise PolicyError(f"{where}.paths: expected a list of patterns")
    any_args = value.get("any_args")
    return _Unsafe(
        programs=frozenset(programs),
        paths=tuple(_compile(p, f"{where}.paths", re.IGNORECASE) for p in paths),
        any_args=None if any_args is None else _compile(any_args, f"{where}.any_args"),
        first=_pattern_table(value.get("first", {}), f"{where}.first"),
        args=_pattern_table(value.get("args", {}), f"{where}.args"),
    )


def _pattern_table(value: object, where: str) -> dict[str, re.Pattern[str]]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{where}: expected a table of program = pattern")
    return {program: _compile(pattern, f"{where}.{program}") for program, pattern in value.items()}


def _load_program(table: Mapping[str, object], where: str) -> _Program:
    _only_keys(table, {"usage", "forms", "args", "values", "max_args", "first", "require"}, where)
    usage = table.get("usage")
    if not isinstance(usage, str) or not usage.strip():
        raise PolicyError(f"{where}: usage must be a non-empty string")
    forms_in, args_in = table.get("forms"), table.get("args")
    if forms_in is None and args_in is None:
        raise PolicyError(f"{where}: needs forms or args")
    forms: list[tuple[re.Pattern[str], ...]] = []
    if forms_in is not None:
        if not isinstance(forms_in, list) or not forms_in:
            raise PolicyError(f"{where}: forms must be a non-empty list of lists")
        for form in forms_in:
            if not isinstance(form, list):
                raise PolicyError(f"{where}: each form must be a list of patterns")
            forms.append(tuple(_compile(p, f"{where}.forms") for p in form))
    args: tuple[re.Pattern[str], ...] | None = None
    if args_in is not None:
        if not isinstance(args_in, list):
            raise PolicyError(f"{where}: args must be a list of patterns")
        args = tuple(_compile(p, f"{where}.args") for p in args_in)
        if not args and "values" not in table:
            raise PolicyError(f"{where}: args is empty and there are no values")
    elif any(key in table for key in ("values", "max_args", "first", "require")):
        raise PolicyError(f"{where}: values, max_args, first and require need args")
    values = _pattern_table(table.get("values", {}), f"{where}.values")
    if any(not option.startswith("-") for option in values):
        raise PolicyError(f"{where}: values keys must be options starting with '-'")
    max_args = table.get("max_args", DEFAULT_MAX_ARGS)
    if not isinstance(max_args, int) or isinstance(max_args, bool) or max_args < 1:
        raise PolicyError(f"{where}: max_args must be a positive integer")
    first = table.get("first")
    require = table.get("require", [])
    if not isinstance(require, list):
        raise PolicyError(f"{where}: require must be a list of patterns")
    return _Program(
        usage=usage,
        forms=tuple(forms),
        args=args,
        values=values,
        max_args=max_args,
        first=None if first is None else _compile(first, f"{where}.first"),
        require=tuple(_compile(p, f"{where}.require") for p in require),
    )


def _compile(pattern: object, where: str, flags: int = 0) -> re.Pattern[str]:
    if not isinstance(pattern, str):
        raise PolicyError(f"{where}: each pattern must be a string")
    try:
        return re.compile(pattern, flags)
    except re.error as error:
        raise PolicyError(f"{where}: invalid pattern {pattern!r}: {error}") from error


def _table(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise PolicyError(f"{where}: expected a non-empty table")
    return value


def _only_keys(table: Mapping[str, object], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise PolicyError(f"{where}: unknown key(s): {', '.join(unknown)}")
