# The command line face of rewinds.
#
#   rewinds run FILE REF     run a file as it existed at a commit
#   rewinds show FILE REF    print a historical file without running it
#   rewinds bisect FILE      find the commit that changed a file's behavior
#
# Everything here is a thin layer over extract.py, runner.py, and
# bisect.py. If you want to script rewinds, those are the modules to
# import; this file only worries about arguments and exit codes.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .bisect import bisect as do_bisect
from .extract import extract_tree, GitError
from .runner import run_file


def _resolve_repo_arg(repo_arg: str | None) -> Path:
    if repo_arg:
        return Path(repo_arg).resolve()
    return Path.cwd()


# Handles both "rewinds run calc.py @HEAD~2" and the terser
# "rewinds run @HEAD~2:calc.py" spelling.
def cmd_run(args: argparse.Namespace) -> int:
    repo = _resolve_repo_arg(args.repo)
    path = args.path
    if path.startswith("@") or ":" in path:
        # ref:file style, no separate ref argument needed
        if ":" in path:
            ref, path = path.split(":", 1)
            ref = ref.lstrip("@")
        else:
            print("error: expected <file> <ref>, or <ref>:<file>", file=sys.stderr)
            return 2
    else:
        ref = args.ref
        if not ref:
            print("error: missing commit ref", file=sys.stderr)
            return 2

    extraction = extract_tree(repo, ref)
    try:
        result = run_file(
            extraction,
            path,
            args=args.file_args,
            timeout=args.timeout,
        )
        # pass the file's output straight through, like a normal command
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if not result.ok:
            print(f"\n[rewinds] exited with code {result.exit_code}", file=sys.stderr)
        return result.exit_code
    finally:
        if not args.keep:
            extraction.cleanup()
        else:
            print(f"[rewinds] kept temp dir: {extraction.root}")


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
    )
    print(report.render())
    return 0 if report.culprit is None else 1


# Plain and quiet: cat, but from any point in history.
def cmd_show(args: argparse.Namespace) -> int:
    repo = _resolve_repo_arg(args.repo)
    extraction = extract_tree(repo, args.ref)
    try:
        target = extraction.target(args.path)
        sys.stdout.write(target.read_text(errors="replace"))
    finally:
        extraction.cleanup()
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
    run.add_argument("path", help="<file> <ref> or <ref>:<file>")
    run.add_argument("ref", nargs="?", help="commit-ish: sha, tag, branch, @HEAD~3")
    run.add_argument("file_args", nargs="*", help="args passed to the file")
    run.add_argument("--timeout", type=float, default=120.0)
    run.add_argument("--keep", action="store_true", help="keep the temp extraction dir")
    run.set_defaults(func=cmd_run)

    bs = sub.add_parser("bisect", help="find the commit that changed behavior")
    bs.add_argument("path", help="the file to run at each commit")
    bs.add_argument("--good", required=True, help="known-good ref")
    bs.add_argument("--bad", default="HEAD", help="known-bad ref (default HEAD)")
    bs.add_argument("--expect", help="stdout must contain this string to count as OK")
    bs.add_argument("file_args", nargs="*", help="args passed to the file")
    bs.set_defaults(func=cmd_bisect)

    sh = sub.add_parser("show", help="print a historical file without running it")
    sh.add_argument("path", help="the file to print")
    sh.add_argument("ref", help="commit-ish")
    sh.set_defaults(func=cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
