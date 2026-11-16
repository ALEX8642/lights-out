from pathlib import Path

from check_sanitised import find_violations

REPO_ROOT = Path(__file__).resolve().parent.parent

# Built at run time so this file passes the check it tests.
PRIVATE_A = ".".join(["192", "168", "1", "1"])
PRIVATE_B = ".".join(["172", "16", "0", "9"])
BENCHMARK = ".".join(["198", "18", "0", "1"])


def test_allows_documentation_and_lab_ranges(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("192.0.2.7 10.20.3.4 127.0.0.1 9.9.9.9\n")
    assert find_violations(tmp_path) == []


def test_flags_other_private_and_public_addresses(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text(f"ok 192.0.2.1\nbad {PRIVATE_A} and {PRIVATE_B}\n")
    assert find_violations(tmp_path) == [f"docs/a.md:2: {PRIVATE_A}", f"docs/a.md:2: {PRIVATE_B}"]


def test_scans_readme_at_the_root(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(f"see {BENCHMARK}\n")
    assert find_violations(tmp_path) == [f"README.md:1: {BENCHMARK}"]


def test_repo_itself_is_clean() -> None:
    assert find_violations(REPO_ROOT) == []
