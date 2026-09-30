# Changelog

All notable changes to this project are documented in this file.
The format is based on Keep a Changelog, and this project adheres
to Semantic Versioning.

## 0.2.0 - 2026-09-30

### Added
- Time-based refs: `@2026-06-01`, `@2026-06`, `@2026`, `@last-month`,
  `@last-week`, `@yesterday` resolve to the newest commit at or before
  that instant. Works with `run`, `show`, and `bisect`. Tags take
  precedence over dates; pre-history requests fall back to the earliest
  commit with a warning.
- Refs are optional on `run` and `show`: omitting the ref shows a picker
  of the file's recent versions (interactive), or the same list with a
  re-run hint when stdin is not a tty — CI never hangs.
- `rewinds log <file>` lists the commits that touched a file, newest
  first.
- Markdown-styled output: bold labels, backticked refs, and blockquotes
  for errors and hints; all diagnostics on stderr, stdout pipe-clean.
- Bisect steps now reflect `--expect` matching in their status, not
  just the child's exit code.
- Ephemeral environments: when a commit declares dependencies
  (`requirements.txt`), `rewinds run` builds a throwaway virtual
  environment from that historical file and executes the script with
  its interpreter. Old code now runs against its old dependencies.
- `rewinds bisect` runs every commit in the range inside its own
  ephemeral environment, so a dependency bump that changed behavior is
  found even when today's packages could never produce the old output.
  Environment build failures fall back to the current interpreter and
  are marked in the report instead of counting as behavior changes.
- `--no-venv` flag on `run` and `bisect` to skip ephemeral environments.
- `--keep-going` flag on `run` to fall back to the current interpreter
  when environment setup fails.
- `rewinds.venv` module: `ensure_venv(extraction)`, `EphemeralVenv`,
  `find_env_file`, `VenvError` for library users.
- `run_file(..., interpreter=[...])` parameter to substitute an
  interpreter.

### Changed
- Friendlier error message when `rewinds run` is invoked without a ref
  or file.

## 0.1.1 - 2026-09-29

### Changed
- License switched from MIT to Apache 2.0

## 0.1.0 - 2026-09-29

### Added
- `rewinds run <file> <ref>`: execute a file as it existed at any commit
- `rewinds show <file> <ref>`: print a historical file without running it
- `rewinds bisect <file> --good <ref> --expect <str>`: find the first
  commit that changed a script's behavior
- Read-only extraction via `git archive` into temporary directories
- Reproducible subprocess execution (pinned hash seed, no PYTHONPATH leak)
- CI-friendly exit codes
