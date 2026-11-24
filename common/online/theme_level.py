"""Which contract an installed theme was written against.

Here rather than in the frontend because the installer and the theme service need the answer
on installs that have no frontend. `frontend/theme_contract.py` serves the contract; this
only names it.
"""

from __future__ import annotations

import json
from pathlib import Path

from common.values import newer_version, parse_version

CURRENT_CONTRACT = 2
OLDEST_CONTRACT = 1
MIN_VERSION_KEY = "min_vpinfe"

# The VPinFE version each contract arrived in, newest first. A theme states the oldest
# build it runs on and gets the newest contract that build already served. Contract 1
# has no entry: it is what a theme gets for saying nothing.
CONTRACT_SINCE = ((2, (3, 0)),)


def declared_contract(theme_dir: str | Path) -> int:
    """The contract a theme's declared minimum version implies, clamped to this build.

    Whether that minimum is newer than the build running is answered at install, not
    here - this is reached only once a theme is on disk and being served.
    """
    manifest = Path(theme_dir) / "manifest.json"
    try:
        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        minimum = parse_version(manifest_data.get(MIN_VERSION_KEY))
    except (OSError, ValueError, TypeError, AttributeError):
        return OLDEST_CONTRACT

    level = OLDEST_CONTRACT
    for contract, since in CONTRACT_SINCE:
        if minimum and not newer_version(since, minimum):
            level = max(level, contract)
    return min(level, CURRENT_CONTRACT)
