"""The VPinFE Extension List: what it holds, what is installed from it, and the copy kept for
when there is no connection.

An official extension is one the list names. The list is a file of `{name, url, ref?,
min_vpinfe?}` entries; each entry's repository holds the extension's own `extension.json`,
read at the entry's ref, which is where its version comes from.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from common import atomic_write, timestamps
from common.config import paths
from common.extensions import fingerprint, host
from common.extensions.contract import MANIFEST_NAME, NAME_PATTERN, ManifestError, read_manifest
from common.i18n import t
from common.online import theme_dates, theme_refs
from common.online.theme_registry_client import ThemeRegistryClient, ThemeRegistryError
from common.values import newer_version, parse_version
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.online.extension_list")

OFFICIAL_LIST = "https://raw.githubusercontent.com/vpinfe/extensions/main/extension_list.json"
MIN_VERSION_KEY = "min_vpinfe"
_WORKERS = 8
# A work folder this old belongs to an install that died before it could clean up.
STALE_WORK_S = 3600
WORK_PREFIXES = (".install-", ".remove-")
# What an archive may be: as sent, unpacked and in files. An extension is code and a picture
# or two, so these are far above any real one.
ARCHIVE_LIMIT = 50 * 1024 * 1024
UNPACKED_LIMIT = 200 * 1024 * 1024
FILE_LIMIT = 5000


class ExtensionListError(Exception):
    """The list or an extension in it could not be read or installed. The text is a line a
    person reads."""


@dataclass(frozen=True)
class Listed:
    """One extension the list names, as its repository describes it at its ref."""

    name: str
    repository: str
    ref: str
    version: str
    minimum: str = ""
    needs: str = ""
    display_name: str = ""
    author: str = ""
    description: str = ""
    updated: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "url": self.repository, "ref": self.ref,
                "version": self.version, "min_vpinfe": self.minimum, "needs": self.needs,
                "display_name": self.display_name, "author": self.author,
                "description": self.description, "updated": self.updated}


@dataclass(frozen=True)
class Held:
    """The list as it was last fetched, and when."""

    checked: str
    entries: tuple[Listed, ...]

    def get(self, name: str) -> Listed | None:
        return next((one for one in self.entries if one.name == name), None)


def needs_newer(declared: object) -> str:
    """The VPinFE an entry asks for when this one is older, else empty."""
    needs = parse_version(declared)
    running = parse_version(get_version())
    if needs and running and newer_version(needs, running):
        return str(declared)
    return ""


def is_update(listed_version: str, installed_version: str) -> bool:
    return newer_version(parse_version(listed_version), parse_version(installed_version))


# -- the copy kept for when there is no connection ---------------------------


def held() -> Held | None:
    """The list as last fetched, None when it never was or the copy cannot be read. What an
    entry needs is worked out here against the VPinFE now running, so an upgrade clears it
    without another check."""
    try:
        data = json.loads(paths.EXTENSION_LIST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = data.get("extensions") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    kept = []
    for one in entries:
        if isinstance(one, dict) and NAME_PATTERN.match(str(one.get("name") or "")):
            kept.append(Listed(
                name=str(one["name"]), repository=str(one.get("url") or ""),
                ref=str(one.get("ref") or ""), version=str(one.get("version") or ""),
                minimum=str(one.get("min_vpinfe") or ""),
                needs=needs_newer(one.get("min_vpinfe")),
                display_name=str(one.get("display_name") or ""),
                author=str(one.get("author") or ""),
                description=str(one.get("description") or ""),
                updated=str(one.get("updated") or "")))
    return Held(str(data.get("checked") or ""), tuple(kept))


def _keep(entries: Iterable[Listed]) -> Held:
    made = Held(timestamps.utc_now_iso(), tuple(entries))
    body = {"checked": made.checked, "extensions": [one.as_dict() for one in made.entries]}
    paths.EXTENSION_LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write.write_atomic(paths.EXTENSION_LIST_PATH,
                              lambda out: json.dump(body, out, indent=2))
    return made


# -- reading the list --------------------------------------------------------


def refresh(client: ThemeRegistryClient | None = None, url: str = OFFICIAL_LIST) -> Held:
    """Fetch the list and every manifest it points at, and keep the result. On any failure
    to read the list itself the copy already kept stays, and the failure is raised.

    Only the official address is read. An entry whose manifest cannot be read keeps what the
    last check held for it: dropping it would make an installed extension look as if it had
    left the list. When no manifest at all could be read the check failed."""
    client = client or ThemeRegistryClient()
    before = held()
    earlier = {one.name: one for one in before.entries} if before else {}
    return _keep(read_list(client, url, earlier))


def read_list(client: ThemeRegistryClient, url: str,
              earlier: Mapping[str, Listed] | None = None) -> list[Listed]:
    """The list at `url` with each entry described by its manifest, and nothing written.
    `earlier` stands in for an entry whose manifest cannot be read."""
    earlier = earlier or {}
    body = client.fetch_json(url)
    raw = body.get("extensions")
    if not isinstance(raw, list):
        raise ExtensionListError(t("error.extensions.list_unreadable"))
    entries = _first_of_each(one for one in raw if _entry_ok(one))
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        read = list(pool.map(
            lambda one: _described(client, one, earlier.get(str(one["name"]))), entries))
    if entries and all(unread for _, unread in read):
        raise ExtensionListError(t("error.extensions.list_unreadable"))
    return [one for one, _ in read if one is not None]


def _first_of_each(entries: Iterable[dict]) -> list[dict]:
    """A name the list gives twice means its first entry: one name is one extension."""
    seen: set[str] = set()
    kept = []
    for one in entries:
        name = str(one["name"])
        if name in seen:
            logger.warning("Extension List names %s twice; the later entry is ignored", name)
            continue
        seen.add(name)
        kept.append(one)
    return kept


def _entry_ok(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    name, url = str(entry.get("name") or ""), str(entry.get("url") or "")
    ok = bool(NAME_PATTERN.match(name)) and url.startswith("https://")
    if not ok:
        logger.warning("Extension List entry ignored: %r", entry)
    return ok


def _described(client: ThemeRegistryClient, entry: dict,
               earlier: Listed | None) -> tuple[Listed | None, bool]:
    """The entry as its manifest describes it, and whether the manifest could not be read
    (in which case `earlier` stands in for it)."""
    name = str(entry["name"])
    repository = str(entry["url"]).rstrip("/")
    ref = str(entry.get("ref") or "")
    try:
        manifest = client.fetch_json(theme_refs.raw_url(repository, ref, MANIFEST_NAME))
    except ThemeRegistryError as exc:
        logger.warning("Extension %s: manifest not read: %s", name, exc)
        return earlier, True
    if str(manifest.get("name") or "") != name:
        logger.warning("Extension List entry %s names itself %r; ignored",
                       name, manifest.get("name"))
        return None, False
    declared = entry.get(MIN_VERSION_KEY) or manifest.get(MIN_VERSION_KEY)
    return Listed(
        name=name, repository=repository, ref=ref,
        version=str(manifest.get("version") or ""), minimum=str(declared or ""),
        needs=needs_newer(declared),
        display_name=str(manifest.get("display_name") or ""),
        author=str(manifest.get("author") or ""),
        description=str(manifest.get("description") or ""),
        updated=_dated(client, repository, ref, earlier)), False


def _dated(client: ThemeRegistryClient, repository: str, ref: str,
           earlier: Listed | None) -> str:
    """When the repository's ref last changed, else what the last check had."""
    kept = earlier.updated if earlier is not None else ""
    if kept and theme_refs.is_tag(ref) and earlier is not None and earlier.ref == ref:
        return kept
    return theme_dates.committed_at(repository, ref, client.fetch_any) or kept


