"""An upload session, what it turns out to hold, and bringing it into the library.

Four steps, each answerable on its own: begin a session, add files to it, analyze what
arrived, and execute a plan over it. The plan is a separate step because what a drop does
to the folders already there is the question somebody is actually answering when they
confirm, and it can only be answered on this side - a caller cannot see the install's disk.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

from common import atomic_write, service_errors
from common.failures import why
from common.games import (
    identity_claims,
    media_browse,
    media_placement,
    media_service,
    table_swap,
)
from common.games.asset_registry import spec_for
from common.games.game_metadata import made_from
from common.games.table_lens import table_settings
from common.i18n import t
from common.media import sized_media
from common.media.media_specs import CONVERTIBLE_IMAGE, IMAGE_FAMILY
from common.runtime import jobs
from common.uploads import arrival, drop_convert, upload_session_service, working_folder
from common.uploads.asset_analyzer_service import (
    AnalysisResult,
    DetectedAsset,
    analyze_path,
    open_listed,
    session_source,
)
from common.uploads.asset_import_service import (
    ALL,
    FACTS,
    FILL,
    INFO_MODES,
    ImportPlan,
    PlannedItem,
    SwapKeptError,
    execute_import_plan,
    find_vps_entry,
    first_mode,
    require_plain_rom,
    select_plan_items,
    vps_folder_name,
)
from common.uploads.upload_session_service import (
    UnknownSessionError,
    UnsafePathError,
    UploadTooLargeError,
)

logger = logging.getLogger("vpinfe.common.uploads.upload_ops")

__all__ = ["NothingImportableError", "PicksNeededError", "UnprocessableUploadError",
           "UploadTooLargeError",
           "abort", "add_file", "analysis_of", "begin", "begin_from", "execute", "file_writer",
           "picture",
           "plan_for", "single_image", "start_import", "stop_import", "summary"]


class UnprocessableUploadError(service_errors.ServiceError):
    """The session is there and nothing can be made of what is in it."""


class NothingImportableError(service_errors.ServiceError):
    """A plan with no items. `details` carries what was blocked and why."""


class PicksNeededError(service_errors.ServiceError):
    """Groups of files for one place with no pick made. `details` carries the groups."""


def _asset_to_dict(asset: DetectedAsset) -> dict:
    return {
        "kind": asset.kind,
        "label": asset.label,
        "label_key": f"asset.kind.{asset.kind}.label",
        "media_kind": asset.media_kind,
        "root": asset.root,
        "size": asset.size,
        "detail": asset.detail,
        "preview": asset.preview,
    }


def _analysis_files(analysis: AnalysisResult) -> list[str]:
    """Every file the source holds, recognized or not, once each."""
    held = [entry.arcname for one in analysis.assets for entry in one.entries if not entry.is_dir]
    return list(dict.fromkeys([*held, *analysis.unrecognized]))


def _analysis_to_dict(analysis: AnalysisResult) -> dict:
    return {
        "source_kind": analysis.source_kind,
        "source_name": analysis.source_name,
        "has_game": analysis.has_game,
        "assets": [_asset_to_dict(one) for one in analysis.assets],
        "files": _analysis_files(analysis),
        "notes": list(analysis.notes),
        "error": analysis.error,
        "unrecognized": list(analysis.unrecognized),
        "placeable": [one for one in analysis.unrecognized if arrival.offers_places(one)],
        "bundle_info": analysis.bundle_info,
    }


def _item_name(item: PlannedItem) -> str:
    """What the file being imported is called, for a surface that lists what will land.
    Several files under one asset - a pup pack, a tree - are a count instead."""
    entries = [one for one in item.asset.entries if not one.is_dir]
    if len(entries) == 1:
        return PurePosixPath(entries[0].arcname).name
    return t("asset.analysis.files", count=len(entries))


def _replaces(plan: ImportPlan, item: PlannedItem) -> str:
    """What this item does to whatever is already there, said before it happens.

    Asked of the disk, so it belongs on this side: a caller cannot see the install's
    folders, and "will this overwrite something" is the question somebody is actually
    answering when they confirm.
    """
    if item.action == "write_info":
        if plan.new_game_dir_name:
            return t("asset.plan.adopts_info")
        if Path(item.destination).exists():
            return t("asset.plan.merges_info")
        return t("asset.plan.adopts_info")
    if plan.new_game_dir_name:
        return ""   # a folder that does not exist yet has nothing to replace
    base = Path(plan.game_dir)
    if item.action == "replace_vpx":
        return t("asset.plan.replaces_named", name=item.replaces) if item.replaces else ""
    if item.action == "replace_media":
        return _media_replaces(base, item)
    if item.action in {"replace_b2s", "copy"} and Path(item.destination).exists():
        return t("asset.plan.replaces_file")
    return ""


def _media_replaces(base: Path, item: PlannedItem) -> str:
    kind = item.asset.media_kind
    if item.asset.convert and item.asset.convert.as_is:
        return ""   # a file kept as it is displaces nothing and takes no slot
    if media_placement.displaced(base, kind, base.name, Path(item.destination).suffix):
        return t("asset.plan.replaces_current")
    shown = media_service.resolved_media(base).get(kind)
    if shown is None or shown.path is None:
        return t("asset.plan.slot_empty")
    return t("asset.plan.precedes", name=shown.path.name)


def _made_from_replaced(plan: ImportPlan, item: PlannedItem) -> list[str]:
    """The tables a patch made from the file this item deletes, by filename."""
    if item.action != "replace_vpx" or plan.new_game_dir_name:
        return []
    return (made_from(table_settings(Path(plan.game_dir)), item.replaces)
            if item.replaces else [])


def _candidate_dict(one: arrival.Candidate) -> dict:
    return {"member": one.member, "name": one.name, "size": one.size, "basis": one.basis,
            "width": one.width, "height": one.height}


def _group_dict(group: arrival.Group) -> dict:
    return {"key": group.key, "kind": group.kind, "media_kind": group.media_kind,
            "label": group.label, "preselected": group.preselected, "extra": group.extra,
            "table_id": group.table_id,
            "candidates": [_candidate_dict(one) for one in group.candidates]}


def _unclaimed_dict(one: arrival.Unclaimed) -> dict:
    return {"member": one.member, "name": one.name, "size": one.size,
            "choices": [{"kind": c.kind, "media_kind": c.media_kind, "label": c.label,
                         "table_id": c.table_id, "suggested": c.suggested}
                        for c in one.choices]}


def _left_out_dict(one: arrival.LeftOut) -> dict:
    return {"member": one.member, "name": one.name, "reason_key": one.reason_key,
            "reason": one.reason}


def _convert_of(drop: drop_convert.Drop | None, item: PlannedItem) -> dict | None:
    """What the row of a file that is converted on the way in shows and offers."""
    if drop is None or item.asset.kind != "media":
        return None
    return drop.view_of(item.asset)


def _info_of(plan: ImportPlan, item: PlannedItem, request: Mapping[str, Any]) -> dict | None:
    """Offers what to do with an `.info`: how it merges into details already on this disk,
    or, where there are none, what a first import takes (`facts`, or `all` with the plays)."""
    if item.action != "write_info":
        return None
    asked = (request.get("info_modes") or {}).get(item.member)
    if plan.new_game_dir_name or not Path(item.destination).exists():
        return {"existing": False, "mode": first_mode(asked), "modes": [FACTS, ALL]}
    return {"existing": True, "mode": asked or FILL, "modes": list(INFO_MODES)}


def _plan_to_dict(plan: ImportPlan, found: arrival.Arrival | None = None,
                  drop: drop_convert.Drop | None = None,
                  request: Mapping[str, Any] | None = None) -> dict:
    views = [_convert_of(drop, item) for item in plan.items]
    return {
        "game_dir": plan.game_dir,
        "new_game_dir_name": plan.new_game_dir_name,
        "rom_name": plan.rom_name,
        "items": [
            {
                "index": index,
                "kind": item.asset.kind,
                "label": spec_for(item.asset.kind).label,
                "label_key": f"asset.kind.{item.asset.kind}.label",
                "detail": item.asset.detail,
                "destination": item.destination,
                "action": item.action,
                "default_enabled": item.default_enabled,
                "size": item.asset.size,
                "media_kind": item.asset.media_kind,
                # What the file is called, and what it does to what is already there.
                # Both are answers about this machine's disk, so a caller cannot work
                # them out and would have to ask anyway.
                "name": _item_name(item),
                "replaces": _replaces(plan, item),
                "made_from_it": _made_from_replaced(plan, item),
                "swap": _swap_of(plan, item),
                "member": item.member,
                "basis": item.basis,
                "extra": item.extra,
                "convert": views[index],
                "info": _info_of(plan, item, request or {}),
            }
            for index, item in enumerate(plan.items)
        ],
        "convert": _convert_state(drop, views),
        "blocked": _blocked(plan),
        "choice": _choice_of(plan),
        "tables": list(plan.tables or (found.tables if found else ())),
        "scope": _scope_of(found),
        "groups": [_group_dict(one) for one in found.groups] if found else [],
        "unclaimed": [_unclaimed_dict(one) for one in found.unclaimed] if found else [],
        "left_out": [_left_out_dict(one) for one in found.left_out] if found else [],
    }


def _convert_state(drop: drop_convert.Drop | None, views: list[dict | None]) -> dict | None:
    """What the drop as a whole needs to know about converting: whether FFmpeg is there, and
    whether any file is re-encoded, which is when a table playing matters. None where no
    file in the drop converts."""
    if drop is None or not any(views):
        return None
    return {"ffmpeg": drop.ffmpeg_state(), "usable": not drop.lacks_ffmpeg_for(views),
            "reencodes": any(one and one.get("reencodes") for one in views),
            "wait": drop.wait}


def _scope_of(found: arrival.Arrival | None) -> str:
    """What media is named for: the table the drop landed on, or the game. Empty where the
    drop named no table or the game has one to name it for."""
    if found is None or not found.target.table_id or not found.tables:
        return ""
    return "game" if found.target.scope == "game" else "table"


def _swap_of(plan: ImportPlan, item: PlannedItem) -> dict | None:
    """The table a replacing item takes the place of, and each file named for it with
    whether it is carried to the new one. None for every other item."""
    if item.action != "replace_vpx" or plan.new_game_dir_name:
        return None
    game_id = table_swap.game_id_of(Path(plan.game_dir))
    if not game_id:
        return None
    found = table_swap.plan(game_id, item.table_id, item.destination, item.instead,
                            brings=item.brings)
    found["files"] = [{**one, "carry": one["kind"] in item.carry} for one in found["files"]]
    return found


def _choice_of(plan: ImportPlan) -> dict | None:
    """What the confirmation offers a game that already has a table: replace one of them
    or keep both. None for a new game, a drop with no table in it, or a game with no table
    that can be swapped."""
    if plan.new_game_dir_name or not plan.game_dir:
        return None
    joining = [one for one in plan.items if one.asset.kind == "table"]
    if not joining:
        return None
    game_id = table_swap.game_id_of(Path(plan.game_dir))
    held = table_swap.choices(game_id) if game_id else []
    if not held:
        return None
    swap = next((one.table_id for one in joining if one.action == "replace_vpx"), "")
    return {"tables": held, "replace": bool(swap),
            "table": swap or next((one["id"] for one in held if one["default"]), held[0]["id"])}


def _blocked(plan: ImportPlan) -> list[dict]:
    return [{"kind": one.asset.kind, "reason": one.reason} for one in plan.blocked]


def _session_dir(upload_id: str) -> Path:
    try:
        return upload_session_service.get_session_dir(upload_id)
    except UnknownSessionError as exc:
        raise service_errors.NotFoundError(why(exc)) from exc


def _vps_entry(vps_id: str) -> dict | None:
    vps_id = (vps_id or "").strip()
    if not vps_id:
        return None
    entry = find_vps_entry(vps_id)
    if entry is None:
        raise service_errors.RefusedError(
            t("error.uploads.unknown_vps_id", vps_id=(vps_id)))
    return entry


def begin(expected_bytes: int = 0) -> dict[str, Any]:
    """A session to upload into. `expected_bytes` is what the sender is about to send:
    refused here, with the reason, where the drive beside the library has no room for it."""
    return {"id": upload_session_service.begin_session(
        expected_bytes=max(0, expected_bytes)).upload_id}


def begin_from(path: str) -> dict[str, Any]:
    """A session over a file, a folder or an archive already on this machine, bounded the
    way browsing is."""
    source = media_browse.within_roots(path)
    if not source.exists():
        raise service_errors.RefusedError(t("error.filesystem.nothing_there"),
                                          details={"path": path})
    return begin_over(source)


def begin_over(source: Path) -> dict[str, Any]:
    """A session over `source`, which the caller has already judged readable."""
    return {"id": upload_session_service.begin_session(source=source).upload_id}


def summary(upload_id: str) -> dict[str, Any]:
    try:
        return upload_session_service.finish_session(upload_id)
    except UnknownSessionError as exc:
        raise service_errors.NotFoundError(why(exc)) from exc


def abort(upload_id: str) -> None:
    """Throw the staged files away, unless an import is reading them: that one ends the
    session itself, or leaves it to expire."""
    if upload_session_service.is_held(upload_id):
        return
    upload_session_service.cleanup_session(upload_id)


def add_file(upload_id: str, relpath: str, stream: IO[bytes]) -> dict[str, Any]:
    try:
        return {"bytes": upload_session_service.store_file(upload_id, relpath, stream)}
    except UnknownSessionError as exc:
        raise service_errors.NotFoundError(why(exc)) from exc
    except UnsafePathError as exc:
        raise service_errors.RefusedError(why(exc)) from exc


@contextlib.contextmanager
def file_writer(upload_id: str, relpath: str, size: int = 0) -> Iterator[Callable[[bytes], None]]:
    """What takes the chunks of one file, for a sender that streams it. `size` is what it
    says it is sending, and a drive with no room for that refuses before any is written."""
    try:
        working_folder.ensure_room(max(0, size))
        with upload_session_service.writer(upload_id, relpath) as put:
            yield put
    except UnknownSessionError as exc:
        raise service_errors.NotFoundError(why(exc)) from exc
    except UnsafePathError as exc:
        raise service_errors.RefusedError(why(exc)) from exc


def _source_of(upload_id: str) -> Path | None:
    try:
        return upload_session_service.get_session_source(upload_id)
    except UnknownSessionError as exc:
        raise service_errors.NotFoundError(why(exc)) from exc


def _session_source(upload_id: str) -> Path:
    source = _source_of(upload_id)
    return source if source is not None else session_source(_session_dir(upload_id))


def _loose_files(upload_id: str) -> list[Path]:
    """The files a session holds when it is one file or loose files only; none for a folder
    or an archive."""
    source = _source_of(upload_id)
    if source is not None:
        return [source] if source.is_file() else []
    session = _session_dir(upload_id)
    if any(one.is_dir() for one in session.iterdir()):
        return []
    return [one for one in session.iterdir() if one.is_file()]


def single_image(upload_id: str) -> Path:
    """The one picture a session holds, for a place that takes exactly one.

    Refused before anything is read where it is several files, a folder or archive, or a
    file that is not an image: a table dropped here is a large file for no reason.
    """
    files = _loose_files(upload_id)
    if len(files) != 1:
        raise service_errors.RefusedError(t("error.uploads.picture_file"))
    if files[0].suffix.lower() not in IMAGE_FAMILY:
        raise service_errors.RefusedError(t("error.uploads.not_picture", name=files[0].name))
    return files[0]


def picture(upload_id: str, name: str, size: int | None) -> sized_media.Served:
    """A picture in the session, at `size`: a loose image by the name it was dropped under,
    or a member of an archive or folder by its path inside it, taken out to a folder of its
    own. Anything else is a 404.
    """
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_FAMILY and suffix not in CONVERTIBLE_IMAGE:
        raise service_errors.NotFoundError(t("error.uploads.no_picture", name=name))
    size = sized_media.size_or_refuse(size, "image")
    source = _source_of(upload_id) or _session_dir(upload_id)
    chosen = _loose_picture(source, name) or _member_picture(upload_id, source, name)
    if chosen is not None and suffix in CONVERTIBLE_IMAGE:
        chosen = _preview_of(upload_id, chosen)
    if chosen is None:
        raise service_errors.NotFoundError(t("error.uploads.no_picture", name=name))
    return sized_media.served(chosen, size)


def _preview_of(upload_id: str, picture_path: Path) -> Path | None:
    """A PNG of a picture a browser cannot draw, made once. None where it does not open."""
    from PIL import Image

    kept = upload_session_service.picture_folder(upload_id) / (
        hashlib.blake2b(str(picture_path).encode(), digest_size=8).hexdigest() + "-preview.png")
    if kept.is_file():
        return kept
    try:
        with Image.open(picture_path) as opened:
            opened.load()
            drawn = opened.convert("RGBA" if "A" in opened.getbands() else "RGB")
        with atomic_write.staged_for(kept) as partial:
            drawn.save(partial, "PNG")
        return kept
    except Exception:  # noqa: BLE001 - a picture that does not open is one that is not there
        logger.warning("Could not draw %s", picture_path.name, exc_info=True)
        return None


def _loose_picture(source: Path, name: str) -> Path | None:
    wanted = PurePosixPath(name).name
    if wanted != name:
        return None
    if source.is_file():
        return source if source.name == wanted else None
    if (exact := source / wanted).is_file():
        return exact
    found = sorted(source.rglob(wanted), key=lambda one: (len(one.parts), one))
    return next((one for one in found if one.name == wanted and one.is_file()), None)


def _member_picture(upload_id: str, source: Path, name: str) -> Path | None:
    """The member named `name` of the archive or folder the session reads, extracted once.
    None where the member is not in it, or it is too big to draw."""
    kept = (upload_session_service.picture_folder(upload_id)
            / f"{hashlib.blake2b(name.encode(), digest_size=8).hexdigest()}"
              f"{PurePosixPath(name).suffix.lower()}")
    if kept.is_file():
        return kept
    held = source if source.is_file() else _session_source(upload_id)
    reader, entries, _error = open_listed(held)
    if reader is None:
        return None
    try:
        entry = next((one for one in entries if not one.is_dir and one.arcname == name), None)
        if entry is None or entry.size > arrival.MEASURE_LIMIT:
            return None
        with atomic_write.staged_for(kept) as partial:
            reader.extract_member(entry.path, partial)
        return kept
    except Exception:  # noqa: BLE001 - a damaged member is a picture that is not there
        logger.warning("Could not take %s out of the upload", name, exc_info=True)
        return None
    finally:
        reader.close()


def analysis_of(upload_id: str) -> dict[str, Any]:
    return _analysis_to_dict(analyze_path(_session_source(upload_id)))


def _arrival_for(upload_id: str, request: dict[str, Any]) -> arrival.Arrival:
    """What the session holds, read for the slot or table the request names, with the
    sender's assignments taken in. Refused where the request is wrong, 422 where nothing
    can be made of the drop."""
    game_dir = request.get("game_dir") or ""
    if (request.get("media_kind") or request.get("asset_kind") == "readme") and not game_dir:
        raise service_errors.RefusedError(t("error.uploads.slot_game"))
    try:
        found = arrival.read(
            _session_source(upload_id), game_dir=game_dir or None,
            media_kind=request.get("media_kind") or "",
            asset_kind=request.get("asset_kind") or "",
            rom_name=require_plain_rom(request.get("rom_name") or ""),
            table_id=request.get("table_id") or "", scope=request.get("scope") or "",
            page=request.get("page") or 1)
        if request.get("assignments"):
            found = arrival.apply(found, request["assignments"])
    except ValueError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    if found.refusal:
        raise UnprocessableUploadError(found.refusal)
    return found


def _drop_for(upload_id: str, found: arrival.Arrival,
              request: dict[str, Any]) -> drop_convert.Drop:
    return drop_convert.Drop(found.source_path, upload_session_service.picture_folder(upload_id),
                             request.get("convert"))


def _built_plan(found: arrival.Arrival, request: dict[str, Any], *,
                require_picks: bool, drop: drop_convert.Drop | None = None) -> ImportPlan:
    try:
        return arrival.plan(
            found, picks=request.get("picks"), require_picks=require_picks,
            converting=drop.conversion_for if drop else None,
            allow_new_game=request.get("allow_new_game", False),
            location_id=request.get("location_id") or "",
            add_table=bool(request.get("add_table")),
            replace_table=request.get("replace_table") or "",
            carry=request.get("carry"), instead=bool(request.get("instead")))
    except arrival.ChooseOneError as exc:
        raise PicksNeededError(
            why(exc), details={"groups": [_group_dict(one) for one in exc.groups]}) from exc
    except ValueError as exc:
        if any(request.get(named) for named in (
                "media_kind", "asset_kind", "picks", "assignments", "table_id")):
            raise service_errors.RefusedError(why(exc)) from exc
        # Nowhere to put it. Something a person fixes - a share to mount, a location to
        # pick - so it is blocked rather than a fault.
        raise service_errors.BlockedError(why(exc)) from exc


def plan_for(upload_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """What the drop would do, before it does any of it."""
    found = _arrival_for(upload_id, request)
    vps_entry = _vps_entry(request.get("vps_id") or "")
    drop = _drop_for(upload_id, found, request)
    plan = _built_plan(found, request, require_picks=False, drop=drop)
    return _plan_to_dict(_as_asked(plan, request, vps_entry, None), found, drop, request)


def _as_asked(plan: ImportPlan, request: dict[str, Any], vps_entry: dict | None,
              selected: list[int] | None) -> ImportPlan:
    """The plan narrowed to `selected`, and a new game's folder named: the name sent,
    else the VPS entry's, else the one the drop suggested."""
    new_name = request.get("new_game_dir_name")
    if new_name is None and vps_entry is not None:
        new_name = vps_folder_name(vps_entry)
    try:
        return select_plan_items(plan, selected, new_name)
    except ValueError as exc:
        raise service_errors.RefusedError(why(exc)) from exc


