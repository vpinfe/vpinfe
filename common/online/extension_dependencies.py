"""Fetching the dependencies an extension lists, from pypi.org, as wheels.

A built app has no pip, so this is the whole of installing: read what the index says about
one exact version, take the wheel that runs on this device, check it against the index's
checksum, and unpack it into a folder of its own under `paths.DEPENDENCIES_DIR`. Nothing is
resolved, built or run, and nothing is fetched from anywhere but the index and its file host.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import sys
import tempfile
import zipfile
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.parser import Parser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import requests
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import Tag, sys_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

from common.config import paths
from common.extensions import dependencies, host, imports
from common.extensions.contract import ManifestError, read_manifest
from common.extensions.dependencies import Dependency
from common.failures import why
from common.i18n import t
from common.online.archive_caps import ARCHIVE_LIMIT, FILE_LIMIT, UNPACKED_LIMIT
from common.security import http_client

logger = logging.getLogger("vpinfe.common.online.extension_dependencies")

INDEX = "https://pypi.org/pypi"
FILE_HOST = "files.pythonhosted.org"
WORK_PREFIX = ".fetch-"


class DependencyError(Exception):
    """A dependency that could not be fetched, in words the person who asked can read."""


@dataclass(frozen=True)
class Wheel:
    """The file the index offers for a dependency on one system."""

    dependency: Dependency
    filename: str
    url: str
    sha256: str
    folder: str


def _get_json(url: str) -> object:
    return http_client.get_json(url)


def _get_bytes(url: str, limit: int) -> bytes:
    return http_client.get_bytes(url, limit=limit)


def choose(one: Dependency, tags: Sequence[Tag] | None = None) -> Wheel:
    """The wheel of `one` that runs on a system, best match first, as the index lists it.
    `tags` are that system's, most preferred first, and default to this device's."""
    url = f"{INDEX}/{one.name}/{one.version}/json"
    try:
        answer = _get_json(url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise DependencyError(
                t("error.extensions.dependency_not_found", dependency=str(one))) from exc
        raise DependencyError(why(exc, url)) from exc
    except requests.RequestException as exc:
        raise DependencyError(why(exc, url)) from exc
    except ValueError as exc:
        raise DependencyError(
            t("error.extensions.dependency_unreadable", dependency=str(one))) from exc
    rank = {tag: place for place, tag in enumerate(tags if tags is not None else sys_tags())}
    best: tuple[int, Wheel] | None = None
    for entry in answer.get("urls", ()) if isinstance(answer, dict) else ():
        found = _readable(one, entry, rank)
        if found is not None and (best is None or found[0] < best[0]):
            best = found
    if best is None:
        raise DependencyError(t("error.extensions.dependency_no_wheel", dependency=str(one)))
    return best[1]


def _readable(one: Dependency, entry: object, rank: dict[Tag, int]) -> tuple[int, Wheel] | None:
    """The place a listed file has among the system's tags, and the wheel it is, or None for
    a file that is not a wheel this system can run, or is not described well enough to check."""
    if not isinstance(entry, dict) or entry.get("packagetype") != "bdist_wheel":
        return None
    filename, url = entry.get("filename"), entry.get("url")
    digests = entry.get("digests")
    sha256 = digests.get("sha256") if isinstance(digests, dict) else None
    if not (isinstance(filename, str) and isinstance(url, str) and isinstance(sha256, str)):
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != FILE_HOST:
        return None
    if not _runs_on_this_python(entry.get("requires_python")):
        return None
    try:
        name, version, _build, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return None
    if canonicalize_name(name) != one.name or str(version) != one.version:
        return None
    places = [(rank[tag], tag) for tag in tags if tag in rank]
    if not places:
        return None
    place, tag = min(places, key=lambda one: one[0])
    return place, Wheel(one, filename, url, sha256.lower(), dependencies.folder_name(tag))


def _runs_on_this_python(needed: object) -> bool:
    """False where the index says a file needs a Python this one is not. A requirement it
    does not state, or states in a form that cannot be read, rules nothing out."""
    if not isinstance(needed, str) or not needed.strip():
        return True
    try:
        return SpecifierSet(needed).contains(platform.python_version(), prereleases=True)
    except InvalidSpecifier:
        return True


def download(wheel: Wheel) -> bytes:
    """The wheel's bytes, within the size an extension may be, and the index's checksum."""
    try:
        data = _get_bytes(wheel.url, ARCHIVE_LIMIT)
    except requests.RequestException as exc:
        raise DependencyError(why(exc, wheel.url)) from exc
    if hashlib.sha256(data).hexdigest() != wheel.sha256:
        raise DependencyError(
            t("error.extensions.dependency_bad_file", dependency=str(wheel.dependency)))
    return data


def _members(archive: zipfile.ZipFile, one: Dependency) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > FILE_LIMIT or sum(m.file_size for m in members) > UNPACKED_LIMIT:
        raise DependencyError(t("error.extensions.dependency_too_big", dependency=str(one)))
    return members


def top_levels(members: Iterable[zipfile.ZipInfo]) -> list[str]:
    """The names a wheel is imported as: each top-level package or module it unpacks."""
    found: set[str] = set()
    for member in members:
        parts = [one for one in member.filename.split("/") if one not in ("", ".", "..")]
        if len(parts) > 2 and parts[0].endswith(".data") and parts[1] in ("purelib", "platlib"):
            parts = parts[2:]
        if not parts or parts[0].startswith("."):
            continue
        first = parts[0]
        if first.endswith((".dist-info", ".data", "__pycache__")):
            continue
        found.add(first.split(".")[0])
    return sorted(found)


def requirement_lines(archive: zipfile.ZipFile) -> list[str]:
    """What the wheel's own metadata says it needs on this device: each `Requires-Dist`
    whose marker holds here, without extras."""
    metadata = next((n for n in archive.namelist()
                     if n.endswith(".dist-info/METADATA") and n.count("/") == 1), None)
    if metadata is None:
        return []
    text = archive.read(metadata).decode("utf-8", errors="replace")
    found: list[str] = []
    for line in Parser().parsestr(text).get_all("Requires-Dist") or ():
        try:
            need = Requirement(line)
        except InvalidRequirement:
            continue
        if need.marker is None or need.marker.evaluate({"extra": ""}):
            found.append(line)
    return found


def check_needs(one: Dependency, archive: zipfile.ZipFile, listed: Iterable[Dependency]) -> None:
    """Refuse a wheel that needs a dependency neither the extension lists nor VPinFE carries,
    or a listed one at a version it does not accept. VPinFE has no resolver: the list is
    the whole set."""
    pinned = {each.name: each for each in listed}
    for line in requirement_lines(archive):
        need = Requirement(line)
        name = canonicalize_name(need.name)
        if name in dependencies.SHIPPED:
            continue
        if name not in pinned or not need.specifier.contains(pinned[name].version,
                                                             prereleases=True):
            raise DependencyError(t("error.extensions.dependency_needs", dependency=str(one),
                                 needs=f"{need.name}{need.specifier}"))


def check_names(one: Dependency, names: Iterable[str]) -> None:
    """Refuse a wheel that unpacks a module VPinFE or the standard library already has."""
    for name in names:
        if (name in sys.stdlib_module_names or name in sys.builtin_module_names
                or name in imports.FIRST_PARTY
                or canonicalize_name(name) in dependencies.SHIPPED):
            raise DependencyError(t("error.extensions.dependency_shipped", dependency=str(one),
                                 module=name))


def unpack(wheel: Wheel, data: bytes, listed: Iterable[Dependency], root: Path) -> Path:
    """The wheel, checked and unpacked into its folder under `root`, which is put in place
    whole or not at all. Where the folder is there already, it is left alone."""
    target = root / wheel.folder / f"{wheel.dependency.name}-{wheel.dependency.version}"
    if (target / dependencies.RECEIPT).is_file():
        return target
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=root))
    try:
        try:
            archive = zipfile.ZipFile(BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise DependencyError(t("error.extensions.dependency_bad_file",
                                 dependency=str(wheel.dependency))) from exc
        try:
            with archive:
                members = _members(archive, wheel.dependency)
                names = top_levels(members)
                check_names(wheel.dependency, names)
                check_needs(wheel.dependency, archive, listed)
                archive.extractall(work / "files")
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error) as exc:
            raise DependencyError(t("error.extensions.dependency_bad_file",
                                 dependency=str(wheel.dependency))) from exc
        except OSError as exc:
            raise DependencyError(why(exc)) from exc
        _flatten_data(work / "files")
        receipt = {"dependency": str(wheel.dependency), "file": wheel.filename,
                   "sha256": wheel.sha256, "top_level": names}
        (work / "files" / dependencies.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(work / "files"), str(target))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def _flatten_data(files: Path) -> None:
    """A wheel's `.data` folder holds what an installer would place by kind; `purelib` and
    `platlib` are importable code and go beside the rest, and nothing else is kept."""
    for data in files.glob("*.data"):
        for kind in ("purelib", "platlib"):
            for one in sorted((data / kind).glob("*")) if (data / kind).is_dir() else ():
                shutil.move(str(one), str(files / one.name))
        shutil.rmtree(data, ignore_errors=True)


def fetch(wanted: Iterable[Dependency], root: Path | None = None) -> list[Path]:
    """The folder of each dependency in `wanted`, fetched where this device lacks it, in order.

    A dependency already unpacked for this device costs no connection. When one fails, the
    folders this call made are removed, so a failed fetch leaves nothing new behind."""
    base = root if root is not None else paths.DEPENDENCIES_DIR
    asked = list(wanted)
    made: list[Path] = []
    folders: list[Path] = []
    try:
        for one in asked:
            here = dependencies.folder_of(one, base)
            if here is None:
                wheel = choose(one)
                here = unpack(wheel, download(wheel), asked, base)
                made.append(here)
                logger.info("Fetched dependency %s", one)
            folders.append(here)
    except BaseException:
        for folder in made:
            shutil.rmtree(folder, ignore_errors=True)
            if folder.parent.is_dir() and not any(folder.parent.iterdir()):
                folder.parent.rmdir()
        raise
    return folders


def provide(folder: Path) -> list[Path]:
    """The folders of the dependencies the extension in `folder` lists, fetched where this device
    lacks them. An extension that lists none costs nothing."""
    try:
        listed = dependencies.of(read_manifest(folder).dependencies)
    except ManifestError as exc:
        raise DependencyError(t(exc.key, **exc.values)) from exc
    return fetch(listed) if listed else []


def sweep(keep: Iterable[Dependency], root: Path | None = None) -> list[Path]:
    """Remove the dependency folders no extension names any more, and any leftover half-made
    one. Returns what it removed."""
    base = root if root is not None else paths.DEPENDENCIES_DIR
    kept = {f"{one.name}-{one.version}" for one in keep}
    removed: list[Path] = []
    if not base.is_dir():
        return removed
    for kind in sorted(base.iterdir()):
        if not kind.is_dir():
            continue
        if kind.name.startswith(WORK_PREFIX):
            shutil.rmtree(kind, ignore_errors=True)
            removed.append(kind)
            continue
        for one in sorted(kind.iterdir()):
            if one.name not in kept or not (one / dependencies.RECEIPT).is_file():
                shutil.rmtree(one, ignore_errors=True)
                removed.append(one)
        if not any(kind.iterdir()):
            kind.rmdir()
    return removed


def sweep_unused() -> list[Path]:
    """Remove the dependencies no extension in any of the folders extensions are kept in names.
    Run before extensions load, from the manifests as they are on disk. When a folder cannot
    be read, nothing is removed: what that folder names is unknown."""
    keep: list[Dependency] = []
    try:
        for root in host.SEARCH_PATH:
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                try:
                    keep.extend(dependencies.of(read_manifest(folder).dependencies))
                except ManifestError:
                    continue
        return sweep(keep)
    except OSError:
        logger.warning("Could not clear the dependencies nothing uses", exc_info=True)
        return []
