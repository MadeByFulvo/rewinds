# Time-based refs: resolve "@2026-06-01" to "the last commit before then".
#
# This is the module that lets users think in dates instead of shas:
#     rewinds run calc.py @2026-06-01
#     rewinds show config.yml @last-month
#     rewinds run calc.py @2025
#
# Semantics, chosen to match what "as it was on that date" means:
#   - a date ref resolves to the newest commit whose *committer* date is
#     on or before the target instant (23:59:59 local for a bare date)
#   - rebases and cherry-picks rewrite dates, so committer date (always
#     preserved on rebase) is used rather than author date
#   - if every commit in the repo is newer than the target, the earliest
#     commit is returned with a warning rather than failing -- "the repo
#     didn't exist yet, here's the oldest thing there is" is friendlier
#     than a dead end
#
# Resolution happens through `git rev-list`, so bare repositories,
# shallow clones, and every ref shape git knows keep working.

from __future__ import annotations

import calendar
import datetime as _dt
import subprocess
from pathlib import Path

from .extract import GitError


class DateRefError(GitError):
    # A time ref looked plausible but couldn't be resolved.
    pass


# Forms accepted after the leading '@'.
#   YYYY-MM-DD      a specific day (resolved to its end, local time)
#   YYYY-MM         a whole month (resolved to the month's end)
#   YYYY            a whole year (resolved to the year's end)
#   last-month      the end of the previous calendar month
#   last-week       seven days before now
#   yesterday       twenty-four hours before now
RELATIVE_WORDS = ("last-month", "last-week", "yesterday")


def looks_like_time_ref(text: str) -> bool:
    """True when text has the shape of a time ref (@ already stripped).

    Used to decide whether a ref git doesn't recognize should be tried
    as a date. Explicit relative words always count.
    """
    if text in RELATIVE_WORDS:
        return True
    parts = text.split("-")
    if not 1 <= len(parts) <= 3:
        return False
    if not all(p.isdigit() for p in parts):
        return False
    if len(parts) == 1:
        return len(parts[0]) == 4
    return len(parts[0]) == 4 and len(parts[1]) == 2 and (
        len(parts) == 2 or len(parts[2]) == 2
    )


def _resolve_relative(word: str, now: _dt.datetime) -> _dt.datetime:
    if word == "yesterday":
        return now - _dt.timedelta(days=1)
    if word == "last-week":
        return now - _dt.timedelta(weeks=1)
    if word == "last-month":
        first_of_this_month = now.replace(day=1)
        end_of_prev = first_of_this_month - _dt.timedelta(days=1)
        return end_of_prev.replace(hour=23, minute=59, second=59, microsecond=0)
    raise DateRefError(f"unknown time ref `{word}`")


def _parse_time_ref(text: str, now: _dt.datetime) -> _dt.datetime:
    parts = text.split("-")
    try:
        if text in RELATIVE_WORDS:
            return _resolve_relative(text, now)
        if len(parts) == 1:  # YYYY
            year = int(parts[0])
            return _dt.datetime(year, 12, 31, 23, 59, 59)
        if len(parts) == 2:  # YYYY-MM
            year, month = int(parts[0]), int(parts[1])
            last_day = calendar.monthrange(year, month)[1]
            return _dt.datetime(year, month, last_day, 23, 59, 59)
        if len(parts) == 3:  # YYYY-MM-DD
            year, month, day = (int(p) for p in parts)
            return _dt.datetime(year, month, day, 23, 59, 59)
    except ValueError:
        pass
    raise DateRefError(
        f"can't parse time ref `{text}`",
        "accepted: `@YYYY-MM-DD`, `@YYYY-MM`, `@YYYY`, `@last-month`, "
        "`@last-week`, `@yesterday`",
    )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


# Resolve a time ref to the newest commit at or before the target
# instant. Returns the sha and, when we had to fall back to the earliest
# commit, a warning message for the caller to show.
def resolve_time_ref(repo: Path, text: str) -> tuple[str, str | None]:
    target = _parse_time_ref(text, _dt.datetime.now())
    stamp = target.strftime("%Y-%m-%d %H:%M:%S %z")

    # --before includes commits stamped exactly at the boundary, which
    # is what "as it was on 2026-06-01" should mean.
    out = _git(
        repo,
        "rev-list",
        f"--before={stamp}",
        "--date-order",
        "-n", "1",
        "HEAD",
    ).strip()

    warning: str | None = None
    if not out:
        # Nothing existed yet at that point; return the earliest commit
        # so the user still gets a usable result.
        first = _git(repo, "rev-list", "--max-parents=0", "HEAD").split()
        if not first:
            raise DateRefError(f"repository has no commits; `{text}` resolves to nothing")
        warning = (
            f"no commits existed before `{text}`; using the earliest commit "
            f"`{first[0][:8]}`"
        )
        return first[0], warning

    return out, warning
