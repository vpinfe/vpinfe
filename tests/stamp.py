"""Which tree the invariants last passed on, for the pre-commit hook to read.

The hook runs the invariants on the staged tree. A run of `python -m tests` that covered the
invariants and passed has already checked the same files, so it leaves the hash of the working
tree in the git directory, and the hook skips its own run when the staged tree has that hash.
Any other tree has no stamp and is checked as before.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from tests import affected

STAMP = "invariants-passed"


def _git(repo: Path, *args: str, index: Path | None = None) -> str:
    env = affected.git_env()
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         check=True, env=env)
    return run.stdout.strip()


def git_dir(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--absolute-git-dir"))


def tree_hash(repo: Path) -> str:
    """The tree `git add -A` would commit: tracked, untracked and not ignored.

    Built in a copy of the index, so the real one is not touched.  The copy keeps the index's
    mtime: git re-reads a file whose mtime is not older than the index's, so a newer copy hides
    a same-size edit made in the same clock tick as the staging.
    """
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        real = git_dir(repo) / "index"
        if real.exists():
            shutil.copy2(real, index)
        _git(repo, "add", "-A", index=index)
        return _git(repo, "write-tree", index=index)


def record(repo: Path, passed_on: str) -> None:
    (git_dir(repo) / STAMP).write_text(passed_on + "\n", encoding="utf-8")
