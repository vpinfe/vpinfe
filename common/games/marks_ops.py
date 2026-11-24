"""What a person says about several tables or games at once, written as one job.

Each edit names one table or one game and the marks to set on it; a field left out is
not touched. Games are written one folder at a time, so a selection of tables that share a
game reads and re-reads that game once. A row that cannot be written is named in the
result and the rest go on.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from common import service_errors
from common.failures import why
from common.games import derived_tags, game_lens, game_service, info_files, launcher_ops
from common.games.game_metadata import (
    set_game_favorite,
    set_game_private,
    set_game_rating,
    set_game_tags,
    set_table_rating,
    set_table_tags,
)
from common.games.game_repository import reread_game
from common.games.info_file import MetaConfig
from common.games.table_ops import filename_or_refuse
from common.i18n import t
from common.runtime import jobs, shutdown

logger = logging.getLogger(__name__)

TABLE_FIELDS = ("rating", "tags", "hidden", "delete_nvram_on_close", "launcher")
GAME_FIELDS = ("rating", "tags", "favorite", "private")


def refuse_misshapen(edits: list[dict[str, Any]]) -> None:
    """Refuse before anything is written when an edit names a field its row cannot take."""
    if not edits:
        raise service_errors.RefusedError(t("error.marks.none"))
    for edit in edits:
        allowed = TABLE_FIELDS if edit.get("table_id") else GAME_FIELDS
        stray = [name for name in _marks(edit) if name not in allowed]
        if stray:
            raise service_errors.RefusedError(t("error.marks.not_here", keys=", ".join(stray)))


def start(edits: list[dict[str, Any]]) -> jobs.Job:
    """Write every edit, grouped by game, as a job whose result is
    `{"rows", "written", "failed"}`; a row stopped before is in neither list."""
    refuse_misshapen(edits)
    by_game: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        by_game.setdefault(str(edit["game_id"]), []).append(edit)

    def work(job: jobs.Job) -> dict[str, Any]:
        reporter = job.reporter()
        written: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        done = 0
        for game_id, group in by_game.items():
            if shutdown.requested():
                break
            reporter.progress(done, len(edits), t("said.marks", count=len(edits) - done))
            _write_game(game_id, group, written, failed)
            done += len(group)
        return {"rows": len(edits), "written": written, "failed": failed}

    return jobs.submit(jobs.KIND_MARKS, work)


def _marks(edit: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in edit.items()
            if name not in ("game_id", "table_id") and value is not None}


def _write_game(game_id: str, group: list[dict[str, Any]],
                written: list[dict[str, str]], failed: list[dict[str, str]]) -> None:
    try:
        game = game_lens.game_for_writing(game_id)
    except Exception as exc:  # noqa: BLE001 - the game's rows are named, the rest go on
        _log(exc, game_id)
        failed.extend(_named(edit, why(exc)) for edit in group)
        return
    wrote = False
    for edit in group:
        try:
            _write_one(game, edit)
        except Exception as exc:  # noqa: BLE001 - one row is named, the rest go on
            _log(exc, game_id)
            failed.append(_named(edit, why(exc)))
        else:
            wrote = True
            written.append(_named(edit))
    if wrote:
        reread_game(game)


def _write_one(game: Any, edit: dict[str, Any]) -> None:
    marks = _marks(edit)
    table_id = str(edit.get("table_id") or "")
    if not table_id:
        _write_game_marks(game, marks)
        return
    filename = filename_or_refuse(game, table_id)
    if "tags" in marks:
        derived_tags.refuse_added(game, list(marks["tags"]))
    if "rating" in marks:
        set_table_rating(game, filename, marks["rating"])
    if "tags" in marks:
        set_table_tags(game, filename, list(marks["tags"]))
    if "hidden" in marks:
        MetaConfig(str(info_files.info_path_of(game))).set_table_hidden(
            filename, bool(marks["hidden"]))
    if "delete_nvram_on_close" in marks:
        game_service.update_table_vpinfe_setting(
            Path(str(game.full_path_game)), table_id, "delete_nvram_on_close",
            bool(marks["delete_nvram_on_close"]))
    if "launcher" in marks:
        launcher_ops.assign(table_id, str(marks["launcher"]))


def _write_game_marks(game: Any, marks: dict[str, Any]) -> None:
    if "tags" in marks:
        derived_tags.refuse_added(game, list(marks["tags"]))
        set_game_tags(game, list(marks["tags"]))
    if "rating" in marks:
        set_game_rating(game, marks["rating"])
    if "favorite" in marks:
        set_game_favorite(game, bool(marks["favorite"]))
    if "private" in marks:
        set_game_private(game, bool(marks["private"]))


def _named(edit: dict[str, Any], reason: str = "") -> dict[str, str]:
    named = {"game": str(edit["game_id"]), "table": str(edit.get("table_id") or "")}
    if reason:
        named["reason"] = reason
    return named


def _log(exc: Exception, game_id: str) -> None:
    if not isinstance(exc, (ValueError, LookupError, OSError, service_errors.ServiceError)):
        logger.warning("Writing marks for game %s failed", game_id, exc_info=True)
