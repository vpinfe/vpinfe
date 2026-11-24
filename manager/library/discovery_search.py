"""Discovery's search box: the one on the tab row, and any page that wants the same.

Typing lists the authors, makers, themes and games it could mean, and a pick opens that
page (`discovery_ask.Suggest`). The box is drawn before a tab has read the catalog, so the
tab hands it what to suggest from with `feed`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

from common.i18n import t
from common.online import vps_find
from manager.kit import panel
from manager.library import discovery_ask

# Where the tab row's box is kept in the page state while its tab is drawn.
KEY = "discovery_search"

Choose = Callable[[str, dict[str, Any]], Awaitable[None]]


class SearchBox:
    """The box, drawn into the current container. Until it is fed it lists nothing."""

    def __init__(self, text: str = "") -> None:
        """`text` is words the box opens holding, which narrow the tab and list no
        suggestions."""
        self.people: list[dict[str, Any]] = []
        self.games: list[dict[str, Any]] = []
        self.choose: Choose | None = None
        self._narrow: Callable[[str], Any] | None = None
        with ui.element("div").classes("manager-suggest-anchor manager-search-slot") as self.root:
            self.input = panel.search(t("manager.discovery.search")) \
                .classes(add="manager-search", remove="w-64")
            self.input.value = self._carried = text
            self.suggest = discovery_ask.Suggest(self.input, self._find, self._pick)
        self.input.on_value_change(self._typed)

    def _find(self, text: str) -> dict[str, list[dict[str, Any]]]:
        return vps_find.find(text, self.people, self.games)

    async def _pick(self, group: str, one: dict[str, Any]) -> None:
        if self.choose is not None:
            await self.choose(group, one)

    def _typed(self) -> None:
        if self._narrow is not None:
            self._narrow(self.input.value or "")

    @property
    def gone(self) -> bool:
        """Drawn over: the page has been built again and this box is no longer on it."""
        return bool(self.root.is_deleted)

    def feed(self, people: list[dict[str, Any]], games: list[dict[str, Any]],
             choose: Choose, *, narrow: Callable[[str], Any] | None = None) -> None:
        """Suggest from `people` and `games`, and run `choose(group, one)` on a pick. The
        lists are kept, not copied, so a tab that reads again in place needs no new feed.
        `narrow(text)` runs on every change of the text, for a tab that filters by it; a
        feed without one leaves the box narrowing nothing. Text typed before the feed is
        narrowed and suggested from at once."""
        self.people, self.games, self.choose = people, games, choose
        self._narrow = narrow
        if text := self.input.value or "":
            if narrow is not None:
                narrow(text)
            if text != self._carried:
                self.suggest.typed(text)


def feeder(state: dict[str, Any]) -> Callable[..., None]:
    """The tab row's box as it is now, as the function that feeds it. A tab calls this
    before it first waits, so that what it feeds when it has read is the box of the page
    it was drawn for, not of one drawn since. A page without a box, or one drawn over, has
    nothing to feed. Called as `feed(people, games, choose, narrow=None)`."""
    box = state.get(KEY)

    def feed(people: list[dict[str, Any]], games: list[dict[str, Any]], choose: Choose,
             *, narrow: Callable[[str], Any] | None = None) -> None:
        if box is not None and not box.gone:
            box.feed(people, games, choose, narrow=narrow)

    return feed
