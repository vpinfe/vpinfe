"""A game's backglass file, drawn: which one a table reads, and its picture."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from common import service_errors
from common.games import (
    asset_lens,
    asset_origin,
    asset_resolver,
    game_lens,
    game_repository,
    media_lookup,
    media_ops,
    media_placement,
)
from common.games.asset_registry import BACKGLASS_FILES, backglass_file_kind
from common.games.formats import directb2s_parser
from common.games.game_repository import game_to_row
from common.i18n import t
from common.media import sized_media

logger = logging.getLogger("vpinfe.common.games.backglass_ops")

KIND = "backglass"

# The origin a picture is recorded with, by the kind of file it was drawn from.
_HOSTS = {"backglass": asset_origin.BACKGLASS_FILE, "b2spro": asset_origin.B2SPRO_FILE}
_DRAWN_FROM = {host: kind for kind, host in _HOSTS.items()}


def _file(game_id: str, table_id: str, path: str, kind: str = KIND) -> Path:
    return _file_of(game_lens.game_or_refuse(game_id), table_id, path, kind)


def _file_of(game: Any, table_id: str, path: str, kind: str = KIND) -> Path:
    """The backglass file named by `path`, of either kind, or else the file of `kind` that
    `table_id` reads. Where no table is named it is the folder's own, and failing that the
    default table's."""
    game_dir = Path(game.full_path_game or "")
    if path:
        found = asset_lens.inside(game_dir, path)
        if not backglass_file_kind(found):
            raise service_errors.RefusedError(t("error.backglass.not_a_backglass"),
                                              details={"path": path})
    else:
        if kind not in BACKGLASS_FILES:
            raise service_errors.RefusedError(t("error.backglass.not_a_backglass"),
                                              details={"kind": kind})
        files, _ = asset_resolver.folder_listing(game_dir)
        if table_id:
            media_ops.stem_or_refuse(game, table_id)
            names = [media_lookup.table_filename(game, table_id)]
        else:
            ordered = sorted(_table_rows(game, ""), key=lambda row: not row.get("default"))
            names = [game_dir.name, *(str(row.get("filename") or "") for row in ordered)]
        held: dict = {}
        for name in names:
            held = asset_resolver.resolve_for_table(name, game_dir.name, files)[kind]
            if held.get("file"):
                break
        if not held.get("file"):
            raise service_errors.NotFoundError(t("error.backglass.none"))
        found = game_dir / held["file"]
    if not found.is_file():
        raise service_errors.NotFoundError(t("error.backglass.none"))
    return found


def picture_file(game_id: str, table_id: str = "", look: str = "on",
                 version: str = "authentic", grill: bool = False, path: str = "",
                 size: int | None = None, *, kind: str = KIND) -> sized_media.Served:
    """The backglass picture in the look asked for. `grill` is the picture as the
    renderer in effect cuts it. The version token is the file's, so a changed file is
    a new picture."""
    if look not in directb2s_parser.LOOKS:
        raise service_errors.RefusedError(t("error.backglass.unknown_look"),
                                          details={"look": look})
    if version not in directb2s_parser.VERSIONS:
        raise service_errors.RefusedError(t("error.backglass.unknown_version"),
                                          details={"version": version})
    size = sized_media.size_or_refuse(size, "image")
    source = _file(game_id, table_id, path, kind)
    try:
        drawn = directb2s_parser.picture(
            source, look, version, grill)  # type: ignore[arg-type]
    except directb2s_parser.BackglassError:
        raise service_errors.NotFoundError(t("error.backglass.damaged")) from None
    served = sized_media.served(drawn, size)
    return sized_media.Served(served.path, sized_media.version(source) or "")


def scoreview_file(game_id: str, table_id: str = "", path: str = "",
                    size: int | None = None) -> sized_media.Served:
    """The art the file draws on the score view window. Not found where it has none."""
    size = sized_media.size_or_refuse(size, "image")
    source = _file(game_id, table_id, path)
    drawn = directb2s_parser.scoreview_art(source)
    if drawn is None:
        raise service_errors.NotFoundError(t("error.backglass.scoreview_absent"))
    served = sized_media.served(drawn, size)
    return sized_media.Served(served.path, sized_media.version(source) or "")


