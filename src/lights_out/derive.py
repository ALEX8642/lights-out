"""Answers derived from a closed-world host's recordings.

When a scenario marks a host ``closed_world``, its recordings are the host's whole state. A file
that no recording reads does not exist; a service missing from a recorded listing does not
exist. Some commands can then be answered without their own recording, deterministically:

- ``cat``, ``head``, ``tail``, ``wc`` and ``grep`` of recorded files are computed from the
  recorded contents, and of unknown paths give the real "No such file or directory";
- ``ls`` of a directory lists the recorded files below it (``-R``: every directory below);
- ``find DIR -name NAME`` and ``file PATH`` answer from the recorded files and the
  directories they imply;
- ``systemctl status``/``is-*``, ``journalctl -u`` and ``service NAME status`` of a service
  missing from the host's recorded listing give the real "not found" answers.

Anything else returns None and stays unrecorded. See docs/adr/0008-replay-matching.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from lights_out.scenario import Recording


@dataclass(frozen=True)
class Derived:
    exit_code: int
    stdout: str


class HostState:
    """What a closed-world host's recordings say exists on it."""

    def __init__(self, recordings: Mapping[tuple[str, ...], Recording]) -> None:
        self.files: dict[str, str] = {}
        self.units: set[str] | None = None  # None: no listing recorded, so say nothing
        self.services: set[str] | None = None
        for argv, recording in recordings.items():
            if argv[0] == "cat" and len(argv) == 2 and argv[1].startswith("/"):
                self.files[argv[1].rstrip("/")] = recording.stdout
            if argv[0] == "systemctl" and len(argv) > 1 and argv[1].startswith("list-unit"):
                found = re.findall(r"([A-Za-z0-9@._:-]+)\.service\b", recording.stdout)
                self.units = (self.units or set()) | set(found)
            if argv[:2] == ("service", "-e"):
                lines = recording.stdout.split()
                self.services = (self.services or set()) | {
                    line.rsplit("/", 1)[-1] for line in lines
                }

    def is_dir(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return path == "/" or any(f.startswith(prefix) for f in self.files)

    def children(self, path: str) -> list[str]:
        prefix = "/" if path == "/" else path.rstrip("/") + "/"
        return sorted(
            {f[len(prefix) :].split("/", 1)[0] for f in self.files if f.startswith(prefix)}
        )


def derive(state: HostState, argv: tuple[str, ...]) -> Derived | None:
    program, args = argv[0], list(argv[1:])
    handler = _HANDLERS.get(program)
    return handler(state, args) if handler else None


def _paths(args: Sequence[str]) -> list[str]:
    return [a for a in args if a.startswith("/")]


def _cat(state: HostState, args: list[str]) -> Derived | None:
    paths = _paths(args)
    if not paths:
        return None
    out, code = [], 0
    for path in paths:
        key = path.rstrip("/") or "/"
        if key in state.files:
            out.append(state.files[key])
        elif state.is_dir(key):
            out.append(f"cat: {path}: Is a directory\n")
            code = 1
        else:
            out.append(f"cat: {path}: No such file or directory\n")
            code = 1
    return Derived(code, "".join(out))


def _ls(state: HostState, args: list[str]) -> Derived | None:
    paths = _paths(args)
    if not paths:
        return None
    if any(a.startswith("-") and "R" in a for a in args):
        return _ls_recursive(state, paths)
    out, code = [], 0
    for path in paths:
        key = path.rstrip("/") or "/"
        if key in state.files:
            body = f"{path}\n"
        elif state.is_dir(key):
            body = "".join(f"{name}\n" for name in state.children(key))
        else:
            out.append(f"ls: cannot access '{path}': No such file or directory\n")
            code = 2
            continue
        out.append(f"{path}:\n{body}\n" if len(paths) > 1 else body)
    return Derived(code, "".join(out))


def _ls_recursive(state: HostState, paths: list[str]) -> Derived:
    out, code = [], 0
    for path in paths:
        key = path.rstrip("/") or "/"
        if not state.is_dir(key):
            if key in state.files:
                out.append(f"{path}\n")
            else:
                out.append(f"ls: cannot access '{path}': No such file or directory\n")
                code = 2
            continue
        pending = [key]
        while pending:
            directory = pending.pop(0)
            names = state.children(directory)
            out.append(f"{directory}:\n" + "".join(f"{name}\n" for name in names) + "\n")
            pending.extend(
                f"{directory.rstrip('/')}/{name}"
                for name in names
                if state.is_dir(f"{directory.rstrip('/')}/{name}")
            )
    return Derived(code, "".join(out).removesuffix("\n"))


def _find(state: HostState, args: list[str]) -> Derived | None:
    options = dict(zip(args, args[1:], strict=False))  # each argument -> the one after it
    name, kind, depth = options.get("-name"), options.get("-type"), options.get("-maxdepth")
    values = {options[k] for k in ("-name", "-type", "-maxdepth") if k in options}
    roots = [a for a in args if a.startswith("/") and a not in values]
    if name is None or not roots or kind not in {None, "f", "d"}:
        return None
    limit = int(depth) if depth is not None and depth.isdigit() else None
    out, code = [], 0
    for root in roots:
        top = root.rstrip("/") or "/"
        if not state.is_dir(top):
            out.append(f"find: '{root}': No such file or directory\n")
            code = 1
            continue
        found: set[str] = set()
        for path in state.files:
            if not path.startswith(top.rstrip("/") + "/"):
                continue
            parts = path[len(top.rstrip("/")) + 1 :].split("/")
            for index in range(len(parts)):
                candidate = top.rstrip("/") + "/" + "/".join(parts[: index + 1])
                is_file = index == len(parts) - 1
                if parts[index] != name or (limit is not None and index + 1 > limit):
                    continue
                if kind == "f" and not is_file or kind == "d" and is_file:
                    continue
                found.add(candidate)
        out.extend(f"{path}\n" for path in sorted(found))
    return Derived(code, "".join(out))


def _file(state: HostState, args: list[str]) -> Derived | None:
    paths = _paths(args)
    if not paths or len(paths) != len(args):
        return None
    out = []
    for path in paths:
        key = path.rstrip("/") or "/"
        if key in state.files:
            out.append(f"{path}: ASCII text\n")
        elif state.is_dir(key):
            out.append(f"{path}: directory\n")
        else:
            out.append(f"{path}: cannot open `{path}' (No such file or directory)\n")
    return Derived(0, "".join(out))


def _lines_option(args: list[str]) -> tuple[int, list[str]]:
    count, rest, i = 10, [], 0
    while i < len(args):
        if args[i] == "-n" and i + 1 < len(args) and args[i + 1].isdigit():
            count, i = int(args[i + 1]), i + 2
            continue
        rest.append(args[i])
        i += 1
    return count, rest


def _head_or_tail(program: str) -> Callable[[HostState, list[str]], Derived | None]:
    def handler(state: HostState, args: list[str]) -> Derived | None:
        count, rest = _lines_option(args)
        paths = _paths(rest)
        if len(paths) != 1:
            return None
        path = paths[0]
        if path not in state.files:
            return Derived(
                1, f"{program}: cannot open '{path}' for reading: No such file or directory\n"
            )
        lines = state.files[path].splitlines(keepends=True)
        picked = lines[:count] if program == "head" else lines[-count:] if count else []
        return Derived(0, "".join(picked))

    return handler


def _wc(state: HostState, args: list[str]) -> Derived | None:
    flags = "".join(a[1:] for a in args if a.startswith("-"))
    paths = _paths(args)
    if len(paths) != 1:
        return None
    path = paths[0]
    if path not in state.files:
        return Derived(1, f"wc: {path}: No such file or directory\n")
    text = state.files[path]
    counts = {"l": text.count("\n"), "w": len(text.split()), "c": len(text.encode())}
    shown = [str(counts[k]) for k in "lwc" if not flags or k in flags]
    return Derived(0, f"{' '.join(shown)} {path}\n")


def _grep(state: HostState, args: list[str]) -> Derived | None:
    letters: set[str] = set()
    max_count: int | None = None
    words: list[str] = []
    i = 0
    while i < len(args):
        argument = args[i]
        if argument == "-m" and i + 1 < len(args) and args[i + 1].isdigit():
            max_count, i = int(args[i + 1]), i + 2
            continue
        if argument.startswith("-") and len(argument) > 1:
            letters.update(argument[1:])
        else:
            words.append(argument)
        i += 1
    patterns = [w for w in words if not w.startswith("/")]
    paths = [w for w in words if w.startswith("/")]
    if len(patterns) != 1 or not paths:
        return None
    flags = re.IGNORECASE if "i" in letters else 0
    text = patterns[0]
    if "F" in letters:
        text = re.escape(text)
    if "w" in letters:
        text = rf"\b(?:{text})\b"
    try:
        regex = re.compile(text, flags)
    except re.error:
        regex = re.compile(re.escape(patterns[0]), flags)
    show_name = len(paths) > 1 and "h" not in letters or "H" in letters
    out, error, matched_any = [], False, False
    for path in paths:
        if path not in state.files:
            out.append(f"grep: {path}: No such file or directory\n")
            error = True
            continue
        hits: list[tuple[int, str]] = []
        for number, line in enumerate(state.files[path].splitlines(), start=1):
            if bool(regex.search(line)) != ("v" in letters):
                hits.append((number, line))
                if max_count is not None and len(hits) >= max_count:
                    break
        matched_any = matched_any or bool(hits)
        prefix = f"{path}:" if show_name else ""
        if "l" in letters:
            out.extend([f"{path}\n"] if hits else [])
        elif "c" in letters:
            out.append(f"{prefix}{len(hits)}\n")
        else:
            out.extend(f"{prefix}{f'{n}:' if 'n' in letters else ''}{line}\n" for n, line in hits)
    return Derived(2 if error else 0 if matched_any else 1, "".join(out))


def _units(args: list[str]) -> list[str]:
    return [a.removesuffix(".service") for a in args if not a.startswith("-")]


def _systemctl(state: HostState, args: list[str]) -> Derived | None:
    if state.units is None or len(args) < 2:
        return None
    verb, units = args[0], _units(args[1:])
    if len(units) != 1 or units[0] in state.units:
        return None
    unit = units[0]
    answers = {
        "status": Derived(4, f"Unit {unit}.service could not be found.\n"),
        "is-active": Derived(3, "inactive\n"),
        "is-failed": Derived(1, "inactive\n"),
        "is-enabled": Derived(
            1, f"Failed to get unit file state for {unit}.service: No such file or directory\n"
        ),
    }
    return answers.get(verb)


def _journalctl(state: HostState, args: list[str]) -> Derived | None:
    if state.units is None:
        return None
    units = [args[i + 1] for i, a in enumerate(args[:-1]) if a in {"-u", "--unit"}]
    if len(units) != 1 or units[0].removesuffix(".service") in state.units:
        return None
    return Derived(0, "-- No entries --\n")


def _service(state: HostState, args: list[str]) -> Derived | None:
    if state.services is None or len(args) != 2 or args[1] not in {"status", "onestatus"}:
        return None
    name = args[0]
    if name in state.services:
        return None
    return Derived(
        1,
        f"{name} does not exist in /etc/rc.d or the local startup directories "
        "(/usr/local/etc/rc.d)\n",
    )


_HANDLERS = {
    "cat": _cat,
    "ls": _ls,
    "find": _find,
    "file": _file,
    "head": _head_or_tail("head"),
    "tail": _head_or_tail("tail"),
    "wc": _wc,
    "grep": _grep,
    "systemctl": _systemctl,
    "journalctl": _journalctl,
    "service": _service,
}
