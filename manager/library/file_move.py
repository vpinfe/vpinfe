"""Moving a game's file to its tables, and a table's file to the game: the menu items, what
they ask and what they say.

The files move in `common/games/file_move.py`; every move there can be put back with the
token it answers, which is what Undo here runs.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from nicegui import ui

from common.failures import why
from common.games import file_move as core
from common.i18n import t
from manager.kit import game_tables, offload, panel, undo
from manager.kit.on_page import on_page
from manager.library import move_picker, move_plan
from manager.library.move_picker import joined, noun

if TYPE_CHECKING:
    from manager.library.file_acts import File

logger = logging.getLogger("vpinfe.manager.library.file_move")

After = Callable[[list[str]], Awaitable[Any]]
# A table's file to move to the game: its game, family, kind and table.
Item = tuple[str, str, str, str]


def _folder_of(game: dict[str, Any]) -> str:
    return re.split(r"[\\/]", str(game.get("folder") or "").rstrip("\\/"))[-1]


def _is_game_file(one: File, family: str) -> bool:
    """A file the game serves its tables with, or for a kind only the folder-named table
    reads, that table's own."""
    if not one.present or not core.movable(family, one.kind):
        return False
    if family == core.ASSET and one.kind in core.TABLE_ONLY:
        folder = _folder_of(one.game)
        return bool(folder and str(one.row.get("binding") or "") == "game"
                    and PurePosixPath(one.path.replace("\\", "/")).stem.lower() == folder.lower())
    if family == core.ASSET:
        return str(one.row.get("binding") or "") == "game"
    return str(one.row.get("via") or "") == "game"


def _is_table_file(one: File, family: str) -> bool:
    if not one.present or not one.table or not core.movable(family, one.kind):
        return False
    if family == core.ASSET:
        return (core.goes_to_game(family, one.kind)
                and str(one.row.get("binding") or "") == "table")
    return str(one.row.get("via") or "") == "table"


def _eligible(library: Any, one: File) -> int | None:
    """How many tables of the game could be chosen, or None where its tables have not
    been read yet."""
    tables = library.tables.get(one.game_id)
    if tables is None:
        return None
    return sum(1 for table in tables if not game_tables.named_as_folder(table, one.game))


def verbs_for(library: Any, files: list[File], family: str, after: After) -> list[panel.Verb]:
    """Choose Tables for the game's files in these, and Move to Game for the tables'."""
    game_files = [one for one in files if _is_game_file(one, family)]
    table_files = [one for one in files if _is_table_file(one, family)]
    offered: list[panel.Verb] = []
    if game_files:
        offered.append(_choose_verb(library, game_files, family, after))
    if table_files:
        offered.append(_to_game_verb(library, table_files, family, after))
    return offered


def card_buttons(library: Any, row: dict[str, Any], game: dict[str, Any], family: str,
                 after: After) -> None:
    """The move buttons for the one file a panel card shows, beside Replace and Remove."""
    from manager.kit import verbs
    from manager.library.file_acts import File

    for verb in verbs_for(library, [File(row, game)], family, after):
        panel.action(verb.label, verb.run, icon=verbs.SHARE, hint=verb.hint,
                     enabled=verb.run is not None)()


def _choose_verb(library: Any, game_files: list[File], family: str,
                 after: After) -> panel.Verb:
    games = list(dict.fromkeys(one.game_id for one in game_files))
    first = game_files[0]
    count = _eligible(library, first)
    if len(games) > 1:
        return panel.Verb(t("manager.file_move.choose"), hint=t("manager.file_move.two_games"))
    if not int(first.game.get("table_count") or 0):
        return panel.Verb(t("manager.file_move.choose"), hint=t("manager.file_move.no_tables"))
    if count == 0:
        return panel.Verb(t("manager.file_move.choose"), hint=t("manager.file_move.no_other"))
    kinds = list(dict.fromkeys((family, one.kind) for one in game_files))

    @on_page
    async def choose() -> None:
        await choose_tables(library, first.game_id, first.game, kinds, after,
                            at_once=count == 1)

    if count == 1:
        return panel.Verb(t("manager.file_move.one"), choose)
    return panel.Verb(t("manager.file_move.choose"), choose)


def _to_game_verb(library: Any, table_files: list[File], family: str,
                  after: After) -> panel.Verb:
    items: list[Item] = [(one.game_id, family, one.kind, one.table) for one in table_files]
    slots = [item[:3] for item in items]
    if len(set(slots)) < len(slots):
        return panel.Verb(t("manager.file_move.to_game"), hint=t("manager.file_move.keep_one"))

    @on_page
    async def press() -> None:
        await to_game(library, items, after_items(after, items))

    return panel.Verb(t("manager.file_move.to_game"), press)


def after_items(after: After, items: list[Item]) -> Callable[[], Awaitable[Any]]:
    games = list(dict.fromkeys(item[0] for item in items))

    async def done() -> None:
        await after(games)

    return done


# --- the moves --------------------------------------------------------------------


def _failed(exc: Exception) -> None:
    logger.warning("manager: a file move failed", exc_info=True)
    ui.notify(t("manager.workbench.move_failed"), caption=why(exc), type="negative")


async def _slots(library: Any, game_id: str, kinds: list[tuple[str, str]]) -> list[move_plan.Slot]:
    return [move_plan.Slot(family, kind, core.label_of(family, kind),
                           await offload.io(library.file_move_state, game_id, family, kind))
            for family, kind in kinds]