_ON = ("1", "true")
LEGACY, B2S = "Plugin.B2SLegacy.", "Plugin.B2S."
# The answer of `renderer` where no plugin draws the backglass.
NONE = ""


def _table_rows(game: Any, table_id: str) -> list[dict]:
    from common.games import table_lens

    rows = [row for row in table_lens.table_rows(game, game_to_row(game)) if row.get("id")]
    return [row for row in rows if row["id"] == table_id] if table_id else rows


def renderer(held: Mapping[str, str], program: Mapping[str, Any] | None = None) -> str:
    """The plugin that draws the backglass given the settings in effect, as its key prefix,
    or `NONE`. `program` is `launcher_ops.program_of`: the plugins installed and the Enable
    each renderer holds while the settings leave it out. `Plugin.B2SLegacy.` wins over
    `Plugin.B2S.` where both are installed and on.

    Without the installed plugins, `Plugin.B2SLegacy.` draws unless its Enable is off, and
    `Plugin.B2S.` draws otherwise.
    """
    present = (program or {}).get("plugins")
    defaults = (program or {}).get("defaults") or {}

    def on(prefix: str, fallback: str) -> bool:
        key = f"{prefix}Enable"
        return (held.get(key) or defaults.get(key) or fallback).lower() in _ON

    if present is None:
        return LEGACY if on(LEGACY, "1") else B2S
    if "B2SLegacy" in present and on(LEGACY, "1"):
        return LEGACY
    return B2S if "B2S" in present and on(B2S, "0") else NONE


def cuts_grill(held: Mapping[str, str], program: Mapping[str, Any] | None = None) -> bool:
    """Whether the renderer in effect cuts the grill off the picture. No renderer shows it."""
    def on(key: str) -> bool:
        return held.get(key, "").lower() in _ON

    drawn = renderer(held, program)
    if drawn == LEGACY:
        return on(f"{LEGACY}B2SHideGrill")
    return drawn == B2S and not on(f"{B2S}ShowGrill")


def grill_cut(game_id: str, table_id: str = "") -> bool:
    """Whether the renderer in effect cuts the grill off the picture. The folder's first
    table answers for the shared slot. Cut where the settings cannot be read."""
    from common.games import launcher_ops

    game = game_lens.game_or_refuse(game_id)
    row = next((one for one in _table_rows(game, table_id)
                if one.get("launcher") and one.get("launcher_app_configurable")), None)
    if row is None:
        return True
    try:
        held = launcher_ops.effective_values(row["launcher"], row["id"])
        program = launcher_ops.program_of(row["launcher"])
    except Exception:  # noqa: BLE001 - the picture is drawn without its settings
        return True

    return cuts_grill(held, program)


def _drawn(game_id: str, table_id: str, kind: str = KIND,
           grill: bool | None = None) -> tuple[Path, str]:
    """The picture to place and its hash: On, authentic, cut as the renderer cuts it
    unless `grill` says."""
    from common.online.vpsdb_media import file_md5

    cut = grill_cut(game_id, table_id) if grill is None else grill
    served = picture_file(game_id, table_id, grill=cut, kind=kind)
    return served.path, file_md5(served.path)


def _intact(found: Path) -> Any | None:
    """What the file says about itself, or None where it is damaged."""
    facts = directb2s_parser.read(found)
    return None if facts is None or directb2s_parser.damaged(facts) else facts


def _seen(game_id: str, table_id: str, kind: str = KIND) -> tuple[Path, Any] | None:
    """The file of `kind` that `table_id` reads and what it says about itself, or None
    where it reads none or the one it reads is damaged."""
    try:
        found = _file(game_id, table_id, "", kind)
    except service_errors.NotFoundError:
        return None
    facts = _intact(found)
    return None if facts is None else (found, facts)


def unpicked(game_id: str, table_id: str = "") -> str:
    """The kind of backglass file a picture is drawn from where nobody picks: the first of
    `BACKGLASS_FILES` that the table reads a readable file of, or "" for none."""
    try:
        return next((kind for kind in BACKGLASS_FILES if _seen(game_id, table_id, kind)), "")
    except Exception:  # noqa: BLE001 - a fill goes on to the other sources and games
        logger.exception("Could not read the backglass files of %s", game_id)
        return ""


