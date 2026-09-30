# The command line face of rewinds.
#
#   rewinds run FILE REF     run a file as it existed at a commit
#   rewinds show FILE REF    print a historical file without running it
#   rewinds bisect FILE      find the commit that changed a file's behavior
#   rewinds log FILE         list the commits that touched a file
#
# Output follows a small markdown dialect: bold labels, backticked refs
# and shas, and blockquotes for anything the user should read as an
# error or as guidance. It stays readable in a plain terminal and
# renders properly anywhere markdown is understood. Diagnostics go to
# stderr so stdout remains pipe-clean.
#
# Everything here is a thin layer over extract.py, runner.py, venv.py,
# and bisect.py; script against those if you need more.

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__
from .bisect import bisect as do_bisect
from .extract import extract_tree, resolve_ref, GitError, pop_time_warning
from .runner import run_file
from .venv import ensure_venv, VenvError

SUBCOMMANDS = ("run", "bisect", "show", "log")


# ----------------------------------------------------------------------
# Markdown-flavored output helpers.
#
# The dialect is deliberately tiny: blockquotes carry errors and hints,
# bold marks labels, backticks mark code. No colors, no emoji, no
# width-fiddling -- output survives `| cat`, log files, and CI logs.

def _blockquote(*lines: str, file=None) -> None:
    out = file if file is not None else sys.stderr
    for line in lines:
        print(f"> {line}" if line else ">", file=out)


def _error(*lines: str) -> None:
    _blockquote(f"**error:** {lines[0]}", "", *lines[1:])


def _note(*lines: str) -> None:
    _blockquote(*lines)


def _resolve_repo_arg(repo_arg: str | None) -> Path:
    if repo_arg:
        return Path(repo_arg).resolve()
    return Path.cwd()


