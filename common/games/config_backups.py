"""Which of an app's settings files a copy was taken of.

Only the app knows which files its settings are in, so it names them and this matches a
copy to one by the name it was taken under.
"""

from __future__ import annotations

from pathlib import Path

from common.games.info_migration import copied_from


def target_for(source: Path, files: dict[str, str]) -> Path | None:
    """Which of the app's files this copy came from, by the name it was taken under."""
    name = copied_from(source).name
    return next((Path(path).expanduser() for path in files.values()
                 if Path(path).expanduser().name == name), None)
