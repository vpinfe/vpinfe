"""Finding and loading the third_party libraries the app ships with.

DOF and libdmdutil are not packaged as Python dependencies - they are dropped into
`third_party/` by the build and imported from a path at runtime. `common.config.paths.bundled`
knows where that is; an environment variable overrides it for a developer working
against a local checkout of one. Nothing here is specific to DOF or libdmdutil; the
extension host will want the same primitives.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from common.config.paths import bundled


def find_named_path(base: Path, names: tuple[str, ...]) -> Path | None:
    if base.is_file() and base.name in names:
        return base
    if not base.exists() or not base.is_dir():
        return None

    for name in names:
        direct = base / name
        if direct.exists():
            return direct

    for name in names:
        hits = sorted(base.rglob(name))
        if hits:
            return hits[0]
    return None


def third_party_base_candidates(env_var: str, package_dir: str) -> list[Path]:
    """Where to look for a bundled library: an override, then where the build put it.

    Two, because the other candidates are not distinct: `sys._MEIPASS` equals `APP_ROOT`,
    and inside a .app both are `Contents/Frameworks`, which `Contents/Resources` mirrors.
    The executable's own directory is never where anything ships.
    """
    candidates: list[Path] = []

    env_override = os.environ.get(env_var, "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.append(bundled("third_party", package_dir))
    return candidates


def import_module_from_path(path: Path, module_prefix: str = "_vpinfe") -> ModuleType:
    module_name = f"{module_prefix}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load module spec from: {path}")

    module = importlib.util.module_from_spec(spec)
    module_dir = str(path.parent)
    restore_path = False
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
        restore_path = True
    try:
        spec.loader.exec_module(module)
    finally:
        if restore_path:
            try:
                sys.path.remove(module_dir)
            except ValueError:
                pass
    return module
