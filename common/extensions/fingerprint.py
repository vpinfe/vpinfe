"""A fingerprint of an extension's files, and what VPinFE kept of the ones it installed.

Everything here is local: a receipt is written when VPinFE installs an extension, the build
carries the same list for the extensions it ships, and checking one is hashing the files on
this device. Nothing needs a connection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from common import atomic_write
from common.config import paths
from common.security import machine_secrets

from .contract import MANIFEST_NAME, ManifestError, read_manifest

logger = logging.getLogger("vpinfe.common.extensions.fingerprint")

# The build's list of the extensions it ships, beside them. A source checkout has none.
BUILD_LIST = "fingerprints.json"

# What Python and the operating system write into a folder on their own. Hashing them
# would make an extension "changed" by being run or browsed.
_IGNORED_DIRS = frozenset({"__pycache__"})
_IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
_UNREADABLE = "unreadable"

MATCHES = "matches"
CHANGED = "changed"


def hash_files(directory: Path | str) -> dict[str, str]:
    """`{path: sha256}` for every file under `directory`, paths relative and with `/`.

    A link is an entry of its own, named by where it points: Python imports through one,
    so adding one is a change. A `.pyc` outside `__pycache__` is hashed because it can be
    imported without its source.
    """
    root = Path(directory)
    found: dict[str, str] = {}
    for current, folders, names in os.walk(root):
        folders[:] = sorted(one for one in folders if one not in _IGNORED_DIRS)
        linked = [one for one in folders if (Path(current) / one).is_symlink()]
        for name in sorted([*names, *linked]):
            if name in _IGNORED_NAMES:
                continue
            path = Path(current) / name
            if path.is_symlink():
                digest = f"link:{os.readlink(path)}"
            else:
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    digest = _UNREADABLE
            found[path.relative_to(root).as_posix()] = digest
    return found


def digest(files: Mapping[str, str]) -> str:
    """One hash for the whole set, the same for the same files wherever they are."""
    whole = hashlib.sha256()
    for path in sorted(files):
        whole.update(f"{path}\0{files[path]}\n".encode())
    return whole.hexdigest()


def differences(expected: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    """The paths that are not as `expected` has them: added, removed or edited."""
    return sorted(one for one in {*expected, *actual} if expected.get(one) != actual.get(one))


@dataclass(frozen=True)
class Receipt:
    """What an extension's files were when VPinFE put them there."""

    repository: str
    release: str
    files: Mapping[str, str]

    @property
    def fingerprint(self) -> str:
        return digest(self.files)

    def as_dict(self) -> dict[str, object]:
        return {"repository": self.repository, "release": self.release, "files": dict(self.files)}


def _receipt_from(held: object) -> Receipt | None:
    if not isinstance(held, dict) or not isinstance(held.get("files"), dict):
        return None
    files = {str(path): str(found) for path, found in held["files"].items()}
    return Receipt(str(held.get("repository") or ""), str(held.get("release") or ""), files)


# -- receipts for what VPinFE installs ---------------------------------------


def receipt(name: str) -> Receipt | None:
    """The receipt for an extension VPinFE installed, None when it kept none."""
    return _receipt_from(machine_secrets.read_private(paths.EXTENSION_RECEIPTS_PATH).get(name))


def write_receipt(name: str, directory: Path | str, *, repository: str, release: str) -> Receipt:
    """Keep a receipt for what is in `directory` now. Call it once the files are all there."""
    made = Receipt(repository, release, hash_files(directory))
    held = machine_secrets.read_private(paths.EXTENSION_RECEIPTS_PATH)
    held[name] = made.as_dict()
    paths.EXTENSION_RECEIPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    machine_secrets.write_private(paths.EXTENSION_RECEIPTS_PATH, held)
    return made


def forget_receipt(name: str) -> None:
    held = machine_secrets.read_private(paths.EXTENSION_RECEIPTS_PATH)
    if held.pop(name, None) is not None:
        machine_secrets.write_private(paths.EXTENSION_RECEIPTS_PATH, held)


# -- the build's list for what it ships ---------------------------------------


def read_build_list(root: Path | str) -> dict[str, Receipt] | None:
    """The receipts the build made for the extensions under `root`. None when there is no
    list, which is a source checkout and not a build: nothing there to check against."""
    try:
        held = json.loads((Path(root) / BUILD_LIST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if getattr(sys, "frozen", False):
            logger.error("The build's extension fingerprints are missing from %s", root)
            return {}
        return None
    except (OSError, ValueError):
        logger.error("The build's extension fingerprints in %s cannot be read", root)
        return {}
    if not isinstance(held, dict):
        return {}
    return {name: made for name, one in held.items() if (made := _receipt_from(one))}


def write_build_list(root: Path | str, target: Path | str | None = None) -> Path:
    """Write the list for every extension under `root`, to `target` or beside them."""
    root = Path(root)
    target = Path(target) if target else root / BUILD_LIST
    held = {}
    for directory in sorted(one for one in root.iterdir() if one.is_dir()):
        if not (directory / MANIFEST_NAME).is_file():
            continue
        try:
            manifest = read_manifest(directory)
        except ManifestError:
            logger.warning("Not fingerprinting %s: its manifest cannot be read", directory.name)
            continue
        held[directory.name] = Receipt(
            manifest.source, manifest.version, hash_files(directory)
        ).as_dict()
    target.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write.staged_for(target) as staged:
        text = json.dumps(held, indent=2, sort_keys=True) + "\n"
        staged.write_text(text, encoding="utf-8")
    return target


# -- the official names ------------------------------------------------------


def listed_names(build_names: Iterable[str]) -> frozenset[str]:
    """The names the official list holds: the ones the build ships, and the ones in the list
    as it was last fetched. A device that never connects still has the first."""
    return frozenset(set(build_names) | (held_names() or frozenset()))


def held_names() -> frozenset[str] | None:
    """The names in the list as it was last fetched, or None when it never was (or the copy
    kept cannot be read): an absent list says nothing about what is on it."""
    try:
        held = json.loads(paths.EXTENSION_LIST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = held.get("extensions") if isinstance(held, dict) else None
    if not isinstance(entries, list):
        return None
    return frozenset(
        one["name"] for one in entries if isinstance(one, dict) and isinstance(one.get("name"), str)
    )
