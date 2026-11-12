"""What a command may stand for, said beside the field somebody types it into.

The names are declared, so this is generated from that declaration rather than written
out twice. Beside the field and not in a manual: a name you have to already know to go
looking for is a name nobody uses.

Behind a disclosure whatever the count, with the count in the label. A context
offering no names draws nothing at all.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common import tokens
from common.i18n import t
from manager.kit import panel


def add_to(entries: list[Any], context: str, *, after: bool = True,
           note: str = "") -> None:
    """Put the names a command in this context may use under the fields they apply to.
    Without `after`, only the names known before the table starts."""
    available = tokens.offered(context, after=after)
    if not available:
        return
    note = note or t("manager.commands.names_note")

    def rows() -> None:
        with ui.element("div").classes("manager-token-list"):
            for one in available:
                ui.label(tokens.written(one.name)).classes("manager-token")
                says = tokens.stands_for(one)
                if one.after_only:
                    says = t("manager.commands.after_finished", says=says)
                ui.label(says).classes("manager-help")

    def draw() -> None:
        with panel.disclosure(t("manager.commands.names_title", count=len(available))) \
                .classes("manager-tokens"):
            with ui.column().classes("gap-1 pt-1"):
                ui.label(note).classes("manager-help")
                rows()

    entries.append((panel.ASIDE, draw))