def _git_log_rows(repo: Path, path: str, limit: int = 10) -> list[tuple[str, str, str]]:
    # (short sha, date, subject) for the commits that touched a file,
    # newest first. This drives both `rewinds log` and the ref picker.
    proc = subprocess.run(
        [
            "git", "-C", str(repo),
            "log", f"--max-count={limit}",
            "--date=short",
            "--pretty=format:%h%x09%ad%x09%s",
            "--", path,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git log failed:\n{proc.stderr.strip()}")
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _stdin_is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:  # pragma: no cover - exotic stdin objects
        return False


# Show the file's recent versions and let the user pick one.
#
# Accepts a row number, an empty answer (latest), or any ref string.
# When stdin isn't interactive the list is printed with a hint and None
# is returned, so CI pipelines get an error rather than a hang.
def _pick_ref(repo: Path, path: str, verb: str) -> str | None:
    rows = _git_log_rows(repo, path)
    if not rows:
        raise GitError(f"no commits in history touch `{path}`")

    print(f"\n**recent versions of `{path}`**\n", file=sys.stderr)
    for i, (sha, date, subject) in enumerate(rows, 1):
        print(f"  {i})  `{sha}`  {date}  {subject}", file=sys.stderr)

    if not _stdin_is_interactive():
        print(file=sys.stderr)
        _note(f"re-run with a ref: `rewinds {verb} {path} <ref>`")
        return None

    answer = input(f"\n> version to {verb} (number, ref, or Enter for latest): ").strip()
    if not answer:
        answer = "1"
    if answer.isdigit() and 1 <= int(answer) <= len(rows):
        answer = rows[int(answer) - 1][0]

    # Validate whatever we ended up with; one retry on a bad ref keeps
    # typos from killing the session.
    try:
        return resolve_ref(repo, answer)
    except GitError:
        _error(f"unknown ref `{answer}`")
        return None


# Handles every accepted spelling of a run target:
#   run calc.py @HEAD~2    classic form
#   run @HEAD~2:calc.py    ref-first form
#   run @HEAD~2 calc.py    ref-first, space separated
#   run calc.py            ref picked interactively
def _split_path_ref(args: argparse.Namespace) -> tuple[str, str | None]:
    path = args.path
    if path.startswith("@") or ":" in path:
        if ":" in path:
            ref, path = path.split(":", 1)
            return path.lstrip("@"), ref.lstrip("@") or None
        # '@ref file' form: argparse may land the file in ref or file_args
        # depending on how many tokens came after it.
        if args.file_args:
            return args.file_args.pop(0), path.lstrip("@")
        if args.ref:
            return args.ref, path.lstrip("@")
        raise GitError(
            f"expected a file after `{path}` -- example: `rewinds run {path} calc.py`"
        )
    return path, args.ref


def cmd_run(args: argparse.Namespace) -> int:
    repo = _resolve_repo_arg(args.repo)
    path, ref = _split_path_ref(args)

    if ref is None:
        picked = _pick_ref(repo, path, "run")
        if picked is None:
            return 2
        ref = picked

    extraction = extract_tree(repo, ref)
    _report_time_warning()
    try:
        # Ephemeral venv: when the commit declares dependencies, build a
        # throwaway environment from them and run inside it.
        venv = None
        if not args.no_venv:
            try:
                venv = ensure_venv(extraction)
            except VenvError as e:
                _error(str(e))
                if not args.keep_going:
                    return 2
                _note("falling back to the current interpreter")

        result = run_file(
            extraction,
            path,
            args=args.file_args,
            timeout=args.timeout,
            interpreter=venv.interpreter() if venv else None,
        )
        # pass the file's output straight through, like a normal command
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if not result.ok:
            _note(f"exited with code `{result.exit_code}`")
        return result.exit_code
    finally:
        if not args.keep:
            extraction.cleanup()
        else:
            _note(f"kept temp dir: `{extraction.root}`")


# Prints the report and exits 1 when a culprit was found, so a bisect
# can be dropped straight into CI or a shell script.
def cmd_bisect(args: argparse.Namespace) -> int:
    repo = _resolve_repo_arg(args.repo)
    report = do_bisect(
        repo,
        path=args.path,
        good=args.good,
        bad=args.bad,
        args=args.file_args,
        expect_output=args.expect,
        use_venv=not args.no_venv,
    )
    print(report.render())
    return 0 if report.culprit is None else 1


# Plain and quiet: cat, but from any point in history.
def cmd_show(args: argparse.Namespace) -> int:
    repo = _resolve_repo_arg(args.repo)
    ref = args.ref
    if ref is None:
        picked = _pick_ref(repo, args.path, "show")
        if picked is None:
            return 2
        ref = picked
    extraction = extract_tree(repo, ref)
    _report_time_warning()
    try:
        target = extraction.target(args.path)
        sys.stdout.write(target.read_text(errors="replace"))
    finally:
        extraction.cleanup()
    return 0


# A time ref that fell back to the earliest commit shouldn't be silent.
def _report_time_warning() -> None:
    warning = pop_time_warning()
    if warning:
        _note(warning)


# Every version of a file, newest first. Also what the picker shows.
def cmd_log(args: argparse.Namespace) -> int:
    repo = _resolve_repo_arg(args.repo)
    rows = _git_log_rows(repo, args.path, limit=args.n)
    if not rows:
        _error(f"no commits in history touch `{args.path}`")
        return 1
    print(f"**history of `{args.path}`**\n")
    for sha, date, subject in rows:
        print(f"  `{sha}`  {date}  {subject}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rewinds",
        description="Run any file from any commit, without touching your working tree.",
    )
    p.add_argument("--version", action="version", version=f"rewinds {__version__}")
    p.add_argument("--repo", help="path to the git repo (default: cwd)")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a file as it existed at a commit")
    run.add_argument("path", help="file to run (ref optional -- picker shown without one)")
    run.add_argument("ref", nargs="?", help="commit-ish: sha, tag, branch, @HEAD~3")
    run.add_argument("file_args", nargs="*", help="args passed to the file")
    run.add_argument("--timeout", type=float, default=120.0)
    run.add_argument("--keep", action="store_true", help="keep the temp extraction dir")
    run.add_argument(
        "--no-venv",
        action="store_true",
        help="skip the ephemeral venv even if the commit declares dependencies",
    )
    run.add_argument(
        "--keep-going",
        action="store_true",
        help="if venv setup fails, run with the current interpreter instead",
    )
    run.set_defaults(func=cmd_run)

    bs = sub.add_parser("bisect", help="find the commit that changed behavior")
    bs.add_argument("path", help="the file to run at each commit")
    bs.add_argument("--good", required=True, help="known-good ref")
    bs.add_argument("--bad", default="HEAD", help="known-bad ref (default HEAD)")
    bs.add_argument("--expect", help="stdout must contain this string to count as OK")
    bs.add_argument(
        "--no-venv",
        action="store_true",
        help="run every commit with the current interpreter, skip ephemeral venvs",
    )
    bs.add_argument("file_args", nargs="*", help="args passed to the file")
    bs.set_defaults(func=cmd_bisect)

    sh = sub.add_parser("show", help="print a historical file without running it")
    sh.add_argument("path", help="the file to print")
    sh.add_argument("ref", nargs="?", help="commit-ish (picker shown without one)")
    sh.set_defaults(func=cmd_show)

    lg = sub.add_parser("log", help="list the commits that touched a file")
    lg.add_argument("path", help="the file to list history for")
    lg.add_argument("-n", type=int, default=10, help="number of commits (default 10)")
    lg.set_defaults(func=cmd_log)

    return p


# Positional flags that consume the following token; anything else
# starting with '-' is valueless as far as first-positional detection
# goes.
_VALUE_FLAGS = {"--repo"}


def _first_positional(argv: list[str]) -> int | None:
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _VALUE_FLAGS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return i
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Friendlier than argparse's invalid-choice wall of text: when the
    # first positional isn't a subcommand, assume the user meant `run`
    # and say exactly what to type.
    pos = _first_positional(args)
    if pos is not None and args[pos] not in SUBCOMMANDS:
        file = args[pos]
        _error(
            f"unknown command `{file}`",
            f"to run a file: `rewinds run {file} <ref>`",
            f"example: `rewinds run {file} @HEAD~1`",
        )
        return 2

    parser = build_parser()
    parsed = parser.parse_args(args)
    try:
        return parsed.func(parsed)
    except GitError as e:
        _error(str(e))
        return 2
    except VenvError as e:
        _error(str(e))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
