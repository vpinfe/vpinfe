"""The files VPS lists that an extension's online source can get.

A source is asked per kind and by VPS id alone, so a game that is not in the library has
offers too. Its rows are matched to the files VPS lists by `vps_file_id`; a row with none is
not Discovery's to draw. Where two sources offer the same file the first source owns it.

Nothing here keeps a connection to the source. The pane asks again when the registry says an
offer for this entry moved, and redraws only the rows whose state did.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from common.online import sources as source_registry
from manager.extensions import ext_lists
from manager.kit import offload
from manager.kit.failed_together import Failures
from manager.kit.on_page import on_page
from manager.library import discovery_get
from manager.library.discovery_get import Offered, Pick, Tray

logger = logging.getLogger("vpinfe.manager.library.discovery_ext")

POLL_S = 2.0

# The unit a size is said in, and the count of bytes in it, largest first.
SIZE_UNITS = (("gb", 1 << 30), ("mb", 1 << 20), ("kb", 1 << 10))
MAX_SIZE = 1 << 63


def kind_for(source: dict[str, Any], listed_as: str) -> str:
    """The name `source` knows the files VPS lists under `listed_as` by; empty where it
    holds none."""
    held = set(source.get("kinds") or ())
    for ours in discovery_get.of_listing(listed_as)[0]:
        found = sorted(source_registry.names_for(source_registry.file_kind(ours)) & held)
        if found:
            return found[0]
    return ""


def size_text(size: int | None) -> str:
    """A size in the unit a person reads it in; empty where the source did not say."""
    if size is None:
        return ""
    size = min(size, MAX_SIZE)
    for unit, scale in SIZE_UNITS:
        if size >= scale:
            return t(f"manager.discovery.size.{unit}", count=f"{size / scale:.1f}")
    return t("manager.discovery.size.kb", count=f"{max(size, 1) / 1024:.1f}")


def slots_for(sources: list[dict[str, Any]], listed: list[str]) -> list[str]:
    """The slots to ask for the files VPS lists: for each source, the first slot of each
    listing that it serves."""
    slots: list[str] = []
    for source in sources:
        held = set(source.get("kinds") or ())
        for listed_as in listed:
            for ours in discovery_get.of_listing(listed_as)[0]:
                slot = source_registry.file_kind(ours)
                if source_registry.names_for(slot) & held:
                    if slot not in slots:
                        slots.append(slot)
                    break
    return slots


def _said(combined: dict[str, Any] | BaseException, source: dict[str, Any],
          kind: str) -> dict[str, Any] | BaseException:
    """What `source` said of `kind` in a game's combined answer. A source the answer does
    not hold has not answered, so what it offered before is kept."""
    if isinstance(combined, BaseException):
        return combined
    for each in combined.get("sources") or ():
        if each["id"] == source["id"]:
            for said in each.get("kinds") or ():
                if said["kind"] == kind:
                    return RuntimeError(said["error"]) if said.get("error") else said
    return LookupError(source["id"])


async def ask(library: Any, row: dict[str, Any], game_id: str, listed: list[str],
              keep: dict[str, Offered] | None = None,
              combined: dict[str, Any] | BaseException | None = None) -> list[Offered]:
    """What the switched-on sources offer for the files VPS lists, one per file. A source
    that cannot answer keeps what `keep` held of it, so one slow answer does not clear its
    rows; the rest draw.

    A game of the library is asked of every source in one call, unless `combined` is that
    call's answer already. An entry the library does not hold is asked of each source.
    """
    sources = library.online_sources()
    questions = [(source, kind, listed_as)
                 for source in sources for listed_as in listed
                 if (kind := kind_for(source, listed_as))]
    if game_id:
        if combined is None:
            combined = await discovery_get.game_offers(
                library, game_id, row["id"], row["name"], slots_for(sources, listed))
        answers = [_said(combined, source, kind) for source, kind, _listed_as in questions]
    else:
        answers = await asyncio.gather(
            *(offload.io(library.source_offers, source["extension"], source["key"], kind,
                         game_id, row["id"], row["name"])
              for source, kind, _listed_as in questions),
            return_exceptions=True)
    found: dict[str, Offered] = {}
    for (source, kind, _listed_as), answer in zip(questions, answers, strict=True):
        if isinstance(answer, BaseException):
            logger.warning("manager: %s could not say what it has for %s", source["id"],
                           row["id"], exc_info=answer)
            for one in (keep or {}).values():
                if one.source_id == str(source["id"]) and one.kind == kind:
                    found.setdefault(str(one.row["vps_file_id"]), one)
            continue
        for each in answer.get("rows") or ():
            file_id = str(each.get("vps_file_id") or "")
            if not file_id or file_id in found:
                continue
            pick = Pick(discovery_get.key_of(str(source["id"]), kind, str(each["key"])), kind,
                        str(source["id"]), row=str(each["key"]))
            found[file_id] = Offered(str(source["extension"]), str(source["key"]),
                                     str(source["id"]), str(source["name"]), kind, each, pick)
    return list(found.values())


def adopt(context: dict[str, Any], tray: Tray, found: list[Offered]) -> None:
    """Make `found` what the pane draws from."""
    context["hosted"] = {str(one.row["vps_file_id"]): one for one in found}
    tray.offered = {one.pick.key: one for one in found}


def _shape(found: list[Offered]) -> list[tuple[str, bool]]:
    return sorted((one.pick.key, bool(one.row.get("current"))) for one in found)


async def refresh(context: dict[str, Any], tray: Tray, game_id: str,
                  listed: list[str], draws_rows: bool = True) -> None:
    """Ask again. A row whose state moved is repainted where it stands; a file that
    appeared, vanished or came in redraws the pane, where the pane draws rows."""
    library, row, state = context["library"], context["discovery"], context["state"]
    before = _shape(list(tray.offered.values()))
    old = dict(tray.offered)
    found = await ask(library, row, game_id, listed, keep=old)
    adopt(context, tray, found)
    if draws_rows and _shape(found) != before:
        await discovery_get.redraw(state)
        return
    moved = [key for key, one in tray.offered.items()
             if key not in old or one.row.get("state") != old[key].row.get("state")
             or one.row.get("acts") != old[key].row.get("acts")]
    tray.repaint(*moved)
    tray.summarize()


def watch(context: dict[str, Any], tray: Tray, game_id: str, listed: list[str],
          version: int, draws_rows: bool = True) -> None:
    """Ask the registry on a timer whether an offer for this entry moved, and ask the source
    again when one did. Drawn inside the pane, so a redraw ends it. Where the pane draws no
    rows the status line is all that follows the answer."""
    row = context["discovery"]
    seen = version

    async def tick() -> None:
        nonlocal seen
        try:
            answer = await offload.io(context["library"].source_changes, seen)
        except Exception:  # noqa: BLE001 - the next tick asks again
            logger.debug("manager: could not read what moved", exc_info=True)
            return
        newest = int(answer.get("version") or 0)
        moved = {str(one) for one in answer.get("vps_ids") or ()}
        if newest < seen or "" in moved or str(row["id"]) in moved:
            seen = newest
            await refresh(context, tray, game_id, listed, draws_rows)
        else:
            seen = max(seen, newest)

    ui.timer(POLL_S, on_page(tick))


async def version_of(library: Any) -> int:
    """The registry's newest change, read before the offers so none between is missed."""
    try:
        return int((await offload.io(library.source_changes, 0)).get("version") or 0)
    except Exception:  # noqa: BLE001 - the first tick then reads everything
        logger.debug("manager: could not read the registry's version", exc_info=True)
        return 0