def offer(game_id: str, table_id: str = "", kind: str = KIND) -> dict | None:
    """The file of `kind` whose picture could fill the backglass slot, as the slot's dialog
    needs it: where it is, the version token its picture is addressed by, and whether the
    grill is cut. None where the table reads no such file or the one it reads is damaged."""
    return offers(game_id, table_id, (kind,))[kind]


def offers(game_id: str, table_id: str = "",
           kinds: tuple[str, ...] = BACKGLASS_FILES) -> dict[str, dict | None]:
    """`offer` for each of `kinds`. A later kind's is None also where its file draws the
    same picture as an earlier kind's, so the dialog shows that picture once."""
    found: dict[str, dict | None] = dict.fromkeys(kinds)
    try:
        seen = {kind: hit[0] for kind in kinds
                if (hit := _seen(game_id, table_id, kind)) is not None}
        if not seen:
            return found
        grill = grill_cut(game_id, table_id)
        game_dir = Path(game_lens.game_or_refuse(game_id).full_path_game or "")
        drawn: set[str] = set()
        for kind, path in seen.items():
            if len(seen) > 1:
                md5 = _hash_or_blank(game_id, table_id, kind, grill)
                if md5 in drawn:
                    continue
                if md5:
                    drawn.add(md5)
            found[kind] = {"path": path.relative_to(game_dir).as_posix(), "file": path.name,
                           "grill": grill, "version": sized_media.version(path) or ""}
    except Exception:  # noqa: BLE001 - a slot is described without its extra tab
        logger.exception("Could not offer the backglass files of %s", game_id)
    return found


def _hash_or_blank(game_id: str, table_id: str, kind: str, grill: bool) -> str:
    """The hash of the picture the file of `kind` draws, or "" where it cannot be drawn,
    which matches no other."""
    try:
        return _drawn(game_id, table_id, kind, grill)[1]
    except service_errors.ServiceError:
        return ""
    except Exception:  # noqa: BLE001 - the other file is still offered
        logger.exception("Could not draw the %s picture of %s", kind, game_id)
        return ""


