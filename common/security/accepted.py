"""What the Owner has accepted that VPinFE did not ship: themes and extensions.

One file, `paths.ACCEPTED_PATH`, mode 0600 and never `vpinfe.json`: a config write, a backup
restore and a hand edit all reach that file, and none of them may accept anything.

A theme is accepted by the address it came from. An extension is accepted by the access it
declared when it was accepted, so an update that asks for more stops until it is accepted
again. Anything VPinFE ships needs no entry.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Collection
from typing import Any

from common.config import paths
from common.security import machine_secrets

logger = logging.getLogger("vpinfe.common.security.accepted")

# Where a theme came from.
STOCK = "stock"
SOURCE = "source"
# A theme or an extension found in a folder, not put there by a source or by VPinFE.
IN_FOLDER = "folder"

# Where an extension came from.
SHIPPED = "shipped"
REPLACES = "replaces"
# Installed by VPinFE from the official list, files as VPinFE wrote them.
OFFICIAL = "official"
# Shipped or installed by VPinFE, and its files are no longer what VPinFE wrote.
CHANGED = "changed"

# The origins VPinFE's team stands behind, and which run with no acceptance.
OFFICIAL_ORIGINS = frozenset({SHIPPED, OFFICIAL})


def is_official(origin: str) -> bool:
    return origin in OFFICIAL_ORIGINS


_THEMES = "themes"
_EXTENSIONS = "extensions"
# Kept in the secrets file, which a person clearing the accepted list has no reason to
# touch, under the list's own path so a list somewhere else starts for itself.
_SEEDED = "accepted_seeded"

_lock = threading.RLock()


def _read() -> dict[str, Any]:
    """The file as it stands, with the themes already installed counted as accepted the
    first time there is no file. A file that cannot be read, or that is gone after it was
    started, is an empty list, never a reason to seed again."""
    with _lock:
        if not paths.ACCEPTED_PATH.exists():
            if _was_seeded():
                return {_THEMES: {}, _EXTENSIONS: {}}
            return _seed()
        held = machine_secrets.read_private(paths.ACCEPTED_PATH)
        for part in (_THEMES, _EXTENSIONS):
            if not isinstance(held.get(part), dict):
                held[part] = {}
        return held


def _seed() -> dict[str, Any]:
    """Every theme folder present counts as accepted, with no address: the install being
    upgraded chose them. Extensions are new in 3.0, so there is nothing to carry over."""
    themes: dict[str, Any] = {one: {"source": None} for one in theme_folders()}
    seeded = {_THEMES: themes, _EXTENSIONS: {}}
    _write(seeded)
    machine_secrets.put(_SEEDED, str(paths.ACCEPTED_PATH))
    logger.info("Accepted list started with %d theme(s) already installed", len(themes))
    return seeded


def _was_seeded() -> bool:
    return machine_secrets.read().get(_SEEDED) == str(paths.ACCEPTED_PATH)


def theme_folders() -> list[str]:
    """The themes in the themes folder, by folder name."""
    root = paths.THEMES_DIR
    if not root.is_dir():
        return []
    return [one.name for one in sorted(root.iterdir())
            if one.is_dir() and not one.name.startswith(".")
            and not one.name.endswith(paths.ASIDE_SUFFIX)]


def unaccepted_themes() -> list[str]:
    """The installed themes that are neither on the list nor the default: stopped. Reads
    folders only, so it is cheap to ask on every draw."""
    return [one for one in theme_folders() if not theme_runs(one)]


def _write(held: dict[str, Any]) -> None:
    paths.ensure_config_dir()
    machine_secrets.write_private(paths.ACCEPTED_PATH, held)


def _same_address(kept: str | None, address: str) -> bool:
    """A kept address of None is a theme that was here before the list. It runs from its
    folder, and any address it is offered from has to be accepted once, which keeps it."""
    return kept == address or (kept is None and not address)


# -- themes -------------------------------------------------------------------


def theme_origin(*, stock: bool, in_folder: bool = False) -> str:
    """STOCK for a theme from the VPinFE Theme List's address, IN_FOLDER for one no source
    accounts for, else SOURCE. The address decides, never the theme's name; `stock` is the
    caller's answer to whether the address is the VPinFE Theme List."""
    if in_folder:
        return IN_FOLDER
    return STOCK if stock else SOURCE


