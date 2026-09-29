# Contributing to rewinds

Thank you for considering a contribution. This document covers what you
need to know to get started.

## Development setup

Requirements: Python 3.9 or later, `git`, and `tar`.

```bash
git clone https://github.com/MadeByFulvo/rewinds.git
cd rewinds
pip install -e .
pip install pytest
```

## Running the tests

```bash
pytest tests/ -q
```

The test suite builds real git repositories in temporary directories and
runs against real git behavior. `git` and `tar` must be available on the
system.

## Project structure

| Path | Responsibility |
|------|----------------|
| `rewinds/extract.py` | Ref resolution and extraction. Enforces the read-only guarantee. |
| `rewinds/runner.py` | Subprocess execution and reproducibility controls. |
| `rewinds/bisect.py` | Commit-range traversal and behavior comparison. |
| `rewinds/cli.py` | Argument parsing, output streaming, exit codes. |

The mapping of file extensions to interpreters lives in
`rewinds/runner.py`. Adding support for a new file type is a one-line
change there, plus a test.

## Guidelines

- Keep the package dependency-free. If a change requires a dependency,
  discuss it in an issue first.
- Preserve the read-only guarantee: no command that writes to the user's
  working tree may be introduced.
- Add or update tests for any behavior change.
- Match the existing comment style: plain `#` comments, no docstrings.
- Ensure `pytest tests/ -q` passes before submitting.

## Submitting changes

1. Open an issue describing the change before large refactors.
2. Create a branch for your work.
3. Commit with focused, descriptive messages.
4. Open a pull request referencing the issue.

## Reporting bugs

Include the command you ran, the expected behavior, the actual behavior,
your Python version, and the output of `git --version`. If the problem
involves a specific repository state, describe the commit range or attach
a minimal reproduction.
