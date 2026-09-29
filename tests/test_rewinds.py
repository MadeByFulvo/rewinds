# Tests for rewinds.
#
# Each test builds a real git repo from scratch: three commits, where
# calc.py prints one thing in the first, nothing relevant changes in
# the second, and calc.py changes its output in the third. Working
# against real git instead of fakes keeps the tests honest about the
# edge cases git actually produces.

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rewinds.bisect import bisect
from rewinds.extract import extract_tree, resolve_ref, GitError
from rewinds.runner import run_file


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


# The three-commit story described at the top of this file.
@pytest.fixture()
def demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")

    good = "print('total: 100')\n"
    (repo / "calc.py").write_text(good)
    (repo / "README.md").write_text("demo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "good version")

    # a commit that doesn't touch calc.py: bisect should skip past this
    (repo / "notes.txt").write_text("unrelated change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated change")

    bad = "print('total: 999')\n"
    (repo / "calc.py").write_text(bad)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bad version")
    return repo


def test_resolve_ref(demo_repo: Path) -> None:
    sha = resolve_ref(demo_repo, "HEAD")
    assert len(sha) == 40


def test_extract_and_run_good(demo_repo: Path) -> None:
    extraction = extract_tree(demo_repo, "HEAD~2")  # the good commit
    try:
        result = run_file(extraction, "calc.py")
        assert result.ok
        assert "total: 100" in result.stdout
    finally:
        extraction.cleanup()


def test_run_from_head_is_bad(demo_repo: Path) -> None:
    extraction = extract_tree(demo_repo, "HEAD")
    try:
        result = run_file(extraction, "calc.py")
        assert "total: 999" in result.stdout
    finally:
        extraction.cleanup()


def test_bisect_finds_culprit(demo_repo: Path) -> None:
    report = bisect(
        demo_repo,
        path="calc.py",
        good="HEAD~2",
        bad="HEAD",
        expect_output="total: 100",
    )
    assert report.culprit is not None
    # the break happened in the last commit, so that's the culprit
    assert report.culprit == resolve_ref(demo_repo, "HEAD")


def test_bisect_no_change(demo_repo: Path) -> None:
    # README.md never changed its behavior, so there should be no culprit
    report = bisect(
        demo_repo,
        path="README.md",
        good="HEAD~2",
        bad="HEAD",
        expect_output="demo",
    )
    assert report.culprit is None


def test_missing_file_raises(demo_repo: Path) -> None:
    extraction = extract_tree(demo_repo, "HEAD")
    try:
        with pytest.raises(GitError):
            extraction.target("nope/missing.py")
    finally:
        extraction.cleanup()


def test_cli_run(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "run", "calc.py", "HEAD~2"])
    assert rc == 0
    assert "total: 100" in capsys.readouterr().out
