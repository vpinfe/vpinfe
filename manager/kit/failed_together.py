"""An act over several items says what failed once, after the last of them."""

from __future__ import annotations

from nicegui import ui

from common.i18n import t


class Failures:
    """What an act over a selection could not do, collected as it goes.

    The loop hands each failure to `add` and carries on; `say` is called once after the
    loop. A walk that asks about each item answers each step as it is taken and does not
    use this.
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []

    def __len__(self) -> int:
        return len(self._items)

    def add(self, name: str, reason: str) -> None:
        """`reason` is already in the catalog's words, from `why()`."""
        self._items.append((name, reason))

    def say(self, key: str) -> None:
        """Nothing when nothing failed. `key` is a plural entry: its `one` form names the
        item as `{name}` and its `other` form counts them as `{count}`; each distinct
        reason is said once, under it."""
        if not self._items:
            return
        ui.notify(t(key, count=len(self._items), name=self._items[0][0]),
                  caption="; ".join(dict.fromkeys(reason for _, reason in self._items
                                                  if reason)),
                  type="negative")
