"""What one theme source said when it was read: its kind, its state, and why.

A result is a plain dict, because it is kept in the theme cache and sent over the wire as
it is.
"""

from __future__ import annotations

from typing import Any

import requests

from common.failures import upstream, why
from common.i18n import t
from common.online import theme_sources
from common.online.theme_registry_client import ThemeRegistryError

LIST = "list"
THEME = "theme"

READY = "ready"
NOT_CHECKED = "not_checked"
NOT_ANSWERING = "not_answering"
NOT_FOUND = "not_found"
NOT_A_THEME = "not_a_theme"
NEEDS_VPINFE = "needs_vpinfe"
SOME_UNREADABLE = "some_unreadable"
NOT_USED = "not_used"
ALREADY_ADDED = "already_added"

BAD = frozenset({NOT_ANSWERING, NOT_FOUND, NOT_A_THEME})
WARN = frozenset({NEEDS_VPINFE, SOME_UNREADABLE, NOT_USED})
# The states Add refuses. Not answering is let through, so an offline machine can add.
REFUSED = frozenset({NOT_FOUND, NOT_A_THEME, ALREADY_ADDED})

_GONE = (404, 410)


class NothingReadError(ThemeRegistryError):
    """Every source failed. `reports` holds what each one said, which the read that raised
    would otherwise take with it."""

    def __init__(self, message: str, reports: dict[str, dict[str, Any]]) -> None:
        super().__init__(message)
        self.reports = reports


class ManifestError(ThemeRegistryError):
    """A `manifest.json` that is there and is not a theme's. `fields` names what it lacks;
    empty when the fault is the value of one, and `bad_layouts` says it is `layouts`."""

    def __init__(self, message: str, fields: list[str] | None = None,
                 bad_layouts: bool = False) -> None:
        super().__init__(message)
        self.fields = list(fields or [])
        self.bad_layouts = bad_layouts


def result(address: str, kind: str, **fields: Any) -> dict[str, Any]:
    """A source's result, read as ready until a field says otherwise."""
    where = theme_sources.parse(address)
    return {
        "address": address,
        "kind": kind,
        "display": where.display if where else address,
        "link": where.link if where else "",
        "name": where.display if where else address,
        "state": READY,
        "reason": "",
        "count": 0,
        "version": "",
        # The VPinFE a single theme asks for, when this one is older.
        "needs": "",
        "pinned": where.ref if where else "",
        # Names a source above this one already offers, so these stay as they are.
        "took": [],
        # Names this list marks to install on their own.
        "installs": [],
        **fields,
    }


def waiting(address: str, kind: str) -> dict[str, Any]:
    return result(address, kind, state=NOT_CHECKED, reason=t("themes.source.waiting"))


def gone(exc: BaseException) -> bool:
    """Whether the host answered that nothing is there."""
    cause = _cause(exc)
    return (isinstance(cause, requests.HTTPError)
            and getattr(cause.response, "status_code", 0) in _GONE)


def _cause(exc: BaseException) -> BaseException:
    inner = exc.__cause__
    return inner if isinstance(exc, ThemeRegistryError) and inner is not None else exc


def classify(exc: BaseException, url: str = "") -> tuple[str, str]:
    """(state, reason) for a read that raised."""
    if isinstance(exc, ManifestError):
        return NOT_A_THEME, _manifest_reason(exc)
    if gone(exc):
        return NOT_FOUND, t("themes.source.not_found")
    cause = _cause(exc)
    if upstream(cause):
        return NOT_ANSWERING, why(cause, url)
    return NOT_A_THEME, str(exc) or why(exc)


def _manifest_reason(exc: ManifestError) -> str:
    if exc.fields:
        return t("themes.source.bad_manifest", fields=", ".join(exc.fields))
    if exc.bad_layouts:
        return t("themes.source.bad_layouts")
    return t("themes.source.bad_type")


def listed(address: str, themes: dict[str, Any]) -> dict[str, Any]:
    marked = [key for key, entry in themes.items()
              if isinstance(entry, dict) and theme_sources.installs_on_its_own(entry, address)]
    return result(address, LIST, count=len(themes), installs=marked)


def failed(address: str, kind: str, state: str, reason: str) -> dict[str, Any]:
    return result(address, kind, state=state, reason=reason)


def unreadable(names: dict[str, str]) -> str:
    """The words for a list some of whose themes could not be read."""
    first = next(iter(names.values()))
    return t("themes.source.some_unreadable", names=", ".join(names), reason=first)


def level(state: str) -> str:
    """How much a state costs, which is what colors its chip. Empty for none."""
    if state in BAD:
        return "bad"
    if state in WARN:
        return "warn"
    return "unknown" if state == NOT_CHECKED else ""
