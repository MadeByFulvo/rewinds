# Actually run a file we pulled out of history.
#
# The file gets executed inside the temp tree it was extracted to, so
# relative paths, sibling imports, and config files all resolve the way
# they did back in that commit. That's what makes running old code
# meaningful instead of just feasible.

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .extract import Extraction


# The outcome of one run, whether it worked or not.
# stderr is kept separate so callers can decide how noisy to be.
@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str

    # Handy for bisect and quick scripts alike.
    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# Which command runs which kind of file.
# Supporting something new is a one-line change here.
_INTERPRETERS = {
    ".py": [sys.executable],
    ".sh": ["sh"],
    ".bash": ["bash"],
}


class NoInterpreterError(RuntimeError):
    # The file exists but we have no idea how to execute it.
    pass


def interpreter_for(path: Path) -> list[str] | None:
    ext = path.suffix.lower()
    if ext in _INTERPRETERS:
        return list(_INTERPRETERS[ext])
    return None


# Run an extracted file, in its own little world.
#
# A few deliberate choices to keep runs predictable:
#   PYTHONPATH is dropped, so you never accidentally import your own
#   installed packages into an old snapshot. The hash seed is fixed so
#   dict iteration order can't change results between runs. And a
#   timeout is always applied, because historical scripts sometimes
#   hang and nobody enjoys killing zombie processes.
#
#   The interpreter argument lets a caller substitute a different one --
#   venv.py uses this to run files inside an ephemeral environment.
def run_file(
    extraction: Extraction,
    rel_path: str,
    args: list[str] | None = None,
    cwd_subdir: str | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: float | None = 120.0,
    interpreter: list[str] | None = None,
) -> RunResult:
    target = extraction.target(rel_path)
    interp = interpreter if interpreter is not None else interpreter_for(target)
    if interp is None:
        raise NoInterpreterError(
            f"don't know how to run {target.name!r} "
            f"(supported: {', '.join(sorted(_INTERPRETERS))})"
        )
    cmd = list(interp) + [str(target)] + list(args or [])

    cwd = extraction.root
    if cwd_subdir:
        sub = extraction.root / cwd_subdir
        if sub.is_dir():
            cwd = sub

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONHASHSEED"] = "0"
    if env_extra:
        env.update(env_extra)

    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return RunResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
