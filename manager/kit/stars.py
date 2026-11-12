
"""Five stars with half stars, one control, wherever a rating is set.

A rating is 0 to 10, a half star a step. A click on a star sets that many whole stars, and
a click on the star that is already the last whole one makes it a half star; the next
click makes it whole again. Left and right step a half star at a time. Only the clear
takes a rating back to none.

Drawn twice and it cannot be drawn once: a grid cell is rendered by AG Grid in the
browser, a panel row is built from elements on the server. So both live here, answering
to the same constants, and `tests/manager` asserts they still agree on what they draw.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.kit import grid
from manager.kit.on_page import on_page

MOST = 5
HALVES = MOST * 2
# The character *is* the control. In a tooltip it measures zero and cannot be clicked,
# and a scripted click passes on it anyway - `docs/conventions.md` has why.
CLEAR = "×"

BOX = "manager-stars"
STAR = "manager-star"
LIT = "manager-star--on"
HALF = "manager-star--half"
CLEAR_CLASS = "manager-star-clear"
SAVED_CLASS = "manager-stars-saved"
READ_CLASS = "manager-stars--read"
NUMBER_CLASS = "manager-stars-number"

# Set by a key press, read by the next drawing of the same stars: a panel is redrawn after
# every write, and a redraw that dropped focus would end a held key after one step.
KEEP_FOCUS = "window.__hubStarsFocus"

# One delegated listener, not a handler per cell: a renderer runs again on every scroll
# and NiceGUI strips inline handlers off raw HTML. Capture phase, because AG Grid's own
# handler sits between the cell and the document.
CLICK_JS = f"""
if (!window.__hubStars) {{
  window.__hubStars = true;
  document.addEventListener('click', (e) => {{
    const star = e.target.closest && e.target.closest('.{BOX} [data-value]');
    if (!star) return;
    e.stopPropagation();
    const box = star.closest('.{BOX}');
    const whole = Number(star.dataset.value);
    const value = whole && Number(box.dataset.rating) === whole ? whole - 1 : whole;
    emitEvent('hub_rate', {{game: box.dataset.game, table: box.dataset.table, value}});
  }}, true);
}}
"""


def shown(halves: int) -> str:
    """A rating in stars as a person says it: 3, 3\u00bd, or \u00bd."""
    if not halves % 2:
        return str(halves // 2)
    return f"{halves // 2 or ''}\u00bd"


def clicked(held: int, n: int) -> int:
    """The rating after a click on star `n`: whole, or half where it already was whole."""
    return n * 2 - 1 if held == n * 2 else n * 2


def stepped(held: int, direction: int) -> int:
    """The rating a half star up or down. Never below a half star, and never none: the
    clear is the way back."""
    if not held and direction < 0:
        return 0
    return min(HALVES, max(1, held + direction))


def _state(n: int, held: int) -> str:
    """The classes a star wears when the rating is `held`: lit for a whole star, half for
    the left half only."""
    if held >= n * 2:
        return f" {LIT}"
    return f" {HALF}" if held == n * 2 - 1 else ""


def _of_five(halves: int) -> str:
    return t("manager.stars.5", n=shown(halves))


def _tip(n: int, held: int) -> str:
    """What star `n` stands for now: a half star says its half, any other says its own number."""
    return _of_five(n * 2 - 1 if held == n * 2 - 1 else n * 2)


class _Stars:
    """The panel's five stars, the value beside them and the clear, as one element tree.

    `click` gets a star's number and `key` a step of one half; `clear` is the way back to
    none. Painting is the caller's: a panel is redrawn by its owner, a phone paints at the tap.
    """

    def __init__(self, value: int, click: Callable[[int], Any],
                 key: Callable[[int], Any], clear: Callable[[], Any]) -> None:
        self.box = ui.element("div").classes(BOX).props(
            'tabindex="0" role="slider" aria-valuemin="0" aria-valuemax="10"')
        with self.box:
            self.stars = [
                ui.element("span").classes(f"{STAR}{_state(n, value)}")
                .on("click", lambda _, n=n: click(n))
                for n in range(1, MOST + 1)]
            self.tips = []
            for n, star in enumerate(self.stars, 1):
                with star:
                    self.tips.append(ui.tooltip(_tip(n, value)))
            self.number = ui.label().classes(NUMBER_CLASS)
            self.clear = ui.label(CLEAR).classes(CLEAR_CLASS) \
                .on("click", lambda _: clear()) \
                .tooltip(t("manager.stars.clear_rating"))
        for name, step in (("left", -1), ("right", 1)):
            self.box.on(f"keydown.{name}.prevent", lambda _, step=step: key(step),
                        js_handler=f"(e) => {{ {KEEP_FOCUS} = true; emit(); }}")
        self.paint(value)
        ui.run_javascript(
            f"if ({KEEP_FOCUS}) {{ {KEEP_FOCUS} = false; setTimeout(() => "
            f"document.getElementById('c{self.box.id}')?.focus(), 50); }}")

    def paint(self, held: int) -> None:
        for n, star in enumerate(self.stars, 1):
            star.classes(remove=f"{LIT} {HALF}")
            star.classes(add=_state(n, held).strip())
        for n, tip in enumerate(self.tips, 1):
            tip.set_text(_tip(n, held))
        self.number.set_text(shown(held) if held else "")
        self.number.set_visibility(held > 0)
        self.clear.set_visibility(held > 0)
        self.box.props(f'aria-valuenow="{held}" aria-valuetext="{_of_five(held)}"')


def renderer(subject: str, *, on_table: bool = False) -> str:
    """The grid's cell, as JavaScript. Only a rated row carries the clear, or an unrated
    library reads as a column of dismissals.

    `on_table` draws the game's rating on a table's row. A table's own rating is drawn
    only on a row whose game has a choice of tables (`own_marks`)."""
    game = "(row.game_id || '')" if on_table or subject == "table" else "row.id"
    table = "row.id" if subject == "table" else "''"
    skip = " if (row.own_marks === false) return '';" if subject == "table" else ""
    lit = f"held >= n * 2 ? ' {LIT}' : held === n * 2 - 1 ? ' {HALF}' : ''"
    return (
        "params => {"
        " const row = params.data || {}; const held = Number(params.value) || 0;"
        + skip
        + f" let out = '<span class=\"{BOX}\" data-game=\"' + {game}"
        + f" + '\" data-table=\"' + {table} + '\" data-rating=\"' + held + '\">';"
        + f" for (let n = 1; n <= {MOST}; n++) {{"
        + f"  out += '<span class=\"{STAR}' + ({lit})"
        + "  + '\" data-value=\"' + n * 2 + '\" title=\"' + (held === n * 2 - 1 ? n - 0.5 : n)"
        + " + ' of 5\"></span>';"
        + " }"
        + f" if (held) {{ out += '<span class=\"{CLEAR_CLASS}\" data-value=\"0\"'"
        + f"  + ' title=\"Clear rating\">{CLEAR}</span>'; }}"
        + " return out + '</span>'; }"
    )


def draw(value: int, on_pick: Callable[[int], Any]) -> Callable[[], None]:
    """The panel's, as elements. A star sets and the clear unsets - one control doing
    both made a click on the lit star mean either, depending on a number the reader was
    not looking at. The owner redraws after a write, so nothing is painted here."""
    def step(direction: int) -> Any:
        now = stepped(value, direction)
        return on_pick(now) if now != value else None

    def build() -> None:
        _Stars(value, lambda n: on_pick(clicked(value, n)), step, lambda: on_pick(0))

    return build


def static(value: int) -> None:
    """A rating to read, with no control: the list row a tap on anywhere belongs to."""
    with ui.element("div").classes(f"{BOX} {READ_CLASS}") \
            .props(f'role="img" aria-label="{t("manager.stars.5", n=shown(value))}"'):
        for n in range(1, MOST + 1):
            ui.element("span").classes(f"{STAR}{_state(n, value)}")


def rated(value: int, save: Callable[[int], Awaitable[None]]) -> Callable[[], None]:
    """The panel's stars where a write can be slow or refused: a phone.

    A tap lights the stars up to it on the spot and the write follows behind it; **Saved** shows
    beside the stars when it lands, and a refusal puts them back as they were and says
    why. Nothing is redrawn, so the screen the stars sit on keeps its place. `save` raises
    to refuse; saves run one at a time in tap order, so the last tap is what the server
    ends up holding.
    """
    def build() -> None:
        held = {"value": value, "shown": value, "tap": 0}
        one_at_a_time = asyncio.Lock()

        @on_page
        async def pick(n: int) -> None:
            held["tap"] += 1
            tap = held["tap"]
            saved.set_visibility(False)
            held["shown"] = n
            control.paint(n)
            try:
                async with one_at_a_time:
                    await save(n)
                    held["value"] = n
            except Exception as exc:
                if tap == held["tap"]:
                    held["shown"] = held["value"]
                    control.paint(held["value"])
                ui.notify(t("manager.stars.save_failed"), caption=why(exc),
                          type="negative", position="top")
                return
            if tap == held["tap"]:
                saved.set_visibility(True)

        def step(direction: int) -> Any:
            now = stepped(held["shown"], direction)
            return pick(now) if now != held["shown"] else None

        control = _Stars(value, lambda n: pick(clicked(held["shown"], n)), step,
                         lambda: pick(0))
        with control.box:
            saved = ui.label(t("manager.stars.saved")).classes(SAVED_CLASS)
            saved.set_visibility(False)

    return build


def game_rater(held: list[dict[str, Any]], rows_by_id: dict[str, Any],
               grid_of: Callable[[], Any], library: Any,
               game_field: str = "rating",
               then: Callable[[set[str]], Any] | None = None,
               ) -> Callable[[list[str], int], Any]:
    """Rate games, then repaint every row of them that changed, under `game_field`.

    A failed write says so and stops: the games saved before it stay repainted, and the
    answer is whether every one was saved. Written through `library`, which holds the rows
    the other grid is drawn from next. `grid_of` rather than the grid, because a caller
    wires this before the grid exists.

    `then` is told which games were saved, after the grid is repainted, so the panel
    beside the grid can show the new rating too.
    """
    @on_page
    async def rate_games(game_ids: list[str], value: int) -> bool:
        saved: set[str] = set()
        try:
            for game_id in game_ids:
                await run.io_bound(library.set_game_rating, game_id, value)
                saved.add(game_id)
        except Exception as exc:
            ui.notify(t("manager.stars.save_failed"), caption=why(exc),
                      type="negative")
        changed = [{**row, game_field: value} for row in held
                   if (row.get("game_id") or row["id"]) in saved]
        if changed:
            grid.transact(grid_of(), held, {"update": changed}, rows_by_id)
        if saved and then is not None:
            await _told(then, saved)
        return len(saved) == len(game_ids)

    return rate_games


async def _told(then: Callable[[set[str]], Any], games: set[str]) -> None:
    answer = then(games)
    if inspect.isawaitable(answer):
        await answer


def rating_handler(held: list[dict[str, Any]], rows_by_id: dict[str, Any],
                   grid_of: Callable[[], Any], library: Any,
                   game_field: str = "rating",
                   then: Callable[[set[str]], Any] | None = None,
                   ) -> Callable[[Any], Any]:
    """One handler for both grids: write it, then repaint only the rows that changed - a
    whole-grid refresh costs the scroll position and the selection for one number.

    A game's rating goes through `game_rater`; a table's repaints its own row.
    """
    rate_games = game_rater(held, rows_by_id, grid_of, library, game_field, then)

    @on_page
    async def rate(event: Any) -> None:
        args = event.args if isinstance(event.args, dict) else {}
        game_id = str(args.get("game") or "")
        table_id = str(args.get("table") or "")
        value = int(args.get("value") or 0)
        if not game_id:
            return
        if not table_id:
            await rate_games([game_id], value)
            return
        try:
            await run.io_bound(library.set_table_rating, game_id, table_id, value)
        except Exception as exc:
            ui.notify(t("manager.stars.save_failed"), caption=why(exc),
                      type="negative")
            return
        row = rows_by_id.get(table_id)
        if row is not None:
            grid.transact(grid_of(), held, {"update": [{**row, "rating": value}]},
                          rows_by_id)
        if then is not None:
            await _told(then, {game_id})

    return rate
