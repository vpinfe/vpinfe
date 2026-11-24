"""A tag as a chip, drawn the same way in a grid cell and on a panel.

Drawn twice and it cannot be drawn once: AG Grid renders a cell in the browser and a panel
is built on the server. Both answer to the constants here, and `tests/manager` asserts
they still agree.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from nicegui import ui

from common.games.tag_registry import COLORS, derived_color
from common.i18n import t
from manager.kit import dropdown

BOX = "manager-tags"
CHIP = "manager-tag"
DOT = "manager-tag-dot"
# Where the grid's drawing finds each tag's color and description.
LOOKS = "__vpinfeTagLooks"


def dot_class(color: str) -> str:
    return f"{DOT} {DOT}--{color if color in COLORS else 'gray'}"


def chip_class(color: str) -> str:
    return f"{CHIP} {CHIP}--{color if color in COLORS else 'gray'}"


def color_of(tag: str, looks: Mapping[str, Mapping[str, Any]]) -> str:
    return str((looks.get(tag) or {}).get("color") or "") or derived_color(tag)


def draw(tags: Sequence[str], looks: Mapping[str, Mapping[str, Any]]) -> None:
    with ui.element("span").classes(BOX):
        for tag in tags:
            look = looks.get(tag) or {}
            color = color_of(tag, looks)
            with ui.element("span").classes(chip_class(color)) as chip:
                ui.element("span").classes(dot_class(color))
                ui.label(tag)
            if look.get("description"):
                chip.tooltip(str(look["description"]))


def swatches(chosen: str, derived: str, on_pick: Callable[[str], Any]
             ) -> Callable[[str], None]:
    """Automatic, wearing `derived`, then every color; `chosen` is ringed, "" for
    Automatic. The answer moves the ring to another color without drawing again."""
    buttons: dict[str, ui.button] = {}
    with ui.element("div").classes("manager-fact-edit manager-swatches"):
        for color, said in (("", t("manager.tags.automatic")),
                            *((one, t(f"manager.tags.color.{one}")) for one in COLORS)):
            with ui.button(on_click=lambda _e=None, c=color: on_pick(c)) \
                    .props("flat round dense") \
                    .classes("manager-swatch"
                             + (" manager-swatch--auto" if not color else "")
                             + (" manager-swatch--on" if color == chosen else "")) \
                    .tooltip(said) as button:
                ui.element("span").classes(dot_class(color or derived))
            buttons[color] = button

    def ring(color: str) -> None:
        for one, button in buttons.items():
            if one == color:
                button.classes("manager-swatch--on")
            else:
                button.classes(remove="manager-swatch--on")

    return ring


class Picker(dropdown.Select):
    """Tags to choose from, each drawn as its chip.

    `adds` lets a tag be typed in that nothing carries yet. `props.opt` is a bare string
    for the moment between typing one and the server's answer, so the slots read both.

    NiceGUI remounts a template slot whenever the select renders, and a press that
    focuses the field makes it render: without `mousedown.prevent` the chip under the
    pointer is replaced before the button comes up, and its remove never fires.
    """

    SELECTED = f"""
        <q-chip dense removable :tabindex="props.tabindex" class="{CHIP}"
                :class="props.opt.tone || '{CHIP}--gray'"
                @mousedown.prevent @remove="props.removeAtIndex(props.index)">
          <span :class="props.opt.dot || '{DOT} {DOT}--gray'"></span>
          {{{{ typeof props.opt === 'string' ? props.opt : props.opt.label }}}}
          <q-tooltip v-if="props.opt.help">{{{{ props.opt.help }}}}</q-tooltip>
        </q-chip>
    """
    OPTION = """
        <q-item v-bind="props.itemProps">
          <q-item-section side><span :class="props.opt.dot"></span></q-item-section>
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
          </q-item-section>
          <q-item-section side v-if="props.opt.count != null">
            <q-item-label caption>{{ props.opt.count }}</q-item-label>
          </q-item-section>
          <q-tooltip v-if="props.opt.help" class="manager-menu-tip"
                     anchor="center right" self="center left">
            {{ props.opt.help }}
          </q-tooltip>
        </q-item>
    """

    def __init__(self, options: Sequence[str], *, value: Sequence[str],
                 looks: Mapping[str, Mapping[str, Any]], adds: bool = False,
                 counts: Mapping[str, int] | None = None,
                 look: str = dropdown.FIELD) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.looks = dict(looks)
        self.counts = dict(counts or {})
        super().__init__(list(options), multiple=True, value=list(value),
                         with_input=True, new_value_mode="add-unique" if adds else None,
                         look=look)
        self.add_slot("selected-item", self.SELECTED)
        self.add_slot("option", self.OPTION)

    def _dressed(self, option: dict[str, Any]) -> dict[str, Any]:
        tag = str(option.get("label") or "")
        color = color_of(tag, self.looks)
        return {**option, "dot": dot_class(color), "tone": f"{CHIP}--{color}",
                "help": str((self.looks.get(tag) or {}).get("description") or ""),
                "count": self.counts.get(tag)}

    def _update_options(self) -> None:
        super()._update_options()
        self._props["options"] = [self._dressed(one) for one in self._props["options"]]

    def _value_to_model_value(self, value: Any) -> Any:
        return [self._dressed(one) for one in super()._value_to_model_value(value) or []]


def install(looks: Mapping[str, Mapping[str, Any]]) -> None:
    held = {name: {"color": str(one.get("color") or ""),
                   "description": str(one.get("description") or "")}
            for name, one in looks.items()}
    ui.run_javascript(f"window.{LOOKS} = {json.dumps(held)};")


# A tag's looks in a grid cell, for the chips drawing in `renderers`.
LOOK = (
    "tag => {"
    f" const look = (window.{LOOKS} || {{}})[tag] || {{}};"
    f" const color = {json.dumps(list(COLORS))}.includes(look.color) ? look.color : 'gray';"
    f" return {{chip: '{CHIP} {CHIP}--' + color, dot: '{DOT} {DOT}--' + color,"
    " tip: look.description || ''}; }"
)
