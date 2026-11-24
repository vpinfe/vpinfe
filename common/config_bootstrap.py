"""Reading `--configdir` before anything is imported. See below for why it is alone."""

from __future__ import annotations

import os
from collections.abc import MutableMapping, Sequence


def apply_configdir_override(
    argv: Sequence[str],
    env: MutableMapping[str, str] | None = None,
) -> str | None:
    """Map a --configdir CLI flag onto the VPINFE_CONFIG_DIR environment variable.

    common.config.paths resolves CONFIG_DIR at import time, so the config location has
    to be in the environment before anything under common/ is imported. argparse
    runs too late for that, so main.py peeks at argv and calls this first.

    An explicit VPINFE_CONFIG_DIR already in the environment wins over the flag,
    matching how env overrides usually beat CLI defaults. Accepts both
    "--configdir DIR" and "--configdir=DIR". Returns the directory that was
    applied, or None if nothing changed.
    """
    if env is None:
        env = os.environ

    if env.get("VPINFE_CONFIG_DIR", "").strip():
        return None

    value: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--configdir" and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith("--configdir="):
            value = arg.split("=", 1)[1]

    if value is not None and value.strip():
        env["VPINFE_CONFIG_DIR"] = value
        return value
    return None
