from pathlib import Path

import pytest
from build_changelog import KINDS, build, fragments


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)


def test_fragments_are_grouped_by_kind_in_a_fixed_order(tmp_path: Path) -> None:
    changes = tmp_path / "changes"
    write(changes, "40.added.md", "- Review tool (#40).\n")
    write(changes, "36.added.md", "- Batch runner (#36).\n")
    write(changes, "20.changed.md", "- Policy v2 (#20).\n")
    write(changes, "README.md", "not a fragment\n")
    assert build(changes) == (
        "### Added\n\n- Batch runner (#36).\n- Review tool (#40).\n\n"
        "### Changed\n\n- Policy v2 (#20).\n"
    )


def test_no_fragments_build_nothing(tmp_path: Path) -> None:
    (tmp_path / "changes").mkdir()
    assert build(tmp_path / "changes") == ""


@pytest.mark.parametrize("name", ["40.md", "40.misc.md", "x.added.md"])
def test_badly_named_fragments_are_errors(tmp_path: Path, name: str) -> None:
    write(tmp_path / "changes", name, "- x\n")
    with pytest.raises(ValueError, match=name):
        fragments(tmp_path / "changes")


def test_kinds_follow_keep_a_changelog() -> None:
    assert KINDS == ("added", "changed", "deprecated", "removed", "fixed", "security", "design")
