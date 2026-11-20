"""What can be done to the extensions on the VPinFE Extension List: read it, install from it,
update, remove. The HTTP layer calls these; nothing here knows the wire."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import requests

from common import extensions, service_errors
from common.extensions import dependencies
from common.extensions.contract import REPOSITORY, Manifest, read_manifest
from common.extensions.host import Record
from common.failures import why
from common.i18n import t
from common.online import extension_dependencies, extension_list, extension_releases
from common.online.theme_registry_client import ThemeRegistryError
from common.security import accepted

logger = logging.getLogger("vpinfe.common.online.extension_ops")

_lock = threading.RLock()
ExtensionError = extension_list.ExtensionListError
_UNREACHABLE = (ThemeRegistryError, requests.RequestException, OSError)

# What a row of `releases` says besides what a check found.
GIT = "git"
UNCHECKED = "unchecked"


class UnavailableError(service_errors.ServiceError):
    """The list could not be reached. Nothing about this install is wrong."""


class InstallFailedError(service_errors.ServiceError):
    """The list was read and the install or removal did not finish."""


def _installed_versions() -> dict[str, str]:
    """The version of each extension VPinFE itself put here: shipped, or from the list."""
    return {record.name: record.manifest.version for record in extensions.records()
            if record.manifest is not None and accepted.is_official(record.origin)
            and Path(record.folder).is_dir()}


def state() -> dict[str, Any]:
    """The list as last fetched, each entry with what this install has of it. Makes no
    request: offline, this is all there is."""
    kept = extension_list.held()
    installed = _installed_versions()
    updates = extension_list.updates(installed, kept)
    entries = []
    for one in (kept.entries if kept else ()):
        entries.append({**one.as_dict(), "installed": one.name in installed,
                        "installed_version": installed.get(one.name, ""),
                        "update": one.name in updates})
    return {"checked": kept.checked if kept else "", "list": extension_list.OFFICIAL_LIST,
            "entries": entries, "updates": sorted(updates),
            "left": sorted(one.name for one in extensions.records() if one.left_list),
            "releases": _releases()}


def check() -> dict[str, Any]:
    """Fetch the list again and keep it. The copy already kept stays when this fails."""
    with _lock:
        try:
            extension_list.refresh()
        except (ExtensionError, ThemeRegistryError, requests.RequestException,
                OSError) as exc:
            raise UnavailableError(why(exc)) from exc
    return state()


def check_all() -> dict[str, Any]:
    """Fetch the list again and ask the repository of every extension that was added by hand.
    A repository that does not answer is shown on its own row; only the list failing raises."""
    with _lock:
        failed = None
        try:
            extension_list.refresh()
        except (ExtensionError, *_UNREACHABLE) as exc:
            failed = UnavailableError(why(exc))
        for record in _added():
            try:
                _check_release(record)
            except (UnavailableError, InstallFailedError, OSError):
                pass
        if failed is not None:
            raise failed
    return state()


def install(name: str) -> dict[str, Any]:
    """Install, update or reinstall `name` from the list. It runs from the next start."""
    with _lock:
        listed = _listed(name)
        try:
            extension_list.install(listed)
        except (ExtensionError, ThemeRegistryError, requests.RequestException,
                OSError) as exc:
            raise InstallFailedError(why(exc)) from exc
        _replaced(listed.name)
    return state()


def update_all() -> dict[str, Any]:
    """Every update the list has for what is installed. The first failure stops the rest and
    says which one."""
    with _lock:
        for name in state()["updates"]:
            install(name)
    return state()


def remove(name: str) -> dict[str, Any]:
    """Take away an extension that came from the list. The one the build ships is turned
    off, not removed."""
    with _lock:
        try:
            gone = extension_list.uninstall(name)
        except OSError as exc:
            raise InstallFailedError(why(exc)) from exc
        if not gone:
            raise service_errors.NotFoundError(t("error.extensions.not_found", name=name))
        _replaced(name)
    return state()


def get_dependencies(name: str) -> dict[str, Any]:
    """Fetch the dependencies `name` lists that this device lacks, and start it if that was what
    held it back. It runs now when it needs nothing else to restart."""
    with _lock:
        record = extensions.registry().get(name)
        if record is None or record.manifest is None:
            raise service_errors.NotFoundError(t("error.extensions.not_found", name=name))
        if not record.is_accepted():
            raise service_errors.BlockedError(t("error.extensions.dependency_not_trusted"))
        try:
            extension_dependencies.fetch(dependencies.of(record.manifest.dependencies))
        except extension_dependencies.DependencyError as exc:
            raise InstallFailedError(why(exc)) from exc
        extensions.dependencies_arrived(name)
    return state()


def _replaced(name: str) -> None:
    """Tell the host the files changed. The install or removal is done by now, so a failure
    here is logged and never reported as the install failing."""
    try:
        extensions.files_replaced(name)
    except Exception:  # noqa: BLE001 - the work is done; the host looks again at the next start
        logger.warning("Could not re-read extension %s after its files changed", name,
                       exc_info=True)


def _listed(name: str) -> extension_list.Listed:
    kept = extension_list.held()
    found = kept.get(name) if kept else None
    if found is None:
        raise service_errors.NotFoundError(t("error.extensions.not_listed", name=name))
    return found



# -- extensions the person added by hand -------------------------------------


def _added() -> list[Record]:
    """The extensions that can be updated from the repository their manifest names: found in a
    folder the person put them in, not claiming to be the team's, and naming a repository."""
    return [one for one in extensions.records() if _eligible(one)]


def _eligible(record: Record) -> bool:
    manifest = _on_disk(record)
    return (manifest is not None and record.origin == accepted.IN_FOLDER
            and not record.from_list and not record.bundled and not record.claims_team
            and bool(REPOSITORY.match(manifest.source)))