def _declared_identities(declared: Mapping[str, Any] | None) -> dict:
    """Turn the caller's declared identities into the vocabulary the writer speaks.

    Rejected here rather than recorded and regretted: a claim that names an upstream
    record without saying how it is known, or that sends a basis outside the closed set,
    is a caller asserting a confidence it has not earned.
    """
    if not declared:
        return {}
    out, refused = {}, []
    for name, sent in declared.items():
        identity = identity_claims.DeclaredIdentity(
            vps_file_id=sent.vps_file_id, host_item_id=sent.host_item_id,
            host=sent.host, game_id=sent.game_id, table_id=sent.table_id,
            confirmed_by=sent.confirmed_by)
        refused += [{"file": name, "why": why} for why in identity.problems()]
        out[name] = identity
    if refused:
        first = refused[0]
        raise service_errors.RefusedError(
            t("error.uploads.declared_identity_refused", file=first["file"], why=first["why"]),
            details={"refused": refused})
    return out


def _run(plan: ImportPlan, source: Path, declared: dict, upload_id: str,
         drop: drop_convert.Drop | None = None, said: drop_convert.Said | None = None,
         halted: Callable[[], bool] | None = None,
         info_modes: Mapping[str, str] | None = None) -> dict[str, Any]:
    blocked = _blocked(plan)
    converted: Mapping[str, Path] = {}
    clean: Callable[[], None] = lambda: None  # noqa: E731
    if drop is not None:
        converted, clean = drop.convert_all(
            [item.asset for item in plan.items if item.asset.convert],
            said or (lambda *_: None), halted or (lambda: False))
    try:
        if halted is not None and halted():
            raise drop_convert.StoppedError
        report = execute_import_plan(plan, source, declared=declared, converted=converted,
                                      info_modes=info_modes)
    except SwapKeptError as exc:
        raise service_errors.RefusedError(
            why(exc), details={"replaced_tables": exc.replaced}) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    except OSError as exc:
        raise working_folder.out_of_room(exc) or exc from exc
    finally:
        clean()
    upload_session_service.cleanup_session(upload_id)
    report["blocked"] = blocked
    return report


