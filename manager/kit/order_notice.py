"""Saying which tables could not be brought to the DMD order their backglass file calls for."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from nicegui import ui

from common.i18n import t


def lines(failed: Iterable[Mapping[str, Any]]) -> str:
    """One line a table: the file it plays and why its settings could not be written."""
    return "\n".join(
        t("manager.order.failed.line", name=str(one.get("name") or one.get("table") or ""),
          reason=str(one.get("reason") or "")) for one in failed)


def tell(failed: Iterable[Mapping[str, Any]] | None) -> None:
    """A warning naming each table in `failed`, or nothing where there are none."""
    found = list(failed or ())
    if found:
        ui.notify(t("manager.order.failed", count=len(found)), caption=lines(found),
                  type="warning", multi_line=True)