def stopper(context: dict[str, Any], tray: Tray, game_id: Callable[[], str],
            listed: list[str]) -> Callable[[], Awaitable[Any]]:
    """The pane's Stop for what extensions are getting: each source with a row under way is
    told to stop, as a pane-level act with no kind and no row."""
    @on_page
    async def stop() -> None:
        library, row = context["library"], context["discovery"]
        going = {(one.extension, one.key): one.name for one in tray.offered.values()
                 if one.level == "unknown"}
        failed = Failures()
        for extension, key in sorted(going):
            try:
                await offload.io(library.source_act, extension, key, "", "", "stop", game_id(),
                                 row["id"], row["name"])
            except Exception as exc:  # noqa: BLE001 - the other sources still stop
                logger.warning("manager: could not stop %s.%s", extension, key, exc_info=True)
                failed.add(going[(extension, key)], why(exc))
        failed.say("manager.discovery.stop_failed")
        if not tray.running:
            tray.stopping = False
        await refresh(context, tray, game_id(), listed)

    return stop


def acts_for(context: dict[str, Any], tray: Tray, game_id: Callable[[], str],
             listed: list[str], pick_key: str) -> Callable[[str], Callable[[], Awaitable[Any]]]:
    """The handler for each button a source drew on a row. It looks the row up when pressed,
    so a button drawn before the last refresh acts on the row as it is now."""
    def handler(act: str) -> Callable[[], Awaitable[Any]]:
        @on_page
        async def press() -> None:
            offered = tray.offered.get(pick_key)
            if offered is None:
                return
            library, row = context["library"], context["discovery"]
            try:
                answer = await offload.io(
                    library.source_act, offered.extension, offered.key, offered.kind,
                    offered.pick.row, act, game_id(), row["id"], row["name"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("manager: %s would not %s", offered.source_id, act, exc_info=True)
                ui.notify(t("manager.discovery.act_failed"), caption=why(exc), type="negative")
                return
            await answered(library, offered, answer or {}, game_id(), row)
            await refresh(context, tray, game_id(), listed)

        return press

    return handler


@on_page
async def answered(library: Any, offered: Offered, answer: dict[str, Any], game_id: str,
                   row: dict[str, Any]) -> None:
    """What the source made of a button: a dialog of its own, or a word."""
    if answer.get("action"):
        record = next((one for one in await offload.io(library.extensions)
                       if one.get("name") == offered.extension), None)
        if record is None:
            ui.notify(t("said.action_failed"), type="negative")
            return
        await ext_lists.open_over(
            record, str(answer["action"]), [offered.pick.row],
            who={"game_id": game_id, "vps_id": row["id"], "name": row["name"]})
    elif answer.get("message"):
        ui.notify(str(answer["message"]))


async def read_look(context: dict[str, Any], offered: Offered, game_id: str) -> dict[str, Any]:
    """What the file's page says: its description, pictures and files. Empty where the source
    has no look or cannot answer."""
    row = context["discovery"]
    try:
        return await offload.io(context["library"].source_look, offered.extension, offered.key,
                                offered.kind, offered.pick.row, game_id, row["id"],
                                row["name"]) or {}
    except Exception:  # noqa: BLE001 - the row stands on what VPS says
        logger.debug("manager: %s has no look at %s", offered.source_id, offered.pick.row,
                     exc_info=True)
        return {}
