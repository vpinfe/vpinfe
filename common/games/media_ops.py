"""Putting artwork in a game's slots, and describing a slot in the detail a curator wants.

A slot is a kind at a tier, and a tier is a filename - so choosing where a file lands is
choosing what it is called. Everything here answers with what now resolves rather than a
bare acknowledgement: a shared file is outranked by any table-specific one, so "written"
and "in use" are different facts and the caller should not have to guess which it got.

`media_service` resolves; this places, removes and renames.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from common import service_errors
from common.failures import why
from common.games import (
    asset_origin,
    game_lens,
    game_repository,
    media_lookup,
    media_placement,
    media_probe,
    media_service,
)
from common.games.game import Game
from common.games.game_repository import game_to_row
from common.i18n import t
from common.media import sized_media
from common.media.media_specs import (
    MEDIA_SPECS,
    MediaCandidate,
    MediaSpec,
    canonical_kind,
    media_candidates,
    media_family,
    resolve_media_entries,
)

logger = logging.getLogger("vpinfe.common.games.media_ops")

_SPECS = {spec.kind: spec for spec in MEDIA_SPECS}
_KINDS = set(_SPECS)
_LOGO_SET = "logo"  # the reserved wheel set that resolves from each game's logo


def kind_or_refuse(kind: str) -> str:
    """The canonical spelling of a media kind, or a refusal listing the ones there are."""
    kind = canonical_kind(kind)
    if kind not in _KINDS:
        raise service_errors.RefusedError(
            t("error.games.unknown_kind"),
            details={"unknown": kind, "known": sorted(_KINDS)})
    return kind


def stem_or_refuse(game: Game, table_id: str) -> str:
    """The stem a table's art is named after, or a refusal if that table is not this
    game's. Distinct from the filename lookup: this wants the name without its suffix."""
    filename = media_lookup.table_filename(game, table_id)
    if not filename:
        raise service_errors.NotFoundError(
            t("error.games.table_not_found"),
            details={"game": game.game_dir_name, "table": table_id})
    return Path(filename).stem


def _folder(game: Game) -> Path:
    return Path(game.full_path_game or "")


def _prefix(game_id: str, table_id: str = "") -> str:
    return (f"/api/v1/games/{game_id}/tables/{table_id}/media" if table_id
            else f"/api/v1/games/{game_id}/media")


def game_media(game_id: str, codecs: bool = False) -> dict:
    """Every kind the folder resolves, which is what all its tables share.

    Art named for one build belongs to that build and answers under its table.
    """
    game = game_lens.game_or_refuse(game_id)
    return {"media": media_service.media_map(_folder(game), _prefix(game_id),
                                             codecs=codecs)}


def table_media(game_id: str, table_id: str, codecs: bool = False) -> dict:
    """The same kinds, resolved for one build rather than for the folder.

    Two builds of a game can genuinely differ - a VR room and a desktop table are not the
    same picture - so each answers for itself.
    """
    game = game_lens.game_or_refuse(game_id)
    stem = stem_or_refuse(game, table_id)
    return {"media": media_service.media_map(_folder(game), _prefix(game_id, table_id),
                                             stem, codecs=codecs)}


def media_file(game_id: str, kind: str, table_id: str = "",
               size: int | None = None, page: int = 1, file: str = "") -> sized_media.Served:
    """The file a slot currently resolves to, at `size` if one is asked for, or a
    refusal. The caller sends it. `page` is the page of a kind that has them.

    `file` names one of the files the slot holds instead, as `detail` lists them. Only a
    name from that list is served: it is looked up, never joined onto a path.
    """
    game = game_lens.game_or_refuse(game_id)
    kind = kind_or_refuse(kind)
    size = sized_media.size_or_refuse(size, media_family(kind))
    stem = stem_or_refuse(game, table_id) if table_id else None
    path = (_held_file(game, kind, stem, file) if file
            else media_service.page_file(_folder(game), kind, page, stem))
    if path is None or not path.is_file():
        raise service_errors.NotFoundError(t("error.games.no_media", kind=(kind)))
    return sized_media.served(path, size)