# -- installing --------------------------------------------------------------


def archive_url(listed: Listed) -> str:
    """The source archive for the entry's ref. `HEAD` is left alone rather than rewritten
    to a branch name, which is wrong on a repository whose default branch is `main`."""
    return f"{listed.repository}/archive/{theme_refs.bare_ref(listed.ref)}.zip"


def install(listed: Listed, client: ThemeRegistryClient | None = None) -> Path:
    """Install what the list names into the official folder, and keep a receipt.

    Refused before anything is downloaded when the extension needs a newer VPinFE. The old
    copy is moved out of the way only once the new one is unpacked and checked, and moved
    back if the swap fails; it never sits beside the new one inside the official folder,
    where a second folder holding a manifest would be loaded as an extension.
    """
    if listed.needs:
        raise ExtensionListError(t("error.extensions.needs_newer_vpinfe", name=listed.name,
                                   needs=listed.needs, running=get_version()))
    client = client or ThemeRegistryClient()
    data = client.download_zip(archive_url(listed), limit=ARCHIVE_LIMIT)
    target = host.OFFICIAL_DIR / listed.name
    host.OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
    sweep_work()
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIXES[0], dir=host.OFFICIAL_DIR.parent))
    keep_work = False
    try:
        unpacked, version = unpack(data, work / "new", listed.name)
        swap(unpacked, target, work / "old")
        try:
            fingerprint.write_receipt(listed.name, target, repository=listed.repository,
                                      release=version)
        except BaseException:
            try:
                swap_back(target, work / "old")
            except OSError:
                keep_work = True
                logger.error("Could not put back the previous %s; it is kept in %s",
                             listed.name, work / "old", exc_info=True)
            raise
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    logger.info("Installed extension %s %s from %s", listed.name, version, listed.repository)
    return target