def _accounted(found: arrival.Arrival) -> list[str]:
    """Every member of the drop the plan showed somewhere."""
    members = [member for claim in found.pool for member in claim.members]
    members += [one.member for one in found.unclaimed]
    members += [one.member for one in found.left_out]
    return sorted(set(members))


@dataclass(frozen=True)
class _Prepared:
    found: arrival.Arrival
    plan: ImportPlan
    identities: dict
    drop: drop_convert.Drop
    vps_entry: dict | None
    info_modes: Mapping[str, str]


def _prepared(upload_id: str, request: dict[str, Any],
              declared: Mapping[str, Any] | None) -> _Prepared:
    """Everything an import needs settled before it starts, so that a request that cannot
    be carried out is refused whole and not part way through."""
    # Before the session is even looked up: a claim we cannot trust is a bad request
    # whichever upload it names, and saying so early keeps the reason readable.
    identities = _declared_identities(declared)

    found = _arrival_for(upload_id, request)
    vps_entry = _vps_entry(request.get("vps_id") or "")
    drop = _drop_for(upload_id, found, request)
    plan = _built_plan(found, request, require_picks=True, drop=drop)
    if vps_entry is not None and not plan.new_game_dir_name:
        raise service_errors.RefusedError(t("error.uploads.vps_new_only"))

    # No selection sent takes what the plan ticked: an extra or an unticked file is offered,
    # not imported.
    selected = request.get("selected")
    if selected is None:
        selected = [index for index, item in enumerate(plan.items) if item.default_enabled]
    asked = _as_asked(plan, request, vps_entry, selected)
    if not asked.items:
        raise NothingImportableError(t("error.uploads.nothing_importable"),
                                     details={"blocked": _blocked(asked)})
    return _Prepared(found, asked, identities, drop, vps_entry,
                     request.get("info_modes") or {})


