# Ephemeral environments: run old code against its old dependencies.
#
# A file from six months ago often needs the packages it was written
# against, not the ones installed today. The historical requirements.txt
# sits right next to the historical script inside the extraction, so we
# build a throwaway venv from it and run the file with that venv's
# interpreter. When the extraction is cleaned up, the venv disappears
# with it -- that's the "ephemeral" part. Nothing is cached, nothing
# leaks between runs, and nothing outside the temp dir is touched.
#
# Limitations worth being honest about (also in the README):
#   - installing packages needs network access
#   - this installs exactly what the historical file pins; unpinned
#     requirements resolve to *current* versions, not historical ones
#   - build-time compilation of sdists can fail on modern pythons;
#     failures surface as a VenvError with pip's output attached

from __future__ import annotations

import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

# Files that can declare a historical environment, in priority order.
ENV_FILES = ("requirements.txt", "requirements-dev.txt", "pyproject.toml")


class VenvError(RuntimeError):
    # Building or using the ephemeral venv went wrong.
    pass


# Find the dependency manifest inside an extraction, if any.
# Returns the path relative to the extraction root, so it can also be
# shown to the user ("using requirements.txt @ 3f2a19c").
def find_env_file(extraction) -> str | None:
    for name in ENV_FILES:
        candidate = extraction.root / name
        if candidate.is_file():
            return name
    return None


@dataclass
class EphemeralVenv:
    # A built venv living inside the extraction. interpret() points at
    # its python; nothing here outlives extraction.cleanup().
    root: Path

    # The interpreter to run historical files with.
    def interpreter(self) -> list[str]:
        python = self.root / "bin" / "python"
        if not python.exists():  # pragma: no cover - platform guard
            python = self.root / "Scripts" / "python.exe"
        return [str(python)]

    # Install a package set from a requirements file into this venv.
    # pip's own output is forwarded on failure so the user can see
    # which pin refused to build and why.
    def install(self, requirements: Path, timeout: float = 600.0) -> None:
        proc = subprocess.run(
            self.interpreter()
            + ["-m", "pip", "install", "-r", str(requirements), "--quiet", "--no-input"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise VenvError(
                f"pip install failed for {requirements.name}:\n{proc.stdout}{proc.stderr}"
            )


def _base_python() -> str:
    # The python that should parent new venvs. sys.executable is right
    # when rewinds itself runs under a real interpreter; the sysconfig
    # fallback covers exotic frozen/embedded launches.
    return sys.executable or sysconfig.get_config_var("BINDIR") + "/python3"


# Build a venv inside the extraction and install the historical deps.
#
# venv creation is cheap (seconds); pip install costs whatever the
# requirements cost. Failures raise VenvError with pip's output, which
# the CLI prints verbatim -- debugging a broken historical pin should
# feel like debugging pip, not like decoding a rewinds error.
def build_venv(extraction, env_file: str, timeout: float = 600.0) -> EphemeralVenv:
    target = extraction.root / ".__rewinds_venv__"
    proc = subprocess.run(
        [_base_python(), "-m", "venv", str(target)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise VenvError(f"venv creation failed:\n{proc.stdout}{proc.stderr}")

    venv = EphemeralVenv(root=target)
    venv.install(extraction.root / env_file, timeout=timeout)
    return venv


# Convenience wrapper: detect a manifest, build if present, else None.
# This is the function callers want 95% of the time.
def ensure_venv(extraction, timeout: float = 600.0) -> EphemeralVenv | None:
    env_file = find_env_file(extraction)
    if env_file is None:
        return None
    return build_venv(extraction, env_file, timeout=timeout)
