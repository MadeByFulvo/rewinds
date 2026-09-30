# Pull old versions of files out of git and into a temp dir.
#
# The whole point of this module: read any commit you like, and never
# write a single byte into the working tree. Everything gets unpacked
# somewhere outside the repo, and whoever created it cleans it up.

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    # Any git command going wrong ends up here.
    pass


# Thin wrapper so the rest of the code never thinks about subprocess.
def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


# Take whatever the user typed and turn it into a sha git can work with.
# Tags, branches, short shas, HEAD~3, all fine. The @ is optional and
# just there to make refs easy to spot in the CLI.
#
# When git doesn't recognize the ref but it has the shape of a time
# ref (2026-06-01, 2026-06, 2026, last-month, ...), it is resolved
# through refs.py to "the newest commit at or before that instant".
# Real git refs always win: a tag named 2026 is a tag first.
def resolve_ref(repo: Path, ref: str) -> str:
    ref = ref.lstrip("@")
    try:
        return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    except GitError:
        pass

    from .refs import DateRefError, looks_like_time_ref, resolve_time_ref

    if looks_like_time_ref(ref):
        try:
            sha, warning = resolve_time_ref(repo, ref)
        except DateRefError as e:
            raise GitError(str(e)) from e
        if warning:
            # Surfaced as a warning attribute on the exception-free path:
            # callers print it when they have a stderr to print to.
            _LAST_TIME_WARNING.append(warning)
        return sha

    raise GitError(f"unknown ref {ref!r}")


# Warnings produced by the most recent resolve_ref call. Consumed (and
# cleared) by the CLI so time-ref fallbacks aren't silent.
_LAST_TIME_WARNING: list[str] = []


def pop_time_warning() -> str | None:
    if _LAST_TIME_WARNING:
        return _LAST_TIME_WARNING.pop()
    return None


# Simple existence check before we go digging through a whole tree.
def file_exists(repo: Path, ref: str, path: str) -> bool:
    ref = ref.lstrip("@")
    out = _git(repo, "ls-tree", "--name-only", "-r", ref, "--", path).strip()
    return bool(out)


# One commit's files, living in a temp dir somewhere safe.
# The usual life cycle: make one with extract_tree, read what you need
# through target(), then call cleanup(). If you forget, no harm done to
# your repo, since this dir was never part of it in the first place.
@dataclass
class Extraction:
    repo: Path
    sha: str
    ref: str  # the ref exactly as the user typed it
    root: Path  # where the files ended up

    # Where a given file lives inside the extraction.
    # Fails with a helpful message if that file wasn't in this commit.
    def target(self, rel_path: str) -> Path:
        p = self.root / rel_path
        if not p.exists():
            raise GitError(f"{rel_path!r} not found at {self.ref} ({self.sha[:8]})")
        return p

    # Deletes the temp dir. Calling it twice is fine.
    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# Unpack an entire commit into a fresh temp dir.
#
# We use git archive rather than checking anything out, because archive
# is fast, works on bare repos, and physically cannot touch the working
# tree. That guarantee is the reason this function is safe to call while
# other things are running in the repo.
def extract_tree(repo: Path, ref: str) -> Extraction:
    sha = resolve_ref(repo, ref)
    root = Path(tempfile.mkdtemp(prefix="rewinds-"))
    _git(repo, "archive", "--format=tar", sha, f"--output={root / 'tree.tar'}")
    proc = subprocess.run(
        ["tar", "-xf", str(root / "tree.tar"), "-C", str(root)],
        capture_output=True,
        text=True,
    )
    (root / "tree.tar").unlink(missing_ok=True)
    if proc.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise GitError(f"tar extraction failed:\n{proc.stderr}")
    return Extraction(repo=repo, sha=sha, ref=ref, root=root)


# Grab one specific file from one specific commit.
# Currently this just extracts the whole tree underneath; the check at
# the top at least fails fast when the file never existed at that ref.
def extract_file(repo: Path, ref: str, path: str) -> Extraction:
    if not file_exists(repo, ref, path):
        raise GitError(f"{path!r} does not exist at {ref}")
    return extract_tree(repo, ref)