def fetch(listed: Listed, client: ThemeRegistryClient, into: Path) -> tuple[Path, str]:
    """What the list names, unpacked and checked under `into`: the folder that holds its
    manifest, and its version. Installs nothing and keeps no receipt."""
    return unpack(client.download_zip(archive_url(listed), limit=ARCHIVE_LIMIT), into,
                  listed.name)


def unpack(data: BytesIO, into: Path, name: str) -> tuple[Path, str]:
    """An archive of the extension `name` unpacked under `into` and checked: the folder that
    holds its manifest, and its version."""
    into.mkdir(parents=True)
    try:
        with zipfile.ZipFile(data) as archive:
            members = archive.infolist()
            if (len(members) > FILE_LIMIT
                    or sum(one.file_size for one in members) > UNPACKED_LIMIT):
                raise ExtensionListError(t("error.extensions.too_big", name=name))
            archive.extractall(into)
    except zipfile.BadZipFile as exc:
        raise ExtensionListError(t("error.extensions.bad_archive", name=name)) from exc
    top = [one for one in into.iterdir() if one.is_dir()]
    root = top[0] if len(top) == 1 and not (into / MANIFEST_NAME).is_file() else into
    try:
        manifest = read_manifest(root)
    except ManifestError as exc:
        raise ExtensionListError(t(exc.key, **exc.values)) from exc
    if manifest.name != name:
        raise ExtensionListError(t("error.extensions.names_another", name=name,
                                   other=manifest.name))
    for one in root.iterdir():
        if one.is_dir() and (one.name == "tests" or one.name.startswith(".")):
            shutil.rmtree(one)
    return root, manifest.version


def swap(new: Path, target: Path, aside: Path) -> None:
    held_back = False
    if os.path.lexists(target):
        os.replace(target, aside)
        held_back = True
    try:
        shutil.move(str(new), str(target))
    except BaseException:
        if held_back:
            os.replace(aside, target)
        raise


def swap_back(target: Path, aside: Path) -> None:
    """Undo `swap` once the new copy is in place: the old one returns, or nothing is left
    where there was nothing before."""
    shutil.rmtree(target, ignore_errors=True)
    if os.path.lexists(aside):
        os.replace(aside, target)


def sweep_work() -> None:
    """Remove the work folders an install or a removal that died left in the config folder.
    A recent one may belong to a run in progress, so it stays."""
    now = time.time()
    try:
        found = [one for one in host.OFFICIAL_DIR.parent.iterdir()
                 if one.name.startswith(WORK_PREFIXES) and one.is_dir()]
    except OSError:
        return
    for one in found:
        try:
            if now - one.stat().st_mtime > STALE_WORK_S:
                shutil.rmtree(one, ignore_errors=True)
        except OSError:
            logger.warning("Could not remove the leftover %s", one.name, exc_info=True)


def uninstall(name: str) -> bool:
    """Remove an extension VPinFE installed, with its receipt. False when it is not in the
    official folder. Only the folder and the receipt go: what the extension saved lives
    with its settings, not in its package."""
    target = host.OFFICIAL_DIR / name
    if not NAME_PATTERN.match(name) or not target.is_dir():
        return False
    sweep_work()
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIXES[1], dir=host.OFFICIAL_DIR.parent))
    try:
        os.replace(target, work / name)
        fingerprint.forget_receipt(name)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    logger.info("Removed extension %s", name)
    return True


def updates(installed: Mapping[str, str], kept: Held | None) -> dict[str, Listed]:
    """The listed extensions newer than what is installed, by name."""
    if kept is None:
        return {}
    return {one.name: one for one in kept.entries
            if one.name in installed and not one.needs
            and is_update(one.version, installed[one.name])}