def files_of(launcher_id: str) -> list[dict[str, Any]]:
    """Each backglass file read by a table that `launcher_id` plays, once: its game, its
    path in the game's folder, its art's size, the height of its grill, the size of its
    score view art, and the DMDs it places as `(window, x, y, width, height)`."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for game_id, game in list(game_repository.catalog().items()):
        try:
            rows = [row for row in _table_rows(game, "") if row.get("launcher") == launcher_id]
            game_dir = Path(game.full_path_game or "")
            for row in rows:
                seen = _seen(game_id, row["id"])
                if seen is None or seen[1].size is None:
                    continue
                path = seen[0].relative_to(game_dir).as_posix()
                found.setdefault((game_id, path), {
                    "game": game_id, "path": path, "size": tuple(seen[1].size),
                    "grill": seen[1].grill,
                    "scoreview_art": seen[1].scoreview_art and tuple(seen[1].scoreview_art),
                    "placed": [(one.target, *one.placement) for one in seen[1].displays
                               if not one.source]})
        except Exception:  # noqa: BLE001 - one game's file is not the sweep
            logger.exception("Could not read the backglass files of %s", game.game_dir_name)
    return list(found.values())


class Scored(NamedTuple):
    """One table, the launcher that plays it, the game file its settings sit beside, and
    whether the backglass file it reads places a Score View DMD."""

    table: str
    launcher: str
    file: str
    in_file: bool


_PLACES: dict[Path, tuple[int, int, bool]] = {}


def _places_dmd(found: Path) -> bool | None:
    """Whether the file places a Score View DMD, None where it is damaged. Held by size and
    date, so a library is read once."""
    stamp = found.stat()
    held = _PLACES.get(found)
    if held is not None and held[:2] == (stamp.st_mtime_ns, stamp.st_size):
        return held[2]
    facts = _intact(found)
    if facts is None:
        return None
    places = any(one.target == "ScoreView" and not one.source for one in facts.displays)
    _PLACES[found] = (stamp.st_mtime_ns, stamp.st_size, places)
    return places


def _away(folder: Path) -> bool:
    """Not a folder, or one with nothing in it. A folder that cannot be listed is not away:
    its files are read, and fail, one at a time."""
    if not folder.is_dir():
        return True
    try:
        return next(folder.iterdir(), None) is None
    except OSError:
        return False


def score_dmds(game: Any, launcher_id: str = "") -> list[Scored]:
    """For each table of the game whose launcher has settings of its own, whether the
    backglass file it reads places a Score View DMD: false for a table that reads none.
    Left out: a table whose file is damaged or cannot be read, and every table of a game
    whose folder is missing, empty or has no path. With `launcher_id`, only the tables that
    launcher plays."""
    found: list[Scored] = []
    folder = Path(game.full_path_game or "")
    if not game.full_path_game or _away(folder):
        return found
    for row in _table_rows(game, ""):
        if (not row.get("launcher") or not row.get("launcher_app_configurable")
                or launcher_id and row["launcher"] != launcher_id):
            continue
        try:
            places = _places_dmd(_file_of(game, row["id"], ""))
        except service_errors.NotFoundError:
            places = False
        except OSError:
            continue
        if places is not None:
            found.append(Scored(row["id"], row["launcher"],
                                str(folder / row["filename"]),
                                places))
    return found


def place_picture(game_id: str, table_id: str = "", kind: str = KIND) -> dict:
    """Write the picture of the file of `kind` as this slot's backglass picture, recorded
    as drawn from that kind of file, so a later redraw knows it is ours and which file to
    draw it from."""
    drawn, md5 = _drawn(game_id, table_id, kind)
    return media_ops.place_file(game_id, "backglass", table_id, drawn, _HOSTS[kind], md5)


def redraw(game_id: str) -> int:
    """Draw again every picture of this game's that the file made and nobody has since
    replaced, from the kind of file it was drawn from. Answers how many were redrawn. A
    picture whose file no longer matches the hash recorded for it was changed by hand and
    is left, and so is one with another file of the same family at its tier: placing the
    redraw would delete it."""
    from common.online.vpsdb_media import file_md5

    game = game_lens.game_or_refuse(game_id)
    game_dir = Path(game.full_path_game or "")
    held = {path: source for path, source in asset_origin.sources(game_dir).items()
            if source.get("host") in _DRAWN_FROM and source.get("hash")}
    if not held:
        return 0
    redrawn = 0
    for table in ({"id": "", "stem": game_dir.name},
                  *({"id": row["id"], "stem": Path(str(row.get("filename") or "")).stem}
                    for row in _table_rows(game, ""))):
        name = media_placement.target_name(KIND, table["stem"], ".png")
        path = f"medias/{name}"
        source = held.get(path)
        if source is None or file_md5(game_dir / path) != source["hash"]:
            continue
        if media_placement.displaced(game_dir, KIND, table["stem"], ".png") != [game_dir / path]:
            continue
        try:
            drawn, md5 = _drawn(game_id, table["id"], _DRAWN_FROM[source["host"]])
        except service_errors.ServiceError:
            continue
        if md5 and md5 != source["hash"]:
            media_ops.place_file(game_id, KIND, table["id"], drawn, source["host"], md5)
            redrawn += 1
    return redrawn


def redraw_all(proceed: Callable[[], bool] = lambda: True) -> int:
    """`redraw` for every game that has such a picture. Answers how many were redrawn."""
    total = 0
    for game_id, game in list(game_repository.catalog().items()):
        if not proceed():
            break
        try:
            if any(source.get("host") in _DRAWN_FROM
                   for source in asset_origin.sources(str(game.full_path_game)).values()):
                total += redraw(game_id)
        except Exception:  # noqa: BLE001 - one game's picture is not the sweep
            logger.exception("Could not redraw the backglass picture of %s",
                             game.game_dir_name)
    return total


__all__ = ["B2S", "KIND", "LEGACY", "NONE", "Scored", "cuts_grill", "files_of", "grill_cut",
           "offer", "offers", "picture_file", "place_picture", "redraw", "redraw_all",
           "renderer", "score_dmds", "scoreview_file", "unpicked"]