def _held_file(game: Game, kind: str, stem: str | None, name: str) -> Path | None:
    """The file of this name among those the slot holds, or None."""
    game_dir = _folder(game)
    files, medias = media_service.media_contents(game_dir)
    variant, active_sets = media_service.media_settings()
    return next((item.path for item in media_candidates(
        game_dir, files, medias, kind, variant, stem, active_sets)
        if item.path.name == name), None)


def overrides(game_id: str) -> dict:
    """Where the game's art is not the whole story.

    Asked from the game's own lens, which otherwise cannot see a table-specific file at
    all: resolving without a table stem never looks at that tier. A curator scanning a
    folder wants the odd one out, and the odd one out is invisible without this.

    One walk for every kind and every table, because the caller is drawing a map of all of
    them and twenty round trips to answer one question would be worse.
    """
    from common.games import table_lens

    game = game_lens.game_or_refuse(game_id)
    game_dir = _folder(game)
    files, medias = media_service.media_contents(game_dir)
    variant, active_sets = media_service.media_settings()

    found: dict[str, list[dict]] = {}
    for table in table_lens.table_rows(game, game_to_row(game)):
        stem = Path(str(table.get("filename") or "")).stem
        if not table.get("id") or not stem:
            continue
        for spec in MEDIA_SPECS:
            own = next((item for item in media_candidates(
                game_dir, files, medias, spec.kind, variant, stem, active_sets)
                if item.tier == "table"), None)
            if own is not None:
                found.setdefault(spec.kind, []).append({
                    "table": table["id"],
                    "filename": table.get("filename") or "",
                    "version": table.get("version") or "",
                    "file": own.path.name,
                })
    return {"overrides": found}


_NO_FACTS = {"size_bytes": None, "modified": None, "width": None, "height": None,
             "format": None, "duration_s": None, "video_codec": None}


