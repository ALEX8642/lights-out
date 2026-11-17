"""Fail if the repository contains an IPv4 address outside the allowed fictional set.

Scenarios are modelled on real incidents. This check keeps real addresses out of the
repository: only documentation ranges, one lab range and a few public resolvers pass.
"""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path

ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "192.0.2.0/24",  # RFC 5737 documentation
        "198.51.100.0/24",  # RFC 5737 documentation
        "203.0.113.0/24",  # RFC 5737 documentation
        "10.20.0.0/16",  # the fictional lab LAN used by scenarios
        "10.0.0.0/32",  # the bare network number, as written in "10.0.0.0/8"
        "127.0.0.0/8",
        "0.0.0.0/32",
        "1.1.1.1/32",
        "1.1.1.2/32",
        "8.8.8.8/32",
        "9.9.9.9/32",
    )
)
SCANNED = ("scenarios", "policies", "tests", "docs", "src", "README.md")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def _allowed(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return True  # not an address (for example 999.1.1.1)
    return any(address in network for network in ALLOWED_NETWORKS)


def _files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in SCANNED:
        path = root / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p for p in sorted(path.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
            )
    return files


def find_violations(root: Path) -> list[str]:
    """Return one ``path:line: address`` entry per disallowed IPv4 literal under ``root``."""
    violations: list[str] = []
    for path in _files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary file
        for number, line in enumerate(text.splitlines(), start=1):
            for match in IPV4.finditer(line):
                if not _allowed(match.group()):
                    relative = path.relative_to(root).as_posix()
                    violations.append(f"{relative}:{number}: {match.group()}")
    return violations


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    violations = find_violations(root)
    for violation in violations:
        print(violation)
    if violations:
        print(f"{len(violations)} address(es) outside the allowed set", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
