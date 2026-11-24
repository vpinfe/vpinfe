"""Hardware > Input: the binding table mounted over the input settings.

The table takes rows and what each is mapped to and writes nothing; this reads both from
the schema and the stored values, and sends what changes to the config.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from common.config import input_registry, setting_groups
from common.i18n import t
from manager.kit import panel
from manager.settings.binding_table import Row, Table, selectors

KEY = "hardware.input"

Write = Callable[[dict[str, dict[str, Any]]], Awaitable[bool]]


def table_rows(options: Sequence[dict],
               findings: Mapping[str, Sequence[Callable[[], None]]] | None = None) -> list[Row]:
    """A row per input setting, gathered under its heading in the order headings first appear."""
    groups: dict[str, list[dict]] = {}
    for option in options:
        groups.setdefault(str(option.get("group") or ""), []).append(option)
    return [Row(name=str(option["key"]),
                label=str(option.get("label") or option["key"]),
                group=str(option.get("group_label") or ""),
                defaults=tuple(selectors(option.get("default"))),
                holds=option.get("group") != setting_groups.DURING_PLAY,
                note=str(option.get("description") or ""),
                findings=tuple((findings or {}).get(str(option["key"]), ())))
            for group in groups.values() for option in group]


def rows(schema: Sequence[dict], values: Mapping[str, Any], write: Write, *,
         findings: Mapping[str, Sequence[Callable[[], None]]] | None = None,
         pads_when_heard: bool = False,
         keep_intro_open: bool = False) -> list[tuple[Any, Any]]:
    """The table as one row spanning the page, for a block to place. How Mapping Works is
    open until some action is mapped otherwise than VPinFE maps it, or always with
    `keep_intro_open`."""
    block = next((one for one in schema if one.get("name") == input_registry.SECTION), {})
    options = block.get("options") or []
    if not options:
        return []
    writable = bool(block.get("writable"))
    stored = values.get(input_registry.SECTION) or {}
    bound = {str(one["key"]): stored.get(one["key"], one.get("default")) for one in options}

    async def put(changes: dict[str, list[str]]) -> bool:
        return await write({input_registry.SECTION: changes})

    def draw() -> None:
        mapped = any(not input_registry.same_bindings(selectors(bound[str(one["key"])]),
                                                      selectors(one.get("default")))
                     for one in options)
        Table(table_rows(options, findings), bound, write=put, writable=writable,
              source=t("manager.binding_table.vpinfe_defaults"),
              pads_when_heard=pads_when_heard, how_open=keep_intro_open or not mapped)

    return [*([] if writable else [panel.intro(t("manager.settings.not_writable"))]),
            (panel.FULL, draw)]
