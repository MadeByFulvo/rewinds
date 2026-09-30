# The feature that earns rewinds its keep: find the commit that broke things.
#
# git bisect already exists, but it wants a test command that returns
# good or bad. Most of the time you don't have one. What you do have is
# a script whose output suddenly changed. So we run that script at each
# commit in the range and watch for the moment its behavior flips.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .extract import extract_tree, resolve_ref, GitError
from .runner import NoInterpreterError, RunResult, run_file
from .venv import ensure_venv, VenvError


# What happened when we ran the file at one particular commit.
@dataclass
class BisectStep:
    sha: str
    short: str  # short sha, for printing
    result: RunResult
    venv_failed: bool = False  # env build failed; ran with system python
    behavior_ok: bool = True  # false when exit code or --expect marked it bad


# The whole story of a bisect run.
# culprit is the commit people actually care about: the first one where
# behavior changed. It's None when nothing changed in the range, which
# usually means the problem lies elsewhere.
@dataclass
class BisectReport:
    good: str
    bad: str
    culprit: str | None
    steps: list[BisectStep]

    def render(self) -> str:
        lines = [
            f"**bisect** `{self.good[:8]}` .. `{self.bad[:8]}`",
            "",
        ]
        for s in self.steps:
            status = "OK" if s.behavior_ok else "**FAIL**"
            note = " *(venv failed; system python)*" if s.venv_failed else ""
            lines.append(f"  `{s.short}`  {status}{note}")
        lines.append("")
        if self.culprit:
            lines.append(f"> **first bad commit:** `{self.culprit}`")
        else:
            lines.append("> no behavior change detected in this range")
        return "\n".join(lines)


def _run_git(repo: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


# Every commit between good and bad, oldest first, good itself excluded.
# The good commit is excluded because we already know it behaves well.
def _commits_between(repo: Path, good_sha: str, bad_sha: str) -> list[str]:
    out = _run_git(repo, "rev-list", f"{good_sha}..{bad_sha}", "--reverse")
    return [c for c in out.strip().splitlines() if c]


# Walk the range, run the file at each commit, stop at the first change.
#
# Two things count as "behavior changed": the exit code goes nonzero, or
# expect_output was given and stdout no longer contains it. Either way,
# the first commit that trips is the culprit.
#
# Files we don't know how to run are skipped instead of failing the
# whole bisect. A missing interpreter says nothing about the commit.
#
# With use_venv (the default) every commit runs inside its own ephemeral
# environment, built from that commit's dependency manifest -- pins may
# legitimately change across the range, and each commit should be judged
# in its own world. A venv build failure is not a behavior change, so we
# fall back to the system interpreter and mark the step.
def bisect(
    repo: Path,
    path: str,
    good: str,
    bad: str = "HEAD",
    args: list[str] | None = None,
    expect_output: str | None = None,
    use_venv: bool = True,
) -> BisectReport:
    good_sha = resolve_ref(repo, good)
    bad_sha = resolve_ref(repo, bad)
    candidates = _commits_between(repo, good_sha, bad_sha)
    if not candidates:
        raise GitError(f"{good} and {bad} are the same commit (or bad is older)")

    steps: list[BisectStep] = []
    culprit: str | None = None

    for sha in candidates:
        extraction = extract_tree(repo, sha)
        try:
            venv = None
            venv_failed = False
            if use_venv:
                try:
                    venv = ensure_venv(extraction)
                except VenvError:
                    # pip failing to build an old pin is environment
                    # trouble, not the behavior we're hunting for.
                    venv_failed = True
            result = run_file(
                extraction,
                path,
                args=args,
                timeout=60.0,
                interpreter=venv.interpreter() if venv else None,
            )
        except NoInterpreterError:
            extraction.cleanup()
            continue
        finally:
            extraction.cleanup()

        behavior_bad = not result.ok or (
            expect_output is not None and expect_output not in result.stdout
        )
        steps.append(
            BisectStep(
                sha=sha,
                short=sha[:8],
                result=result,
                venv_failed=venv_failed,
                behavior_ok=not behavior_bad,
            )
        )
        if behavior_bad:
            culprit = sha
            break

    return BisectReport(good=good_sha, bad=bad_sha, culprit=culprit, steps=steps)
