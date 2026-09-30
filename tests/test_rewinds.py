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


# ---------------------------------------------------------------------
# Simplified invocation tests: bare form, tolerant order, picker, log.


def test_bare_invocation_without_subcommand(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    # `run` is required again; a bare file gets a friendly hint, not
    # argparse's invalid-choice wall.
    rc = main(["--repo", str(demo_repo), "calc.py", "HEAD~2"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown command" in err
    assert "rewinds run calc.py @HEAD~1" in err


def test_ref_first_order(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "run", "@HEAD~2:calc.py"])
    assert rc == 0
    assert "total: 100" in capsys.readouterr().out


def test_ref_first_space_separated(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "run", "@HEAD~2", "calc.py"])
    assert rc == 0
    assert "total: 100" in capsys.readouterr().out


def test_picker_noninteractive_lists_versions(
    demo_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    from rewinds.cli import main

    # No ref and no tty: print the version list and exit 2, never hang.
    rc = main(["--repo", str(demo_repo), "run", "calc.py"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "recent versions of" in err
    assert "re-run with a ref" in err


def test_picker_explicit_ref_bypasses_picker(
    demo_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "show", "README.md", "HEAD~2"])
    assert rc == 0
    assert "demo" in capsys.readouterr().out


def test_log_command(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "log", "calc.py"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "history of" in out
    assert "good version" in out
    assert "bad version" in out


def test_log_unknown_file(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "log", "never-existed.txt"])
    assert rc == 1


# ---------------------------------------------------------------------
# Time-based refs: @YYYY-MM-DD, @YYYY-MM, @YYYY, and relative words.


def _commit_at(repo: Path, when: str, marker: str) -> None:
    """Make a commit whose committer date is `when`, containing marker."""
    (repo / "calc.py").write_text(f"print('{marker}')\n")
    _git(repo, "add", "-A")
    env = {**__import__("os").environ,
           "GIT_COMMITTER_DATE": when,
           "GIT_AUTHOR_DATE": when}
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", marker],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.fixture()
def time_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "time_demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")

    _commit_at(repo, "2026-01-10T12:00:00", "v-january")
    _commit_at(repo, "2026-03-05T12:00:00", "v-march")
    _commit_at(repo, "2026-06-20T12:00:00", "v-june")
    _commit_at(repo, "2026-09-01T12:00:00", "v-september")
    return repo


def test_time_ref_exact_day(time_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt

    # @2026-06-01 means "as it stood on June 1": the newest commit at or
    # before the END of that day, so a commit later that same day counts.
    extraction = xt(time_repo, "@2026-03-05")
    try:
        assert "v-march" in (extraction.root / "calc.py").read_text()
    finally:
        extraction.cleanup()


def test_time_ref_month_boundary(time_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt

    # @2026-05 = end of May -> the March commit is the newest ancestor.
    extraction = xt(time_repo, "@2026-05")
    try:
        assert "v-march" in (extraction.root / "calc.py").read_text()
    finally:
        extraction.cleanup()


def test_time_ref_year_end(time_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt

    # @2026 = the end of 2026 -> everything so far; HEAD wins.
    extraction = xt(time_repo, "@2026")
    try:
        assert "v-september" in (extraction.root / "calc.py").read_text()
    finally:
        extraction.cleanup()


def test_time_ref_before_history_falls_back(time_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    # 2025 predates the repo: fall back to the earliest commit, loudly.
    rc = main(["--repo", str(time_repo), "run", "calc.py", "@2025"])
    out = capsys.readouterr()
    assert rc == 0
    assert "v-january" in out.out
    assert "no commits existed before" in out.err


def test_time_ref_after_history_is_just_head(time_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt

    extraction = xt(time_repo, "@2027-01-01")
    try:
        assert "v-september" in (extraction.root / "calc.py").read_text()
    finally:
        extraction.cleanup()


def test_real_tag_beats_time_ref(time_repo: Path) -> None:
    from rewinds.extract import resolve_ref

    # A tag named 2026 must resolve as a tag, not as the year 2026.
    _git(time_repo, "tag", "2026", "HEAD~1")
    assert resolve_ref(time_repo, "2026") == resolve_ref(time_repo, "HEAD~1")


def test_bare_head_like_refs_still_work(time_repo: Path) -> None:
    from rewinds.extract import resolve_ref

    assert resolve_ref(time_repo, "HEAD") == resolve_ref(time_repo, "@HEAD")
    assert resolve_ref(time_repo, "HEAD~2") == resolve_ref(time_repo, "@HEAD~2")


def test_invalid_time_ref_gives_hint(demo_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(demo_repo), "run", "calc.py", "@2026-13-99"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "accepted:" in err


def test_unknown_ref_still_errors(time_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(time_repo), "run", "calc.py", "@nope-not-a-ref"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown ref" in err


# ---------------------------------------------------------------------
# Ephemeral venv tests.
#
# These create repos whose history includes a requirements.txt. The
# pinned package is "six" (tiny, universal, no build step) so the suite
# stays fast and works offline only if pip can reach an index -- pip
# installs need network, which is a documented property of the feature.

PINNED = "six==1.16.0"


def _venv_repo(tmp_path: Path) -> Path:
    """A repo whose calc.py needs a dependency from requirements.txt."""
    repo = tmp_path / "venv_demo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "requirements.txt").write_text(PINNED + "\n")
    (repo / "calc.py").write_text(
        "import six\nprint('six version:', six.__version__)\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add pinned script")
    return repo


@pytest.fixture()
def venv_repo(tmp_path: Path) -> Path:
    return _venv_repo(tmp_path)


def test_find_env_file(venv_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt
    from rewinds.venv import find_env_file

    extraction = xt(venv_repo, "HEAD")
    try:
        assert find_env_file(extraction) == "requirements.txt"
    finally:
        extraction.cleanup()


def test_no_env_file_means_no_venv(demo_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt
    from rewinds.venv import ensure_venv

    extraction = xt(demo_repo, "HEAD")
    try:
        assert ensure_venv(extraction) is None
    finally:
        extraction.cleanup()


def test_run_inside_ephemeral_venv(venv_repo: Path) -> None:
    from rewinds.extract import extract_tree as xt
    from rewinds.venv import ensure_venv
    from rewinds.runner import run_file as rf

    extraction = xt(venv_repo, "HEAD")
    try:
        venv = ensure_venv(extraction)
        assert venv is not None
        result = rf(extraction, "calc.py", interpreter=venv.interpreter())
        assert result.ok, result.stderr
        assert "six version: 1.16.0" in result.stdout
    finally:
        extraction.cleanup()


def test_cli_run_uses_venv(venv_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    rc = main(["--repo", str(venv_repo), "run", "calc.py", "HEAD"])
    assert rc == 0
    assert "six version: 1.16.0" in capsys.readouterr().out


def test_cli_no_venv_flag_skips_env(venv_repo: Path, capsys: pytest.CaptureFixture) -> None:
    from rewinds.cli import main

    # The system interpreter may or may not have six installed; either
    # way the flag must prevent any venv build attempt. What we must not
    # see is six 1.16.0, which only the ephemeral env can provide.
    rc = main(["--repo", str(venv_repo), "run", "calc.py", "HEAD", "--no-venv"])
    out = capsys.readouterr()
    assert rc in (0, 1)
    assert "six version: 1.16.0" not in out.out


def test_bisect_same_good_and_bad_raises(venv_repo: Path) -> None:
    from rewinds.bisect import bisect as b

    with pytest.raises(GitError):
        b(venv_repo, path="calc.py", good="HEAD", bad="HEAD")


def test_bisect_runs_each_commit_in_its_own_venv(tmp_path: Path) -> None:
    from rewinds.bisect import bisect as b

    repo = tmp_path / "venv_bisect"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")

    # commit 1: six 1.16.0, script reports its version
    (repo / "requirements.txt").write_text("six==1.16.0\n")
    (repo / "calc.py").write_text(
        "import six\nprint('six version:', six.__version__)\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "good pin")

    # commit 2: bump the pin, output changes accordingly
    (repo / "requirements.txt").write_text("six==1.12.0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "downgrade pin")

    report = b(
        repo,
        path="calc.py",
        good="HEAD~1",
        bad="HEAD",
        expect_output="six version: 1.16.0",
    )
    assert report.culprit is not None
    assert report.culprit == resolve_ref(repo, "HEAD")
    # both steps ran inside their own ephemeral envs, no failure marks
    assert all(not s.venv_failed for s in report.steps)
