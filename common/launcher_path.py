"""The file a configured launcher actually runs.

On macOS what a person picks is `VPinballX.app`, which is a directory - so anything
asking whether a setting names a program has to walk into the bundle first, or it
reports the one right answer as a mistake.

Its own module because two things need it and they are in different layers: the launcher
resolves it to run something, and the settings check resolves it to say whether the path
is good. A copy in each is one drifting away from what actually launches.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_launcher_path(configured_value: str) -> Path:
    """The executable a configured launcher path points at."""
    launcher_path = Path(str(configured_value or "").strip()).expanduser()
    if sys.platform == "darwin" and launcher_path.suffix.lower() == ".app":
        launcher_path = launcher_path / "Contents" / "MacOS" / launcher_path.stem
    return launcher_path
