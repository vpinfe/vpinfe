"""Pictures from a source on Discovery: taking one for a game that has none, and setting aside
one that would replace what the game has.

A game on Home that lacks a picture a source has is taken from its tile. A picture that
replaced one VPinFE placed shows beside the game's own in the pane, where it is taken like any
other and can be set aside: Keep Mine for this version of it, Ignore Updates for the picture.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from common.media.media_specs import media_label_map
from common.online import vps_marks
from manager.kit import media_ownership, offload, panel, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import discovery_get, discovery_marks

if TYPE_CHECKING:
    from manager.library.discovery_kinds import Kind, Scene

logger = logging.getLogger("vpinfe.manager.library.discovery_pictures")

After = Callable[[], Awaitable[None]]


@on_page
async def take(library: Library, game: Mapping[str, Any], after: After) -> None:
    """Fill the game's empty slot with the file its tile names, then draw again."""
    pick = game["take"]
    word = media_label_map().get(str(pick["kind"]), str(pick["kind"]))
    try:
        await offload.io(library.fetch_media, str(game["game_id"]), None, str(pick["kind"]),
                         str(pick["source"]), str(game["vps_id"]), str(pick["size"]))
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        logger.warning("manager: could not take the %s for %s", pick["kind"], game["game_id"],
                       exc_info=True)
        ui.notify(t("manager.discovery.take_failed", kind=word), caption=why(exc),
                  type="negative")
        return
    ui.notify(t("manager.discovery.taken", kind=word, name=game["name"]), type="positive")
    await after()


def take_verb(library: Library, game: Mapping[str, Any], after: After) -> list[panel.Verb]:
    """Take, for a tile whose game can be filled by taking the file it offers."""
    if not game.get("take"):
        return []
    return [panel.Verb(t("manager.discovery.take"), lambda: take(library, game, after),
                       icon=verbs.FROM_ONLINE)]


def take_button(library: Library, game: Mapping[str, Any], after: After) -> None:
    """Take on a tile that opens the game when pressed: the press stops here."""
    if not game.get("take"):
        return
    word = media_label_map().get(str(game["take"]["kind"]), str(game["take"]["kind"]))
    source = media_ownership.source_name(str(game["take"]["source"]))
    with ui.element("div").classes("shrink-0").on("click.stop", lambda: None) \
            .on("keydown.enter.stop", lambda: None):
        panel.icon_action(t("manager.discovery.take"), lambda: take(library, game, after),
                          icon=verbs.FROM_ONLINE,
                          hint=t("manager.discovery.download_pic", kind=word, source=source))()


def newer(kind: Kind, offer: Mapping[str, Any]) -> bool:
    """Whether this offer is the picture that replaces the one VPinFE placed."""
    return bool(kind.change) and str(offer.get("md5") or "") == str(kind.change["md5"])


def set_aside(context: dict[str, Any], scene: Scene, kind: Kind) -> None:
    """Keep Mine and Ignore Updates, under the tile of the picture that replaces the game's
    own. Both are marks, so both offer Undo."""
    library, change = context["library"], kind.change

    async def after() -> None:
        await discovery_get.redraw(context["state"])

    def aside(subject: discovery_marks.Subject) -> Callable[[], Awaitable[None]]:
        return partial(discovery_marks.apply, library, discovery_marks.IGNORED, [subject],
                       after, name=subject.name)

    word = kind.label
    keep = discovery_marks.Subject(
        vps_marks.VERSION, str(change["id"]),
        t("manager.discovery.mark.picture_new", kind=word, game=scene.title),
        detail=str(change["md5"]))
    never = discovery_marks.Subject(
        vps_marks.UPDATES, str(change["id"]),
        t("manager.discovery.mark.picture_updates", kind=word, game=scene.title))
    with ui.row().classes("items-center gap-2 w-full"):
        panel.action(t("manager.discovery.picture.keep"), aside(keep), icon=verbs.KEEP,
                     inline=True, hint=t("manager.discovery.picture.keep.help", kind=word))()
        panel.action(t("manager.discovery.picture.ignore"), aside(never), icon=verbs.IGNORE,
                     inline=True, hint=t("manager.discovery.picture.ignore.help", kind=word))()