def _finished(upload_id: str, ready: _Prepared, said: drop_convert.Said | None = None,
              halted: Callable[[], bool] | None = None) -> dict[str, Any]:
    found, plan = ready.found, ready.plan
    report = _run(plan, found.source_path, ready.identities, upload_id, ready.drop, said,
                  halted, ready.info_modes)
    held = set(_accounted(found))
    written = sorted({one.arcname for item in plan.items for one in item.asset.entries
                      if one.arcname in held})
    report["written"] = written
    report["not_written"] = [member for member in _accounted(found) if member not in written]
    if report.get("new_game"):
        if ready.vps_entry is not None:
            _associate(report, ready.vps_entry)
        else:
            _guess(report)
    return report


def execute(upload_id: str, request: dict[str, Any],
            declared: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bring the session into the library, and clear it."""
    return _finished(upload_id, _prepared(upload_id, request, declared))


def start_import(upload_id: str, request: dict[str, Any],
                 declared: Mapping[str, Any] | None = None) -> jobs.Job:
    """`execute` as a job a caller watches: refused before it starts for anything `execute`
    would refuse. `Job.parts` has one entry per converted file, by its name in the drop. A
    stopped import has added nothing and leaves the session as it was."""
    ready = _prepared(upload_id, request, declared)
    parts: dict[str, dict[str, object]] = {}
    upload_session_service.hold(upload_id)

    def work(job: jobs.Job) -> dict[str, Any]:
        def said(member: str, state: str, fraction: float) -> None:
            parts[member] = {"member": member, "state": state, "pct": int(fraction * 100)}
            job.parts = list(parts.values())
            done = sum(1 for one in parts.values() if one["state"] == drop_convert.DONE)
            job.progress(done, len(parts), PurePosixPath(member).name)

        try:
            return _finished(upload_id, ready, said, job.halt.is_set)
        except drop_convert.StoppedError:
            job.stopped = True
            return {"stopped": True}
        except service_errors.RefusedError as exc:
            kept = (exc.details or {}).get("replaced_tables")
            if not kept:
                raise
            return {"failed": True, "error": why(exc), "replaced_tables": kept}
        finally:
            upload_session_service.release(upload_id)

    try:
        return jobs.submit(jobs.KIND_UPLOAD_IMPORT, work, stoppable=True,
                           stop_link="/api/v1/uploads/import/stop")
    except BaseException:
        upload_session_service.release(upload_id)
        raise


def stop_import() -> bool:
    """Ask a running import to stop before it adds anything. False where none is running."""
    running = jobs.active(jobs.KIND_UPLOAD_IMPORT)
    for job in running:
        job.halt.set()
    return bool(running)


def _guess(report: dict) -> None:
    """Match a new game nobody picked an entry for, from its folder name. Offline, and
    never fatal: the files are on disk whatever this finds."""
    import logging

    from common.games import auto_match, media_fill
    from common.games.game import Game
    from common.games.game_metadata import load_game_meta
    from common.games.game_repository import refresh_game

    game_dir = Path(report["game_dir"])
    game = Game(game_dir_name=game_dir.name, full_path_game=str(game_dir))
    try:
        game.meta_config = load_game_meta(game)
        report["vps_matched"] = auto_match.match_new([game])["matched"] > 0
        refresh_game(game_dir)
        media_fill.request([game_dir])
    except Exception:
        logging.getLogger("vpinfe.common.uploads.upload_ops").exception(
            "Matching the new game failed after import")
        report["vps_matched"] = False


def _associate(report: dict, vps_entry: dict) -> None:
    """Files are on disk; association failure is reported, not fatal."""
    import logging

    from common.games import media_fill
    from common.games.game_metadata import MATCHED_ON_IMPORT
    from common.games.game_service import associate_vps_to_folder

    try:
        associate_vps_to_folder(Path(report["game_dir"]), vps_entry, False,
                                matched_by=MATCHED_ON_IMPORT)
        report["vps_associated"] = True
        media_fill.request([report["game_dir"]])
    except FileNotFoundError as exc:
        report["vps_associated"] = False
        report["vps_error"] = why(exc)
    except Exception:
        logging.getLogger("vpinfe.common.uploads.upload_ops").exception(
            "VPS association failed after import")
        report["vps_associated"] = False
        report["vps_error"] = t("error.uploads.see_log")