def _file_facts(path: Path) -> dict:
    """Size, date, format, pixel size and running time - what tells two candidates for a
    slot apart.

    Every part is best-effort: a file that cannot be opened still has a name worth showing,
    and a slot that reports nothing at all is worse than one missing a number.
    """
    try:
        stat = path.stat()
    except OSError:
        return dict(_NO_FACTS)
    return {**media_probe.probe(path), "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()}


def _shown_to(game: Game, game_dir: Path, files: set[str], medias: set[str], kind: str,
              variant: str, active_sets: dict[str, str] | None) -> dict[Path, int]:
    """How many of the game's tables each file is shown to for this kind."""
    from common.games import table_lens

    counted: dict[Path, int] = {}
    for table in table_lens.table_rows(game, game_to_row(game)):
        stem = Path(str(table.get("filename") or "")).stem
        if not table.get("id") or not stem:
            continue
        hit = resolve_media_entries(game_dir, files, medias, variant, stem,
                                    active_sets).get(kind)
        if hit is not None and hit.path is not None:
            counted[hit.path] = counted.get(hit.path, 0) + 1
    return counted


def _reached(game_dir: Path, kind: str, lens: str, variant: str) -> str:
    """The stem a Remove at `lens` acts on: the default's only while the default is what
    the game shows, so a file a set or a logo hides is not taken unseen."""
    reached = media_placement.reached(game_dir, kind, lens, variant)
    if reached != lens:
        hit = media_service.resolved_media(game_dir).get(kind)
        if hit is None or hit.tier != "default":
            return lens
    return reached


def _after_remove(game_dir: Path, files: set[str], medias: set[str], kind: str,
                  variant: str, active_sets: dict[str, str] | None,
                  stem: str | None) -> Path | None:
    """The file this lens is shown once `remove` has taken the files at its tier."""
    at = game_dir.name if stem is None else stem
    gone = media_placement.removable(game_dir, kind, _reached(game_dir, kind, at, variant),
                                     variant=variant)
    left_in_root = files - {path.name for path in gone if path.parent == game_dir}
    left_in_medias = medias - {path.name for path in gone
                               if path.parent == game_dir / "medias"}
    hit = resolve_media_entries(game_dir, left_in_root, left_in_medias, variant, stem,
                                active_sets).get(kind)
    return hit.path if hit is not None else None


def detail(game_id: str, kind: str, table_id: str = "", page: int = 1) -> dict:
    """One slot: the winner, what it is, and every tier that holds a file for it, with
    how many of the game's tables each of those is shown to, and the file that takes
    over once Remove has taken this lens's own.

    What a curator needs and a frontend never asks for. For a later page, the page's own
    file and facts: it has no tiers of its own, being read where page 1 resolves, and
    what takes over once it is removed is the page after it.
    """
    game = game_lens.game_or_refuse(game_id)
    kind = kind_or_refuse(kind)
    stem = stem_or_refuse(game, table_id) if table_id else None
    game_dir = _folder(game)
    hit = media_service.resolved_media(game_dir, stem).get(kind)
    path = hit.path if hit is not None else None
    pages = media_service.resolved_pages(game_dir, stem).get(kind, [])
    if page > 1:
        if not 1 < page <= len(pages):
            raise service_errors.NotFoundError(
                t("error.games.no_such_page", slot=_SPECS[kind].label, page=page))
        path = pages[page - 1]

    files, medias = media_service.media_contents(game_dir)
    variant, active_sets = media_service.media_settings()
    candidates: dict[Path, MediaCandidate] = {}
    for item in media_candidates(game_dir, files, medias, kind, variant, stem, active_sets):
        candidates.setdefault(item.path, item)
    serves = _shown_to(game, game_dir, files, medias, kind, variant, active_sets)
    after_remove = (_after_remove(game_dir, files, medias, kind, variant, active_sets, stem)
                    if page == 1 else (pages[page] if page < len(pages) else None))
    recorded = asset_origin.sources(game_dir)
    hosts = {key: str(source.get("host", "") or "").strip()
             for key, source in recorded.items()
             if str(source.get("host", "") or "").strip()}
    prefix = _prefix(game_id, table_id)
    from common.games import backglass_ops

    offered = (backglass_ops.offers(game_id, table_id) if kind == backglass_ops.KIND
               else {})
    return {
        "kind": kind,
        "family": media_family(kind),
        "backglass_file": offered.get("backglass"),
        "b2spro_file": offered.get("b2spro"),
        "present": path is not None,
        "file": path.name if path is not None else None,
        "path": asset_origin.path_of(game_dir, path) or None,
        "via": hit.tier if hit is not None else None,
        "origin": (asset_origin.origin_of(hosts, game_dir, path)
                   or None) if path is not None else None,
        "matched_to": asset_origin.match_of(recorded, game_dir, path) or None,
        "page": page,
        "pages": max(len(pages), 1 if path is not None else 0),
        "tiers": [{"tier": item.tier, "file": item.path.name,
                   "path": asset_origin.path_of(game_dir, item.path) or None,
                   "wins": item.path == path, "serves": serves.get(item.path, 0),
                   **_file_facts(item.path)}
                  for item in candidates.values()] if page == 1 or path is None else [
            {"tier": hit.tier if hit is not None else "", "file": path.name, "wins": True,
             "serves": serves.get(hit.path, 0) if hit is not None else 0}],
        "after_remove": after_remove.name if after_remove is not None else None,
        "links": {"self": (None if path is None else f"{prefix}/{kind}" if page == 1
                           else f"{prefix}/{kind}?page={page}")},
        **(_file_facts(path) if path is not None else _NO_FACTS),
    }


def _placement(game_dir: Path, kind: str, spec: MediaSpec, table_id: str, stem: str,
               label: str) -> dict:
    """One destination: what the file would be called there, and what it would take.

    The extension is trimmed back off the name because the file decides it, and it is only
    supplied here to satisfy the family check.
    """
    suffix = spec.family[0]
    going = media_placement.displaced(game_dir, kind, stem, suffix)
    return {"table": table_id, "label": label,
            "base": media_placement.target_name(kind, stem, suffix,
                                                game_dir=game_dir)[:-len(suffix)],
            # `as_posix`, never `str`: a relative path on the wire is forward-slashed
            # whatever host built it. `str(WindowsPath)` gave clients "medias\\bg.png" on
            # Windows and "medias/bg.png" everywhere else, for the same library.
            "displaces": sorted(path.relative_to(game_dir).as_posix()
                                for path in going)}


def placements(game_id: str, kind: str) -> dict:
    """Every name this kind can take in this folder, with the cost of each.

    Answered without the file, because it can be: what a write displaces is the whole
    family at that tier, which does not depend on the extension arriving.
    """
    from common.games import table_lens

    spec = next((item for item in MEDIA_SPECS if item.kind == kind), None)
    if spec is None:
        raise service_errors.RefusedError(
            t("error.games.unknown_kind"),
            details={"unknown": kind, "known": sorted(_KINDS)})
    game = game_lens.game_or_refuse(game_id)
    game_dir = _folder(game)

    found = [_placement(game_dir, kind, spec, "", game_dir.name, "")]
    for table in table_lens.table_rows(game, game_to_row(game)):
        stem = Path(table["filename"]).stem
        option = _placement(game_dir, kind, spec, table["id"], stem, table["filename"])
        # A .vpx named after its folder makes the two tiers the same filename, and that
        # file is the game's. Most single-table folders are like that, so this is the
        # common case rather than a corner, and offering both would be two choices that
        # do one thing.
        if table.get("id") and option["base"].lower() not in {
                item["base"].lower() for item in found}:
            found.append(option)
    return {"placements": found, "extensions": list(spec.family)}


def displaced(game_id: str, kind: str, filename: str, table_id: str = "",
              page: int = 1) -> dict:
    """What placing `filename` at this tier would replace, as page `page` of a kind that
    has them.

    Asked before an upload, so a confirmation can name the files rather than warn in the
    abstract - and so the bytes are not sent for a drop the user cancels.
    """
    game = game_lens.game_or_refuse(game_id)
    game_dir = _folder(game)
    lens = stem_or_refuse(game, table_id) if table_id else game_dir.name
    try:
        stem = media_placement.page_stem(game_dir, kind, lens, lens if table_id else "", page)
        going = media_placement.displaced(game_dir, kind, stem, Path(filename).suffix, page)
    except media_placement.UnplaceableError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    # Forward-slashed on the wire whatever host built it - see `_placement`.
    return {"displaced": sorted(path.relative_to(game_dir).as_posix()
                                for path in going)}


def _wrote(game_dir: Path, kind: str, written: Path, game_id: str,
           table_id: str, table_stem: str | None) -> dict:
    reread(game_dir)
    entries = media_service.media_map(game_dir, _prefix(game_id, table_id), table_stem)
    return {"written": written.name, "media": {kind: entries[kind]}}


def reread(game_dir: Path) -> None:
    """The loaded game is what themes and `/media` serve, so it reads the folder again."""
    media_service.invalidate_media_cache()
    game_repository.refresh_game(game_dir)


def place_file(game_id: str, kind: str, table_id: str, source: Path,
               origin: str = "user", md5: str = "", page: int = 1) -> dict:
    """Copy a file into the slot and answer with what now resolves. A `page` past 1 goes
    beside the page 1 the slot shows.

    Shared by every caller that fills a slot from a file that already exists somewhere -
    one on this machine, one an online catalog published. Where the source is allowed to be
    is each caller's own question; this one is only about the write.
    """
    game = game_lens.game_for_writing(game_id)
    kind = kind_or_refuse(kind)
    if not Path(source).is_file():
        raise service_errors.RefusedError(t("error.games.not_file"),
                                          details={"path": str(source)})
    game_dir = _folder(game)
    table_stem = stem_or_refuse(game, table_id) if table_id else game_dir.name
    try:
        stem = media_placement.page_stem(game_dir, kind, table_stem,
                                         table_stem if table_id else "", page)
        written = media_placement.place(game_dir, kind, stem, source, page)
    except media_placement.UnplaceableError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    # Recorded with who placed it, which is what lets a later media refresh tell its own
    # art from something hand-placed and leave the latter alone.
    media_placement.record_origin(game_dir, written, origin, md5)
    return _wrote(game_dir, kind, written, game_id, table_id,
                  table_stem if table_id else None)


def place_upload(game_id: str, kind: str, table_id: str, staged: Path,
                 stem: str, page: int = 1) -> dict:
    """Store bytes that have already arrived at `stem`'s tier, then say what resolves.

    Split from `place_file` because the caller owns the staging: it holds the upload, it
    decides where the temporary file lives, and it removes it whatever happens here.
    """
    game = game_lens.game_for_writing(game_id)
    kind = kind_or_refuse(kind)
    game_dir = _folder(game)
    try:
        at = media_placement.page_stem(game_dir, kind, stem, stem if table_id else "", page)
        written = media_placement.place(game_dir, kind, at, str(staged), page)
    except media_placement.UnplaceableError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    media_placement.record_origin(game_dir, written)
    return _wrote(game_dir, kind, written, game_id, table_id,
                  stem if table_id else None)


def fetch_file(game_id: str, kind: str, table_id: str, source: str, vps_id: str,
               size: str) -> dict:
    """Download what an online catalog publishes and put it in the slot.

    A source and an id, never a URL: the only links this follows are ones a source produced
    for that id and kind, which is what stops it being a way to make this install fetch
    whatever a caller likes. The id does not have to be this game's - a mod, or a game the
    matcher got wrong, is exactly when the art has to come from another entry.
    """
    import tempfile

    from common.online import sources
    from common.security.http_client import download_file

    offer = sources.url_for(source, kind, vps_id, size, sources.enabled_ids())
    if offer is None:
        raise service_errors.NotFoundError(
            t("error.games.source_lacks_media"),
            details={"source": source, "vps_id": vps_id, "kind": kind, "size": size})
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging) / Path(offer.url).name
        try:
            download_file(offer.url, staged)
        except Exception as exc:
            raise service_errors.UnavailableError(why(exc)) from exc
        # Stamped with the source and the source's own hash. Without the hash this art is
        # indistinguishable from hand-placed later, so a refresh would leave it untouched
        # forever - the bulk downloader has always recorded one.
        return place_file(game_id, kind, table_id, staged, offer.source, offer.md5)