@on_page
async def choose_tables(library: Any, game_id: str, game: dict[str, Any],
                        kinds: list[tuple[str, str]], after: After, *,
                        at_once: bool = False) -> None:
    """Ask which tables are to have the game's files of these kinds and move them there.
    With one table to choose, no question is asked."""
    try:
        slots = await _slots(library, game_id, kinds)
    except Exception as exc:  # noqa: BLE001 - said, and nothing moved
        _failed(exc)
        return
    if any(one.state.get("playing") for one in slots):
        ui.notify(t("manager.file_move.playing"), type="warning")
        return
    slots = [one for one in slots if one.has_file]
    eligible = [row for row in slots[0].rows if not row["folder_named"]] if slots else []
    if not eligible:
        ui.notify(t("manager.file_move.no_other"), type="info")
        return
    if at_once and len(eligible) == 1 and not any(
            move_plan.outcome(one, [eligible[0]["id"]]).problem for one in slots):
        wanted = {move_picker.slot_key(one): [eligible[0]["id"]] for one in slots}
        changed = slots
    else:
        wanted = await move_picker.ask(slots) or {}
        changed = [one for one in slots if move_picker.slot_key(one) in wanted]
    if not wanted:
        return
    done: list[tuple[move_plan.Slot, dict[str, Any]]] = []
    try:
        for one in changed:
            result = await offload.io(library.move_file_to_tables, game_id, one.family,
                                      one.kind, wanted[move_picker.slot_key(one)])
            done.append((one, result))
    except Exception as exc:  # noqa: BLE001 - said, with what did move still undoable
        _failed(exc)
    if not done:
        return
    await after([game_id])
    said, caption, warn = to_tables_words([(one, result) for one, result in done])
    _offer(library, game_id, [result["undo"] for _one, result in done], said, caption, warn,
           after)


@on_page
async def to_game(library: Any, items: list[Item],
                  then: Callable[[], Awaitable[Any]]) -> None:
    """Make each table's file the game's, and say what that did."""
    done: list[tuple[str, str, dict[str, Any]]] = []
    try:
        for game_id, family, kind, table_id in items:
            result = await offload.io(library.move_file_to_game, game_id, family, kind,
                                      table_id)
            done.append((game_id, core.label_of(family, kind), result))
    except Exception as exc:  # noqa: BLE001 - said, with what did move still undoable
        _failed(exc)
    if not done:
        return
    await then()
    said, caption, warn = to_game_words([(label, result) for _game, label, result in done])
    reverse = [(game_id, str(result["undo"])) for game_id, _label, result in done]
    _offer_reverse(library, reverse, said, caption, warn, then)


def _offer(library: Any, game_id: str, tokens: list[str], said: str, caption: str,
           warn: bool, after: After) -> None:
    reverse = [(game_id, token) for token in tokens]

    async def then() -> None:
        await after([game_id])

    _offer_reverse(library, reverse, said, caption, warn, then)


def _offer_reverse(library: Any, reverse: list[tuple[str, str]], said: str, caption: str,
                   warn: bool, then: Callable[[], Awaitable[Any]]) -> None:
    async def put_back() -> None:
        for game_id, token in reversed(reverse):
            await offload.io(library.undo_file_move, game_id, token)
        await then()

    undo.offer(said, put_back, warn=warn, caption=caption)


# --- what is said -----------------------------------------------------------------


def to_tables_words(done: list[tuple[move_plan.Slot, dict[str, Any]]]) -> tuple[str, str, bool]:
    """What a move of the game's files to tables says: the line, the caption under it and
    whether it warns, which it does where a table is left without the file."""
    uses = list(dict.fromkeys(stem for _slot, result in done for stem in result["uses"]))
    kinds = [noun(slot.label) for slot, _result in done]
    lost = list(dict.fromkeys(stem for _slot, result in done for stem in result["lost"]))
    kept = any(result["replaced"] for _slot, result in done)
    stays = all(result["stays"] for _slot, result in done)
    notes: list[str] = []
    if len(done) == 1:
        kind = kinds[0]
        if stays:
            gained = list(dict.fromkeys(one["table"] for one in done[0][1]["replaced"]))
            said = t("manager.file_move.said.uses_one", table=gained[0], kind=kind) \
                if len(gained) == 1 \
                else t("manager.file_move.said.uses_many", count=len(gained), kind=kind)
        elif len(uses) > 1:
            said = t("manager.file_move.said.copied", kind=kind, count=len(uses))
        else:
            said = t("manager.file_move.said.moved_one", kind=kind, table=uses[0])
        if lost:
            notes.append(t("manager.file_move.said.lost_one", table=lost[0], kind=kind)
                         if len(lost) == 1
                         else t("manager.file_move.said.lost_many", count=len(lost),
                                kind=kind))
    else:
        said = t("manager.file_move.said.several", count=len(done))
        if lost:
            notes.append(t("manager.file_move.said.lost_several", kinds=joined(kinds),
                           tables=joined(lost)))
    if kept:
        notes.append(t("manager.file_move.said.kept"))
    return said, ". ".join(notes), bool(lost)


def to_game_words(done: list[tuple[str, dict[str, Any]]]) -> tuple[str, str, bool]:
    labels = [noun(label) for label, _result in done]
    results = [result for _label, result in done]
    changed = list(dict.fromkeys(stem for result in results for stem in result["changed_for"]))
    kept = any(result["replaced"] for result in results)
    if len(results) == 1:
        said = t("manager.file_move.said.to_game", kind=labels[0])
    else:
        said = t("manager.file_move.said.to_game_several", count=len(results))
    if not kept:
        return said, t("manager.file_move.said.every_table"), False
    notes = [t("manager.file_move.said.replaced_kept", kind=labels[0]) if len(results) == 1
             else t("manager.file_move.said.replaced_kept_several")]
    if changed:
        notes.append(t("manager.file_move.said.now_uses_one", table=changed[0])
                     if len(changed) == 1
                     else t("manager.file_move.said.now_uses_many", count=len(changed)))
    return said, ". ".join(notes), bool(changed)

