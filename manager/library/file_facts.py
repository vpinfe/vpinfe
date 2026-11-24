"""The one line under a file's name that tells two files of a kind apart: size, shape, running
time, format and date. Drawn by the Games pane's slot and by Discovery's held card."""

from __future__ import annotations

from typing import Any

from common.i18n import t
from manager.kit import when
from manager.library import mediasource


def media_spec(detail: dict[str, Any]) -> str:
    """Size, shape, running time and date on one line - what tells two files of a kind
    apart when both look right in a thumbnail."""
    parts = []
    if detail.get("width") and detail.get("height"):
        parts.append(t("manager.workbench.width_by_height", width=detail["width"],
                       height=detail["height"]))
    if detail.get("duration_s"):
        parts.append(running_time(float(detail["duration_s"])))
    name = str(detail.get("file") or "")
    if detail.get("format"):
        parts.append(str(detail["format"]))
    elif "." in name:
        parts.append(name.rsplit(".", 1)[1].upper())
    size = mediasource._size(detail.get("size_bytes"))
    if size:
        parts.append(size)
    if detail.get("modified"):
        parts.append(when.ago(detail["modified"]))
    return " · ".join(parts)


def running_time(seconds: float) -> str:
    """As a player shows it. Never 0:00 - a sound that is there runs for something."""
    minutes, rest = divmod(max(1, round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{rest:02}" if hours else f"{minutes}:{rest:02}"


def asset_spec(detail: dict[str, Any]) -> str:
    parts = []
    if detail.get("folder"):
        parts.append(t("manager.workbench.files_in_folder", count=int(detail.get("files") or 0)))
    elif detail.get("format"):
        parts.append(str(detail["format"]))
    size = mediasource._size(detail.get("size_bytes"))
    if size:
        parts.append(size)
    if detail.get("modified"):
        parts.append(when.ago(detail["modified"]))
    return " · ".join(parts)
