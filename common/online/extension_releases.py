"""Updates for an extension the person added by hand, taken from the repository its manifest
names: the latest release there, the manifest at that release's tag, the archive of the tag.

Nothing here runs on its own. A check reads the latest release and remembers what it found;
an update fetches that release, and keeps the copy it replaces outside the folders extensions
are loaded from.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common import atomic_write, timestamps
from common.config import paths
from common.extensions import host
from common.extensions.contract import (
    MANIFEST_NAME,
    REPOSITORY,
    Manifest,
    ManifestError,
    parse,
    read_manifest,
)
from common.i18n import t
from common.online import extension_list, theme_refs
from common.online.extension_list import ExtensionListError
from common.online.theme_registry_client import ThemeRegistryClient
from common.security import http_client
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.online.extension_releases")

GITHUB = "github.com"
NEWER = "newer"
CURRENT = "current"
NONE = "none"
UNSUPPORTED = "unsupported"
UNREACHABLE = "unreachable"
KINDS = (NEWER, CURRENT, NONE, UNSUPPORTED, UNREACHABLE)
# A tag becomes part of an address, so it is held to what a tag is.
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,99}$")
_FOLDER = "folder"
_META = "previous.json"


@dataclass(frozen=True)
class Latest:
    """What one check found. `address` is the repository that was asked, `source` the
    repository the new version's own manifest names, and `moved_to` an address the repository
    now answers from."""

    kind: str
    address: str = ""
    host: str = ""
    version: str = ""
    tag: str = ""
    page: str = ""
    source: str = ""
    moved_to: str = ""
    needs: str = ""
    scopes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    play: tuple[str, ...] = ()
    checked: str = ""
    installed: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Latest | None:
        if raw.get("kind") not in KINDS:
            return None
        values: dict[str, Any] = {}
        for one in fields(cls):
            value = raw.get(one.name, one.default)
            if isinstance(one.default, tuple):
                value = tuple(str(each) for each in value) if isinstance(value, list) else ()
            elif isinstance(value, str) or one.name == "kind":
                value = str(value)
            else:
                value = ""
            values[one.name] = value
        return cls(**values)

    def still_newer(self, installed_version: str) -> bool:
        """Whether what was found is still ahead of what is installed now."""
        return self.kind == NEWER and extension_list.is_update(self.version, installed_version)


# -- reading a repository ----------------------------------------------------


def check(name: str, source: str, installed_version: str,
          client: ThemeRegistryClient | None = None) -> Latest:
    """The latest release of the repository `source`, a published one that is not a draft or
    a pre-release, and whether its manifest, read at that release's tag, is newer than
    `installed_version`. Nothing is written. Raises for a repository that cannot be reached
    or a release that cannot be read."""
    client = client or ThemeRegistryClient()
    found = REPOSITORY.match(source)
    if not found:
        raise ExtensionListError(t("error.extensions.release_unreadable", name=name))
    owner, repo = found.group(1), found.group(2)
    where = host_of(source)
    api = _api(where, owner, repo)
    if api is None:
        return Latest(UNSUPPORTED, address=source, host=where)
    response = http_client.get_response(f"{api}/releases/latest", timeout=client.timeout)
    if response.status_code == 404:
        return Latest(NONE, address=source, host=where)
    response.raise_for_status()
    try:
        release = response.json()
    except ValueError as exc:
        raise ExtensionListError(t("error.extensions.release_unreadable", name=name)) from exc
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return Latest(NONE, address=source, host=where)
    tag = str(release.get("tag_name") or "")
    if not _TAG.match(tag) or ".." in tag:
        raise ExtensionListError(t("error.extensions.release_unreadable", name=name))

    raw = client.fetch_json(theme_refs.raw_url(source, tag, MANIFEST_NAME))
    try:
        manifest = parse(raw)
    except ManifestError as exc:
        raise ExtensionListError(t(exc.key, **exc.values)) from exc
    if manifest.name != name:
        raise ExtensionListError(t("error.extensions.names_another", name=name,
                                   other=manifest.name))
    page = _release_page(release, source, tag)
    return Latest(
        NEWER if extension_list.is_update(manifest.version, installed_version) else CURRENT,
        address=source, host=where, version=manifest.version, tag=tag, page=page,
        source=manifest.source, moved_to=_moved_to(source, page, manifest.source),
        needs=extension_list.needs_newer(raw.get(extension_list.MIN_VERSION_KEY)),
        scopes=manifest.scopes, capabilities=manifest.capabilities, play=manifest.play,
        checked=timestamps.utc_now_iso(), installed=installed_version)


def host_of(address: str) -> str:
    return urlsplit(address).netloc


def _read(directory: Path) -> Manifest:
    try:
        return read_manifest(directory)
    except ManifestError as exc:
        raise ExtensionListError(t(exc.key, **exc.values)) from exc


def _api(where: str, owner: str, repo: str) -> str | None:
    """The releases API of the repository's host: GitHub's, or a Forgejo site's, which answers
    for itself at `/api/v1/version`. None for a host that is neither."""
    if where == GITHUB:
        return f"https://api.github.com/repos/{owner}/{repo}"
    base = f"https://{where}/api/v1"
    answer = http_client.get_response(f"{base}/version")
    try:
        body = answer.json() if answer.status_code == 200 else None
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("version"):
        return f"{base}/repos/{owner}/{repo}"
    return None


def _release_page(release: Mapping[str, Any], source: str, tag: str) -> str:
    page = str(release.get("html_url") or "")
    if page.startswith("https://") and REPOSITORY.match("/".join(page.split("/")[:5])):
        return page
    return f"{source.rstrip('/')}/releases/tag/{tag}"


def _repository(address: str) -> tuple[str, str, str]:
    found = REPOSITORY.match(address)
    where = urlsplit(address).netloc.casefold()
    return (where, found.group(1).casefold(), found.group(2).casefold()) if found else ("", "", "")


def _moved_to(asked: str, page: str, declared: str) -> str:
    """Where the repository now lives, when the address asked is not it: the repository the
    release page is in, else the one the new version's manifest names. Empty when neither
    differs."""
    for there in ("/".join(page.split("/")[:5]), declared):
        if _repository(there) != _repository(asked) and _repository(there)[0]:
            return there
    return ""


# -- what the last check found -----------------------------------------------


def held() -> dict[str, Latest]:
    try:
        data = json.loads(paths.EXTENSION_RELEASES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    kept = {}
    for name, raw in (data.items() if isinstance(data, dict) else ()):
        found = Latest.from_dict(raw) if isinstance(raw, dict) else None
        if found is not None:
            kept[str(name)] = found
    return kept


def remember(name: str, found: Latest) -> None:
    kept = {key: one.as_dict() for key, one in held().items()}
    kept[name] = found.as_dict()
    _write(kept)


def forget(name: str) -> None:
    kept = {key: one.as_dict() for key, one in held().items() if key != name}
    _write(kept)


def _write(kept: dict[str, Any]) -> None:
    paths.EXTENSION_RELEASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write.write_atomic(paths.EXTENSION_RELEASES_PATH,
                              lambda out: json.dump(kept, out, indent=2))


# -- updating and going back -------------------------------------------------


def update(name: str, target: Path, found: Latest, *, source: str,
           client: ThemeRegistryClient | None = None, was_accepted: bool = False,
           trust: Callable[[Manifest], None] | None = None) -> str:
    """Replace the extension in `target` with the release `found` describes, and keep the copy
    it replaces. `source` is the repository the installed manifest names now; it must be the
    one that was checked. `trust` runs once the new files are in place, and a failure there
    puts everything back. Returns the new version.

    A folder kept with Git is never touched. The release is fetched again, so what arrives is
    checked against what the check showed: another version, or other access, means it changed
    since and the person checks again."""
    if (target / ".git").exists():
        raise ExtensionListError(t("error.extensions.updated_with_git", name=name))
    if found.kind != NEWER or found.address != source:
        raise ExtensionListError(t("error.extensions.not_checked", name=name))
    before = _read(target).version
    if not found.still_newer(before):
        raise ExtensionListError(t("error.extensions.not_checked", name=name))
    if found.needs:
        raise ExtensionListError(t("error.extensions.needs_newer_vpinfe", name=name,
                                   needs=found.needs, running=get_version()))
    client = client or ThemeRegistryClient()
    data = client.download_zip(f"{source.rstrip('/')}/archive/{found.tag}.zip",
                               limit=extension_list.ARCHIVE_LIMIT)
    work = _work_folder()
    keep_work = False
    try:
        unpacked, version = extension_list.unpack(data, work / "new", name)
        arrived = _read(unpacked)
        if (arrived.version, arrived.scopes, arrived.capabilities, arrived.play) != (
                found.version, found.scopes, found.capabilities, found.play):
            raise ExtensionListError(t("error.extensions.changed_since_checked", name=name))
        extension_list.swap(unpacked, target, work / "old")
        kept = False
        try:
            _keep(name, work / "old", work / "prior",
                  {"version": before, "replaced_by": version, "was_accepted": was_accepted})
            kept = True
            if trust is not None:
                trust(arrived)
        except BaseException:
            if kept:
                _unkeep(name, work / "prior")
            try:
                extension_list.swap_back(target, work / "old")
            except OSError:
                keep_work = True
                logger.error("Could not put back the previous %s; it is kept in %s",
                             name, work / "old", exc_info=True)
            raise
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    logger.info("Updated extension %s from %s to %s from %s", name, before, version, source)
    return version


def previous(name: str, installed_version: str) -> dict[str, Any] | None:
    """The copy the last update replaced, while that update is what is installed. A newer
    version copied in since leaves nothing to go back to: the copy is older than what the
    person chose."""
    held_at = paths.EXTENSION_PREVIOUS_DIR / name
    try:
        meta = json.loads((held_at / _META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(meta, dict) or str(meta.get("replaced_by") or "") != installed_version
            or not (held_at / _FOLDER / MANIFEST_NAME).is_file()):
        return None
    return {"version": str(meta.get("version") or ""),
            "was_accepted": bool(meta.get("was_accepted"))}


def go_back(name: str, target: Path, *, installed_version: str,
            restore: Callable[[Manifest], None] | None = None) -> str:
    """Put the copy the last update replaced back in `target`, and drop it: there is nothing
    behind it to go back to. `restore` runs once the files are in place, and a failure there
    puts the version that was there back. Returns the version now in place."""
    if (target / ".git").exists():
        raise ExtensionListError(t("error.extensions.updated_with_git", name=name))
    if previous(name, installed_version) is None:
        raise ExtensionListError(t("error.extensions.no_previous", name=name))
    held_at = paths.EXTENSION_PREVIOUS_DIR / name
    work = _work_folder()
    keep_work = False
    try:
        shutil.copytree(held_at / _FOLDER, work / "new")
        extension_list.swap(work / "new", target, work / "old")
        try:
            back = _read(target)
            if restore is not None:
                restore(back)
        except BaseException:
            try:
                extension_list.swap_back(target, work / "old")
            except OSError:
                keep_work = True
                logger.error("Could not put back %s; it is kept in %s", name, work / "old",
                             exc_info=True)
            raise
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(held_at, ignore_errors=True)
    logger.info("Went back to %s %s", name, back.version)
    return back.version


_WORK = ".update-"


def _work_folder() -> Path:
    """A fresh folder beside the extensions to work in. A leftover that still holds the copy an
    update set aside is never swept, since for a folder added by hand it can be the only copy."""
    root = host.INSTALLED_DIR.parent
    now = time.time()
    try:
        for one in root.iterdir():
            if (one.name.startswith(_WORK) and one.is_dir() and not (one / "old").exists()
                    and now - one.stat().st_mtime > extension_list.STALE_WORK_S):
                shutil.rmtree(one, ignore_errors=True)
    except OSError:
        logger.warning("Could not clear the leftovers of earlier updates", exc_info=True)
    return Path(tempfile.mkdtemp(prefix=_WORK, dir=root))


def _keep(name: str, old: Path, prior: Path, meta: dict[str, Any]) -> None:
    """Keep a copy of `old` as the version `name` can go back to. What was kept before moves
    to `prior` and is put back if this fails."""
    held_at = paths.EXTENSION_PREVIOUS_DIR / name
    moved = os.path.lexists(held_at)
    if moved:
        os.replace(held_at, prior)
    try:
        held_at.mkdir(parents=True)
        shutil.copytree(old, held_at / _FOLDER)
        atomic_write.write_atomic(held_at / _META, lambda out: json.dump(meta, out))
    except BaseException:
        _unkeep(name, prior)
        raise


def _unkeep(name: str, prior: Path) -> None:
    """Take away what `_keep` made and bring back what it moved to `prior`."""
    held_at = paths.EXTENSION_PREVIOUS_DIR / name
    shutil.rmtree(held_at, ignore_errors=True)
    if os.path.lexists(prior):
        os.replace(prior, held_at)
