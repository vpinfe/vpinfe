"""The one dropdown. Every select in the Manager is this class or a subclass of it.

`FIELD` is the value in a row of a panel or dialog: a bordered box one row tall, the
text centered in it. `OUTLINED` is a control standing on its own - a toolbar's picker, a
box that fills a panel - at Quasar's outlined height.

`ui.select` is not called anywhere else in `manager/`; a call site names a look and never
writes the props. Both looks are checked by `tests/manager/test_every_dropdown_is_one.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nicegui import ui

from common.i18n import number

FIELD = "field"
OUTLINED = "outlined"

MARK = "manager-select"
_PROPS = {FIELD: "dense borderless options-dense behavior=menu",
          OUTLINED: "dense outlined options-dense behavior=menu"}
_CLASSES = {FIELD: "manager-edit-field manager-edit-select", OUTLINED: ""}
_MANY = "manager-edit-many"


class Select(ui.select):
    """A `ui.select` in one of the two looks.

    `multiple` draws what is chosen as chips, and a chosen option leaves the open list:
    the x on its chip is the only way out.

    `sections` maps an option's value to the heading it sits under in the open list. The
    options must already be in the order the headings run; a heading is drawn before the
    first option of each run and cannot be chosen. Not for `multiple`, and not with typed
    search, which filters the headings as if they were options.

    `counts` maps an option's value to a number drawn in dim grey at the end of its row, in
    the open list and in the box, so the label stays the name alone. `shown` maps a value to
    the wording the closed box uses in place of the label, for a name that needs more than
    the open list's headings give it. Neither is for `multiple`.

    `tags` maps an option's value to a word set quietly at the end of its row in the open
    list only, for the choice the program makes when none is. Not for `multiple`.
    """

    LISTED = """
        <q-item-label v-if="props.opt.section" header class="manager-group">
          {{ props.opt.label }}
        </q-item-label>
        <q-item v-else v-bind="props.itemProps">
          <q-item-section><q-item-label>{{ props.opt.label }}</q-item-label></q-item-section>
          <q-item-section v-if="props.opt.count" side>
            <q-item-label class="manager-select-count">{{ props.opt.count }}</q-item-label>
          </q-item-section>
          <q-item-section v-if="props.opt.tag" side>
            <q-item-label class="manager-select-tag">{{ props.opt.tag }}</q-item-label>
          </q-item-section>
        </q-item>
    """
    BOXED = """
        <span>{{ props.opt.shown || props.opt.label }}</span>
        <span v-if="props.opt.count" class="manager-select-count">{{ props.opt.count }}</span>
    """

    def __init__(self, *args: Any, look: str = FIELD,
                 sections: Mapping[Any, str] | None = None,
                 counts: Mapping[Any, int] | None = None,
                 shown: Mapping[Any, str] | None = None,
                 tags: Mapping[Any, str] | None = None, **kwargs: Any) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self._sections = dict(sections or {})
        self._counts = dict(counts or {})
        self._shown = dict(shown or {})
        self._tags = dict(tags or {})
        super().__init__(*args, **kwargs)
        self.classes(f"{MARK} {_CLASSES[look]}").props(_PROPS[look])
        if kwargs.get("multiple"):
            self.props("use-chips")
            if look == FIELD:
                self.classes(_MANY)
        if self._sections or self._counts or self._tags:
            self.add_slot("option", self.LISTED)
        if self._counts or self._shown:
            self.add_slot("selected-item", self.BOXED)

    def _annotated(self, one: dict[str, Any]) -> dict[str, Any]:
        """An option, or the value shown in the box, with the count and wording the slots read."""
        value = self._values[one["value"]]
        if value in self._counts:
            one["count"] = number(self._counts[value])
        if value in self._shown:
            one["shown"] = self._shown[value]
        return one

    def _value_to_model_value(self, value: Any) -> Any:
        chosen = super()._value_to_model_value(value)
        return chosen if self.multiple or chosen is None else self._annotated(chosen)

    def _update_options(self) -> None:
        super()._update_options()
        if self.multiple:
            chosen = self.value or []
            self._props["options"] = [one for one in self._props["options"]
                                      if self._values[one["value"]] not in chosen]
        for one in (one for one in self._props["options"] if not one.get("section")):
            self._annotated(one)
            if (tag := self._tags.get(self._values[one["value"]])) is not None:
                one["tag"] = tag
        if self._sections:
            options: list[dict[str, Any]] = []
            heading = None
            # A change of value inside `super()._update_options` can draw the list once already.
            for one in (one for one in self._props["options"] if not one.get("section")):
                under = self._sections.get(self._values[one["value"]])
                if under is not None and under != heading:
                    options.append({"label": under, "value": f"section:{under}",
                                    "disable": True, "section": True})
                    heading = under
                options.append(one)
            self._props["options"] = options
