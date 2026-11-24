"""Recordings kept on the device until a person uses one or throws it away, one folder
each under `ROOT`."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common import service_errors
from common.config.paths import CAPTURE_DIR
from common.games import asset_origin, game_repository
from common.i18n import t
from common.timestamps import utc_now_iso

from . import slots

logger = logging.getLogger("vpinfe.common.capture.proposals")

ROOT = CAPTURE_DIR / "proposals"
ABOUT = "proposal.json"

Place = Callable[..., Any]


def _place(game_id: str, kind: str, table_id: str, source: Path, origin: str,
           md5: str) -> Any:
    from common.games import media_ops

    return media_ops.place_file(game_id, kind, table_id, source, origin, md5)


def keep(game_id: str, table_id: str, kind: str, source: Path) -> dict[str, Any]:
    """Keep `source` as a proposal for this slot, and answer it."""
    proposal_id = uuid.uuid4().hex[:12]
    folder = ROOT / proposal_id
    folder.mkdir(parents=True)
    kept = folder / f"{kind}{source.suffix}"
    shutil.copy2(source, kept)
    about = {"id": proposal_id, "game_id": game_id, "table_id": table_id, "kind": kind,
             "file": kept.name, "created": utc_now_iso()}
    (folder / ABOUT).write_text(json.dumps(about, indent=2), encoding="utf-8")
    return about


def _read(folder: Path) -> dict[str, Any] | None:
    try:
        about = json.loads((folder / ABOUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(about, dict) or not (folder / str(about.get("file") or "")).is_file():
        return None
    return about


def _held() -> list[dict[str, Any]]:
    if not ROOT.is_dir():
        return []
    found = [about for folder in sorted(ROOT.iterdir()) if folder.is_dir()
             if (about := _read(folder)) is not None]
    return sorted(found, key=lambda about: str(about.get("created") or ""))


def _row(about: dict[str, Any]) -> dict[str, Any]:
    game_id, table_id = str(about["game_id"]), str(about.get("table_id") or "")
    game = game_repository.game_by_id(game_id)
    replaces = None
    if game is not None:
        row = slots.serving(game_id, table_id, str(about["kind"]))
        if row is not None:
            replaces = {"path": row["path"], "source": slots.source(row),
                        "goes": slots.goes(row, table_id)}
    size = (ROOT / str(about["id"]) / str(about["file"])).stat().st_size
    return {**about, "table_id": table_id, "size": size,
            "name": str(game_repository.game_to_row(game).get("name") or "")
            if game is not None else "",
            "replaces": replaces,
            "url": f"/api/v1/capture/proposals/{about['id']}/file"}


def listing() -> dict[str, Any]:
    """Every proposal waiting, oldest first, with how much room they take together."""
    rows = [_row(about) for about in _held()]
    return {"count": len(rows), "bytes": sum(row["size"] for row in rows),
            "proposals": rows}


def _one(proposal_id: str) -> dict[str, Any]:
    about = _read(ROOT / proposal_id) if proposal_id.isalnum() else None
    if about is None:
        raise service_errors.NotFoundError(t("error.capture.no_such_proposal"),
                                           details={"proposal": proposal_id})
    return about


def file_of(proposal_id: str) -> Path:
    about = _one(proposal_id)
    return ROOT / proposal_id / str(about["file"])


def use(proposal_id: str, place: Place | None = None) -> dict[str, Any]:
    """Place a proposal in its slot, keep the file it replaces in Backups and delete it, and
    forget the proposal."""
    about = _one(proposal_id)
    game_id, table_id, kind = (str(about["game_id"]), str(about.get("table_id") or ""),
                               str(about["kind"]))
    row = slots.serving(game_id, table_id, kind)
    slots.can_keep(game_id, row, table_id)
    written = (place or _place)(game_id, kind, table_id, file_of(proposal_id),
                                asset_origin.RECORDED, "")
    removed = slots.remove(game_id, row, table_id, str((written or {}).get("written") or ""))
    discard(proposal_id)
    slots.refreshed(game_id)
    return {"placed": kind, "removed": removed}


def decide(proposal_id: str, use_it: bool) -> dict[str, Any]:
    if use_it:
        return use(proposal_id)
    discard(proposal_id)
    return {"placed": None, "removed": []}


def discard(proposal_id: str) -> None:
    _one(proposal_id)
    shutil.rmtree(ROOT / proposal_id, ignore_errors=True)


def discard_all() -> dict[str, Any]:
    held = _held()
    for about in held:
        shutil.rmtree(ROOT / str(about["id"]), ignore_errors=True)
    return {"discarded": len(held)}
