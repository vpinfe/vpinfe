"""What can be done to the extensions on the VPinFE Extension List: read it, install from it,
update, remove. The HTTP layer calls these; nothing here knows the wire."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import requests

from common import extensions, service_errors
from common.failures import why
from common.i18n import t
from common.online import extension_list
from common.online.theme_registry_client import ThemeRegistryError
from common.security import accepted

logger = logging.getLogger("vpinfe.common.online.extension_ops")

_lock = threading.RLock()
ExtensionError = extension_list.ExtensionListError


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
            "left": sorted(one.name for one in extensions.records() if one.left_list)}


def check() -> dict[str, Any]:
    """Fetch the list again and keep it. The copy already kept stays when this fails."""
    with _lock:
        try:
            extension_list.refresh()
        except (ExtensionError, ThemeRegistryError, requests.RequestException,
                OSError) as exc:
            raise UnavailableError(why(exc)) from exc
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