def move_page(game_id: str, kind: str, table_id: str, page: int, to: int) -> dict:
    """Swap page `page` of a slot with page `to`, so each takes the other's number.

    The pages are the ones beside the page 1 the slot shows, whichever tier that is.
    """
    game = game_lens.game_for_writing(game_id)
    kind = kind_or_refuse(kind)
    game_dir = _folder(game)
    lens = stem_or_refuse(game, table_id) if table_id else game_dir.name
    try:
        stem = media_placement.page_stem(game_dir, kind, lens,
                                         lens if table_id else "", 2)
        written = media_placement.move_page(game_dir, kind, stem, page, to)
    except media_placement.UnplaceableError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    return _wrote(game_dir, kind, written, game_id, table_id, lens if table_id else None)


def remove(game_id: str, kind: str, table_id: str = "", page: int | None = None) -> dict:
    """Take a file out of one tier. A build's own art and the default both survive the
    folder's file going. With no `page` the kind goes with all its pages; with one, only
    that page does and the pages after it move down a number. Always at the tier asked
    for: a page that lives at another tier is not this lens's to remove. The folder's tier
    holding nothing, the default it falls to is the file taken.
    """
    game = game_lens.game_for_writing(game_id)
    game_dir = _folder(game)
    lens = stem_or_refuse(game, table_id) if table_id else game_dir.name
    variant = media_service.media_settings()[0]
    try:
        removed = media_placement.remove(game_dir, kind, _reached(game_dir, kind, lens, variant),
                                         page, variant)
    except media_placement.UnplaceableError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    if removed:
        reread(game_dir)
    return {"removed": removed}


def use_logos_for_wheels() -> dict:
    """Set `[Media] wheelset` to the reserved `logo`, so every game's wheel is its logo."""
    from common.settings import config_service

    config_service.apply({"settings": {"media": {"wheelset": _LOGO_SET}}})
    return {"wheelset": _LOGO_SET}


def get_missing_media(game_ids: list[str]) -> dict:
    """Start the art fill for these games whatever the switch for new games says.

    `started` is false while another fill runs, which does not cover these games: the
    caller says so rather than waiting.
    """
    from common.games import media_fill
    from common.runtime.jobs import JobBusyError

    try:
        media_fill.start(game_ids)
    except JobBusyError:
        return {"started": False}
    return {"started": True}


__all__ = ["detail", "displaced", "fetch_file", "game_media", "get_missing_media",
           "kind_or_refuse", "media_file", "move_page", "overrides", "place_file", "place_upload",
           "placements", "remove", "stem_or_refuse", "table_media",
           "use_logos_for_wheels"]