def theme_accepted(key: str, address: str, *, stock: bool, in_folder: bool = False) -> bool:
    """Whether this theme, from this address, may be installed and run.

    `address` is the source it comes from, "" for a theme found in the themes folder, and
    `stock` whether that address is the VPinFE Theme List. One from the VPinFE Theme List
    needs no entry. One accepted from another address is not accepted from this one."""
    if theme_origin(stock=stock, in_folder=in_folder) == STOCK:
        return True
    found = _read()[_THEMES].get(key)
    return isinstance(found, dict) and _same_address(found.get("source"), address)


def theme_runs(key: str) -> bool:
    """Whether the frontend may play this installed theme. Asked at start, where the
    sources are not known, so it asks only whether the theme was ever accepted or
    installed from the VPinFE Theme List."""
    return key == default_theme() or isinstance(_read()[_THEMES].get(key), dict)


def default_theme() -> str:
    """The theme a theme that is not accepted gives way to."""
    from common.config import config_schema

    declared = config_schema.option("themes", "active")
    return str(declared.default) if declared else ""


def accept_theme(key: str, address: str) -> None:
    """Accept this theme from `address`, "" for one found in the themes folder."""
    with _lock:
        held = _read()
        held[_THEMES][key] = {"source": address}
        _write(held)


def note_theme_installed(key: str, address: str, *, stock: bool) -> None:
    """Record a theme VPinFE installed from the VPinFE Theme List, so its provenance
    survives the list changing or being unreachable. Any other address is accepted
    first and recorded by `accept_theme`."""
    if theme_origin(stock=stock) == STOCK:
        accept_theme(key, address)


def forget_theme(key: str) -> None:
    """A theme that is removed is accepted again only by being accepted again."""
    with _lock:
        held = _read()
        if held[_THEMES].pop(key, None) is not None:
            _write(held)


# -- extensions ---------------------------------------------------------------


def extension_accepted(name: str, origin: str, scopes: Collection[str],
                       capabilities: Collection[str], folder: str | None = None,
                       play: Collection[str] = (), dependencies: Collection[str] = ()) -> bool:
    """Whether this extension may run: shipped with VPinFE, or accepted from where it is
    now with at least the access it declares now. `folder` is the folder it is in, and
    an acceptance given for another folder does not cover it; None leaves the folder out."""
    if is_official(origin):
        return True
    return (not new_access(name, origin, scopes, capabilities, folder, play, dependencies)
            and _kept(name, origin, folder) is not None)


def _kept(name: str, origin: str, folder: str | None = None) -> dict[str, Any] | None:
    found = _read()[_EXTENSIONS].get(name)
    if not isinstance(found, dict) or found.get("origin") != origin:
        return None
    return found if folder is None or found.get("folder") == folder else None


def accepted_before(name: str, origin: str, folder: str | None = None) -> bool:
    """Whether there is an acceptance for this extension, whatever it covered."""
    return _kept(name, origin, folder) is not None


def accepted_version(name: str, origin: str, folder: str | None = None) -> str:
    """The version the Owner last accepted, empty when none was."""
    found = _kept(name, origin, folder) or {}
    return str(found.get("version") or "")


def new_access(name: str, origin: str, scopes: Collection[str],
               capabilities: Collection[str], folder: str | None = None,
               play: Collection[str] = (), dependencies: Collection[str] = ()) -> list[str]:
    """What the extension declares that its acceptance did not cover: all of it when it
    was never accepted. A surface a Remote can open is `play:<surface>`, and a dependency is
    `dependency:<name>==<version>`; one more than the acceptance listed is new, so an update
    cannot add either unasked."""
    found = _kept(name, origin, folder) or {}
    covered = {*(found.get("scopes") or ()), *(found.get("capabilities") or ()),
               *(f"play:{one}" for one in found.get("play") or ()),
               *(f"dependency:{one}" for one in found.get("dependencies") or ())}
    return [one for one in (*scopes, *capabilities, *(f"play:{one}" for one in play),
                            *(f"dependency:{one}" for one in dependencies))
            if one not in covered]


def accept_extension(name: str, origin: str, *, folder: str, version: str,
                     scopes: Collection[str], capabilities: Collection[str],
                     play: Collection[str] = (), dependencies: Collection[str] = ()) -> None:
    """Accept this extension as it declares itself now. Replaces what an earlier
    acceptance covered, so the list holds what the Owner last saw."""
    with _lock:
        held = _read()
        held[_EXTENSIONS][name] = {"origin": origin, "folder": folder, "version": version,
                                   "scopes": sorted(scopes),
                                   "capabilities": sorted(capabilities),
                                   "play": sorted(play), "dependencies": sorted(dependencies)}
        _write(held)
