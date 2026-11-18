"""Changelog fragments: one small file per change, assembled at release time.

Every pull request adds ``changes/<issue>.<kind>.md`` instead of editing CHANGELOG.md, so pull
requests never conflict over the changelog. At release, ``build_changelog.py --release X.Y.Z``
writes the fragments into a new section of CHANGELOG.md and removes them.

    uv run python scripts/build_changelog.py              print the next section
    uv run python scripts/build_changelog.py --check      fail if a fragment is badly named
    uv run python scripts/build_changelog.py --release 0.2.0 --date 2026-11-20
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KINDS = ("added", "changed", "deprecated", "removed", "fixed", "security", "design")
_NAME = re.compile(r"(\d+)\.(" + "|".join(KINDS) + r")\.md")
ROOT = Path(__file__).resolve().parent.parent


def fragments(directory: Path) -> list[tuple[int, str, Path]]:
    """(issue, kind, path) for every fragment, sorted by kind order then issue number."""
    found = []
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        match = _NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"{path.name}: name must be <issue>.<kind>.md, kind one of {KINDS}")
        found.append((int(match.group(1)), match.group(2), path))
    return sorted(found, key=lambda f: (KINDS.index(f[1]), f[0]))


def build(directory: Path) -> str:
    sections: list[str] = []
    for kind in KINDS:
        bodies = [p.read_text().strip() for _, k, p in fragments(directory) if k == kind]
        if bodies:
            sections.append(f"### {kind.capitalize()}\n\n" + "\n".join(bodies) + "\n")
    return "\n".join(sections)


def release(version: str, date: str) -> None:
    changes, changelog = ROOT / "changes", ROOT / "CHANGELOG.md"
    body = build(changes)
    if not body:
        raise SystemExit("no fragments to release")
    text = changelog.read_text()
    marker = "## [Unreleased]\n"
    if marker not in text:
        raise SystemExit("CHANGELOG.md has no [Unreleased] heading")
    text = text.replace(marker, f"{marker}\n## [{version}] - {date}\n\n{body}", 1)
    changelog.write_text(text)
    for _, _, path in fragments(changes):
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--release", metavar="VERSION")
    parser.add_argument("--date", help="release date, YYYY-MM-DD")
    args = parser.parse_args()
    try:
        if args.release:
            if not args.date:
                parser.error("--release needs --date")
            release(args.release, args.date)
        elif args.check:
            fragments(ROOT / "changes")
        else:
            print(build(ROOT / "changes"))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
