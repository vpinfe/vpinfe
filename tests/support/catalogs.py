"""Every owner's catalog as one, keyed the way the app serves it."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def served(name: str = "en") -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("i18n_script", ROOT / "scripts" / "i18n.py")
    assert spec is not None and spec.loader is not None, "scripts/i18n.py is gone"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.merged(name))