def _on_disk(record: Record) -> Manifest | None:
    """The manifest in the folder now. After an update a running extension still holds the
    old one in memory, so what is installed is read from the files."""
    try:
        return read_manifest(record.directory)
    except (OSError, ValueError):
        return None


def _added_record(name: str) -> Record:
    record = extensions.registry().get(name)
    if record is None or not _eligible(record):
        raise service_errors.NotFoundError(t("error.extensions.not_found", name=name))
    return record


def _new_access(record: Record, found: extension_releases.Latest) -> list[str]:
    """What the release asks for beyond what was accepted. An extension that was never
    accepted has nothing to widen: the update does not accept it."""
    if not accepted.accepted_before(record.name, record.origin, record.folder):
        return []
    return accepted.new_access(record.name, record.origin, found.scopes, found.capabilities,
                               record.folder, found.play, found.dependencies)


def _releases() -> dict[str, dict[str, Any]]:
    kept = extension_releases.held()
    rows = {}
    for record in _added():
        manifest = _on_disk(record)
        if manifest is not None:
            rows[record.name] = _row(record, manifest, kept.get(record.name))
    return rows


def _row(record: Record, manifest: Manifest, found: extension_releases.Latest | None,
         ) -> dict[str, Any]:
    row: dict[str, Any] = {
        "state": UNCHECKED, "address": manifest.source, "host": "", "version": "", "tag": "",
        "page": "", "moved_to": "", "needs": "", "new_access": [], "dependencies": [],
        "checked": "",
        "installed": manifest.version,
        "previous": ""}
    if (record.directory / ".git").exists():
        row["state"] = GIT
        return row
    row["previous"] = (extension_releases.previous(record.name, manifest.version) or {}).get(
        "version", "")
    if found is not None and found.address == manifest.source:
        newer = found.still_newer(manifest.version)
        row.update(state=extension_releases.CURRENT if found.kind == extension_releases.NEWER
                   and not newer else found.kind, host=found.host, version=found.version,
                   tag=found.tag, page=found.page, moved_to=found.moved_to, needs=found.needs,
                   checked=found.checked)
        if newer:
            row["new_access"] = _new_access(record, found)
            row["dependencies"] = list(found.dependencies)
    return row


def check_release(name: str) -> dict[str, Any]:
    """Ask the repository of one extension that was added by hand what its latest release is."""
    with _lock:
        _check_release(_added_record(name))
    return state()


def _check_release(record: Record) -> None:
    manifest = _on_disk(record)
    if manifest is None or (record.directory / ".git").exists():
        return
    name, address = record.name, manifest.source
    try:
        found = extension_releases.check(name, address, manifest.version)
    except ExtensionError as exc:
        logger.warning("Could not read the latest release of %s at %s: %s", name, address, exc)
        raise InstallFailedError(why(exc)) from exc
    except _UNREACHABLE as exc:
        logger.warning("Could not reach %s for %s: %s", address, name, exc)
        extension_releases.remember(name, extension_releases.Latest(
            extension_releases.UNREACHABLE, address=address,
            host=extension_releases.host_of(address)))
        raise UnavailableError(why(exc)) from exc
    extension_releases.remember(name, found)
    logger.info("Checked %s at %s: %s", name, address, found.kind)


def update_release(name: str, *, trust: bool = False, version: str = "") -> dict[str, Any]:
    """Replace an extension added by hand with the release its last check found. A release that
    asks for more than was accepted needs `trust`, which accepts the new access with it. When
    `version` is given it must be the release the check holds now: it is the one the person was
    shown. It runs from the next start."""
    with _lock:
        record = _added_record(name)
        manifest = _on_disk(record)
        found = extension_releases.held().get(name)
        if manifest is None or found is None:
            raise InstallFailedError(t("error.extensions.not_checked", name=name))
        if version and found.version != version:
            raise InstallFailedError(t("error.extensions.changed_since_checked", name=name))
        if _new_access(record, found) and not trust:
            raise InstallFailedError(t("error.extensions.needs_trust", name=name))
        origin, folder = record.origin, record.folder
        was = accepted.accepted_before(name, origin, folder)

        def accept(arrived: Manifest) -> None:
            if was:
                _accept(name, origin, folder, arrived)

        try:
            extension_releases.update(name, record.directory, found, source=manifest.source,
                                      was_accepted=was, trust=accept)
        except (ExtensionError, *_UNREACHABLE) as exc:
            logger.warning("Could not update %s from %s: %s", name, manifest.source, exc)
            raise InstallFailedError(why(exc)) from exc
        _replaced(name)
    return state()


def use_previous(name: str) -> dict[str, Any]:
    """Put back the copy the last update from the repository replaced. It runs from the next
    start."""
    with _lock:
        record = _added_record(name)
        manifest = _on_disk(record)
        before = extension_releases.previous(name, manifest.version) if manifest else None
        if manifest is None or before is None:
            raise InstallFailedError(t("error.extensions.no_previous", name=name))
        origin, folder = record.origin, record.folder

        def restore(back: Manifest) -> None:
            if before["was_accepted"]:
                _accept(name, origin, folder, back)

        try:
            extension_releases.go_back(name, record.directory,
                                       installed_version=manifest.version, restore=restore)
        except (ExtensionError, OSError) as exc:
            logger.warning("Could not go back to the previous %s: %s", name, exc)
            raise InstallFailedError(why(exc)) from exc
        _replaced(name)
    return state()


def _accept(name: str, origin: str, folder: str, manifest: Manifest) -> None:
    accepted.accept_extension(name, origin, folder=folder, version=manifest.version,
                              scopes=manifest.scopes, capabilities=manifest.capabilities,
                              play=manifest.play, dependencies=manifest.dependencies)
