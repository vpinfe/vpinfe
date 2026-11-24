"""The Discovery rail: the Game section and its Table, then a section for each kind the entry
lists or the game holds, under the Assets and Media headings."""

from __future__ import annotations

from functools import partial
from typing import Any

from common.i18n import t
from manager.library import discovery_pane
from manager.library.discovery_kinds import GAME, TABLE_LISTING, Kind, Scene
from manager.workbench.core import Section

_SUBJECTS = frozenset({"discovery"})


def sections(scene: Scene) -> tuple[Section, ...]:
    """Game, then Table, then each kept asset and media kind under its own heading.
    Each heading's kinds are in alphabetical order of what the rail says, so a row is found
    by its name and never moves when a file arrives. A kind with nothing to show is not in the
    rail; a game's own file for it is added on the game's Media section."""
    out = [Section(GAME, lambda _: t("word.game"),
                   _game, subjects=_SUBJECTS, titled=True)]
    tables = [one for one in scene.files if one.listed_as == TABLE_LISTING]
    assets = [one for one in scene.files if one not in tables]
    media = [one for one in scene.media if not one.blank]
    for group, kinds in (("", tables), (t("manager.workbench.assets"), assets),
                         (t("manager.discovery.media"), media)):
        out.extend(_section(kind, group) for kind in sorted(kinds, key=_by_name))
    return tuple(out)


def _by_name(kind: Kind) -> str:
    return kind.label.casefold()


def _section(kind: Kind, group: str) -> Section:
    word = kind.word

    def label(_context: dict[str, Any]) -> str:
        return t(word)

    return Section(kind.key, label, partial(_kind, kind.key), subjects=_SUBJECTS,
                   group=group, titled=True, corner=partial(_corner, kind.key))


async def _game(context: dict[str, Any]) -> None:
    discovery_pane.game(context, context["scene"])


async def _kind(key: str, context: dict[str, Any]) -> None:
    discovery_pane.kind_body(context, context["scene"], key)


def _corner(key: str, context: dict[str, Any], holder: Any) -> None:
    discovery_pane.corner(context, holder, key)
