"""Extensions And Themes: what is installed, which version, and whether VPinFE runs it.

Names and versions only. A path to either says where a person keeps their files.
"""

from __future__ import annotations

import json
import logging

from common import extensions, i18n
from common.config import paths
from common.security import accepted
from common.support.sections import Section, SectionText

logger = logging.getLogger("vpinfe.common.support.collect.extensions")

UNKNOWN_VERSION = "unknown version"


def collect() -> SectionText:
    lines = ["### Extensions", ""] + (_extensions() or ["None installed"])
    lines += ["", "### Themes", ""] + (_themes() or ["None installed"])
    return SectionText.ok(lines)


def _extensions() -> list[str]:
    lines = []
    for record in extensions.registry().records():
        version = record.manifest.version if record.manifest else UNKNOWN_VERSION
        detail = f"- **{record.name}** {version}: {record.state}"
        reason = _reason(record)
        lines.append(f"{detail} ({reason})" if reason else detail)
    return lines


def _reason(record: extensions.Record) -> str:
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
