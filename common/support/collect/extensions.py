"""Extensions And Themes: what is installed, where each extension came from, and whether
VPinFE runs it.

Names, versions, repositories and fingerprints. A path to an extension or a theme says where
a person keeps their files, so none is given.
"""

from __future__ import annotations

import json
import logging

from common import extensions, i18n
from common.config import paths
from common.extensions import host
from common.security import accepted
from common.support.sections import Section, SectionText

logger = logging.getLogger("vpinfe.common.support.collect.extensions")

UNKNOWN_VERSION = "unknown version"
FINGERPRINT_LENGTH = 16

ORIGINS = {
    accepted.SHIPPED: "included with VPinFE",
    accepted.OFFICIAL: "from the Extension List",
    accepted.CHANGED: "changed since VPinFE put it there",
    accepted.REPLACES: "unofficial, uses the name of an official extension",
    accepted.IN_FOLDER: "unofficial",
}


def collect() -> SectionText:
    found = _records()
    lines = ["### Extensions", ""]
    if found:
        lines += [_unofficial(found), ""]
    lines += _extensions(found) or ["None installed"]
    lines += ["", "### Themes", ""] + (_themes() or ["None installed"])
    return SectionText.ok(lines)


def _records() -> list[host.Record]:
    """What this run loaded, or the folders themselves when it loaded none: the
    command-line package is made by a process that starts no extension."""
    return extensions.records() or extensions.inventory()


def _unofficial(found: list[host.Record]) -> str:
    named = [f"{one.name} ({'on' if _is_on(one) else 'off'})"
             for one in found if not one.official]
    return f"Unofficial extensions: {', '.join(named) if named else 'none'}"


def _extensions(found: list[host.Record]) -> list[str]:
    lines = []
    for record in found:
        version = record.manifest.version if record.manifest else UNKNOWN_VERSION
        detail = f"- **{record.name}** {version}: {_state(record)}"
        reason = _reason(record)
        lines.append(f"{detail} ({reason})" if reason else detail)
        lines.append(f"  - {_provenance(record)}")
    return lines


def _is_on(record: host.Record) -> bool:
    """Running, or, for a record nothing started (the command-line package starts none),
    switched on. An extension waiting to be trusted is off however its switch is set."""
    if record.state == host.PENDING:
        return record.enabled
    return record.state == host.LOADED


def _state(record: host.Record) -> str:
    if record.state == host.PENDING:
        return "on" if record.enabled else "off"
    return record.state


def _provenance(record: host.Record) -> str:
    repository, release = record.repository_and_release
    return (f"{ORIGINS.get(record.origin, record.origin).capitalize()}. "
            f"Repository {repository or 'not named'}, release {release or 'not named'}, "
            f"fingerprint {record.fingerprint[:FINGERPRINT_LENGTH] or 'none'}")


def _reason(record: host.Record) -> str:
    if not record.why:
        return ""
    values = {key: ", ".join(value) if isinstance(value, tuple) else value
              for key, value in record.why_values.items()}
    try:
        return i18n.t_source(record.why, **values)
    except (KeyError, ValueError, IndexError):
        return record.why


def _themes() -> list[str]:
    lines = []
    for folder in accepted.theme_folders():
        state = "accepted" if accepted.theme_runs(folder) else "not accepted"
        lines.append(f"- **{folder}** {_theme_version(folder)}: {state}")
    return lines


def _theme_version(folder: str) -> str:
    try:
        manifest = json.loads((paths.THEMES_DIR / folder / "manifest.json")
                              .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN_VERSION
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return str(version) if version else UNKNOWN_VERSION


SECTION = Section("extensions", "Extensions And Themes", 70, collect)
