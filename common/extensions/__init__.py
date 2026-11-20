"""The extension host: what an install has loaded, and what each one may reach.

An extension is a directory holding `extension.json` beside a Python package whose
`register(ctx)` core calls once at startup. `common/extensions/contract.py` is the whole
of what an implementation imports; the context it is handed is the whole of its reach.

Two `extensions` names, the way the apps do it: this one is core's host, and the
extensions themselves live in their own directories under the config dir.

Loading is explicit and happens once, before the API is built - the API mounts what the
registry already holds, so a test drives a fixture root instead of whatever the machine
running it happens to have installed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# Imported for the seed it registers: it must exist before a 2.x config is converted.
from . import handover  # noqa: F401
from .context import LOG_ROOT, ExtensionContext, logger_for
from .contract import (
    PLATFORM_ABI,
    ContractError,
    Manifest,
    ManifestError,
    parse,
    read_manifest,
)
from .host import (
    BUNDLED_DIR,
    DISABLED,
    FAILED,
    INSTALLED_DIR,
    LOADED,
    OFF,
    Record,
    Registry,
)
from .store import ExtensionStore, get_extension_store

__all__ = [
    "BUNDLED_DIR", "DISABLED", "FAILED", "INSTALLED_DIR", "LOADED", "LOG_ROOT", "OFF",
    "PLATFORM_ABI",
    "ContractError", "ExtensionContext", "ExtensionStore", "Manifest", "ManifestError",
    "Record", "Registry", "accept", "clear", "disable", "get_extension_store", "granted_scopes",
    "load_from", "load_installed", "logger_for", "mounted", "parse", "read_manifest",
    "read_roots", "records", "refuse", "registry", "running", "served", "set_registry",
    "switch", "unofficial_running", "inventory",
]

logger = logging.getLogger("vpinfe.common.extensions")

_registry: Registry | None = None


def registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry


def set_registry(replacement: Registry) -> None:
    """Swap the registry. For tests, and for whenever an install loads from elsewhere."""
    global _registry
    _registry = replacement


def load_installed() -> list[Record]:
    return registry().load_installed()


def load_from(root: Path | str) -> list[Record]:
    return registry().load_from(root)


def records() -> list[Record]:
    return registry().records()


def running(name: str) -> bool:
    return registry().running(name)


def unofficial_running() -> list[Record]:
    return registry().unofficial_running()


def inventory() -> list[Record]:
    return registry().inventory()


def granted_scopes() -> frozenset[str]:
    return registry().granted_scopes()


def mounted() -> list[tuple[Record, Any, str]]:
    """Each mounted extension, its router, and the scope its routes are gated on.

    The router is Any rather than a named type: common/ does not know the web
    framework, and `object` would be a narrower claim than the host makes - it cannot
    be mounted without the caller casting it back.
    """
    return registry().mounted()


def served() -> list[Record]:
    """Each extension core answers for on `/ext/<name>`, whatever state it is in."""
    return registry().served()


def read_roots() -> tuple[str, ...]:
    return registry().read_roots()


def switch(name: str, on: bool) -> Record | None:
    return registry().switch(name, on)


def switch_copy(name: str, on: bool) -> Record | None:
    return registry().switch_copy(name, on)


def accept(name: str) -> Record | None:
    return registry().accept(name)


def files_replaced(name: str) -> Record | None:
    return registry().files_replaced(name)


def disable(name: str, why: str, **values: str) -> None:
    registry().disable(name, why, **values)


def refuse(name: str, why: str, **values: str) -> None:
    registry().refuse(name, why, **values)


def clear() -> None:
    registry().clear()
