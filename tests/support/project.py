"""The files that make up the project, as git lists them rather than as the disk holds them."""

from __future__ import annotations

import subprocess
import unittest
from fnmatch import fnmatch
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@cache
def _listed() -> tuple[str, ...]:
    if not (REPO / ".git").exists():
        raise unittest.SkipTest("not a git checkout")
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO, check=True, capture_output=True, text=True).stdout
    return tuple(filter(None, listed.split("\0")))


def project_files(*patterns: str) -> list[Path]:
    """Every file git tracks or would track, whose name matches one of `patterns`, sorted.

    Raises SkipTest outside a git checkout.
    """
    return sorted(REPO / rel for rel in _listed()
                  if any(fnmatch(rel.rsplit("/", 1)[-1], pattern) for pattern in patterns)
                  # --cached still lists a file the working tree has deleted, and a
                  # submodule as one entry that is not a file.
                  and (REPO / rel).is_file())
