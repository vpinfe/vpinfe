"""A finished job's result, as the one line the Jobs view shows beside it.

A result of a shape not named here has no line: a count read off the wrong shape would
say something false.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from common.i18n import t
from common.runtime import jobs


def of(kind: str, result: object) -> str:
    """The line for `result`, or "" where this kind of result has none."""
    describe = _KINDS.get(kind)
    return describe(result) if describe else ""


def _library_scan(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if "games" in result and "new_games" in result:
        # A library read: how many games, how many it had not seen, how many of those matched.
        new = int(result.get("new_games") or 0)
        return t("said.job_library_new" if new else "said.job_library_read",
                 count=int(result["games"]), new=new,
                 matched=int(result.get("new_matched") or 0))
    if "found" in result and "not_found" in result:
        return t("said.job_scan_found", found=int(result["found"]),
                 missing=int(result["not_found"]))
    return ""


def auto_matched(counts: dict[str, int]) -> str:
    """The line for a match-again pass, from the counts it answered."""
    return t("said.job_auto_matched", changed=int(counts.get("changed") or 0),
             unmatched=int(counts.get("unmatched") or 0))


def info_upgraded(result: Any) -> str:
    return t("said.job_info_upgraded", upgraded=int(result["upgraded"]),
             current=int(result["already_current"]), failed=int(result["failed"]))


def info_restored(result: Any) -> str:
    return t("said.job_info_restored", restored=int(result["restored"]),
             failed=int(result["failed"]))


def _media_fill(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if "filled" in result:
        said = {"count": int(result.get("games") or 0), "filled": int(result["filled"]),
                "unmatched": int(result.get("unmatched") or 0),
                "failed": int(result.get("failed") or 0)}
        skipped = int(result.get("skipped") or 0)
        if skipped:
            return t("said.job_filled_left", skip=skipped, no=said["unmatched"],
                     count=said["count"], filled=said["filled"], failed=said["failed"])
        return t("said.job_media_filled", **said)
    if "updated" in result:
        said = {"count": int(result.get("games") or 0), "updated": int(result["updated"]),
                "failed": int(result.get("failed") or 0)}
        skipped = int(result.get("skipped") or 0)
        if skipped:
            return t("said.job_replaced_left", skipped=skipped, **said)
        return t("said.job_media_replaced", **said)
    return ""


def _media_look(result: Any) -> str:
    if not isinstance(result, dict) or "found" not in result:
        return ""
    return t("said.job_media_looked", count=int(result.get("games") or 0),
             found=int(result["found"]), unread=int(result.get("unread") or 0))


def _media_rotate(result: Any) -> str:
    if not isinstance(result, dict) or "turned" not in result:
        return ""
    return t("said.job_media_turned", count=int(result.get("games") or 0),
             turned=int(result["turned"]), skipped=len(result.get("skipped") or ()))


def _media_convert(result: Any) -> str:
    if not isinstance(result, dict) or "converted" not in result:
        return ""
    return t("said.job_media_converted", count=int(result.get("games") or 0),
             converted=int(result["converted"]) + int(result.get("renamed") or 0),
             skipped=len(result.get("skipped") or ()))


def _upload_import(result: Any) -> str:
    if not isinstance(result, dict) or "written" not in result:
        return ""
    return t("said.job_upload_import", count=len(result["written"]))


def _backups_moved(result: Any) -> str:
    if not isinstance(result, dict) or "moved" not in result:
        return ""
    return t("said.job_backups_moved", count=int(result["moved"]),
             left=len(result.get("left") or ()), failed=len(result.get("failed") or ()))


def _table_settings(result: Any) -> str:
    if not isinstance(result, dict) or "written" not in result:
        return ""
    return t("said.job_table_settings", count=len(result["written"]),
             failed=len(result.get("failed") or ()))


def _backglass_order(result: Any) -> str:
    if not isinstance(result, dict) or "written" not in result:
        return ""
    return t("said.job_table_order", count=len(result["written"]),
             failed=len(result.get("failed") or ()))


def _marks(result: Any) -> str:
    if not isinstance(result, dict) or "written" not in result:
        return ""
    return t("said.job_marks", count=len(result["written"]),
             failed=len(result.get("failed") or ()))


def _capture(result: Any) -> str:
    run = result.get("run") if isinstance(result, dict) else None
    if not isinstance(run, dict) or "of" not in run:
        return ""
    return t("said.job_recorded", done=int(run.get("done") or 0), total=int(run["of"]))


def _tool(result: Any) -> str:
    version = str(result.get("version") or "") if isinstance(result, dict) else ""
    return t("said.job_tool_version", version=version) if version else ""


def _dof_config(result: Any) -> str:
    if not isinstance(result, dict) or "changed" not in result:
        return ""
    return t("said.dof_config.updated" if result["changed"] else "manager.settings.already_date")


_KINDS: dict[str, Callable[[Any], str]] = {
    jobs.KIND_LIBRARY_SCAN: _library_scan,
    jobs.KIND_MEDIA_FILL: _media_fill,
    jobs.KIND_MEDIA_LOOK: _media_look,
    jobs.KIND_MEDIA_ROTATE: _media_rotate,
    jobs.KIND_MEDIA_CONVERT: _media_convert,
    jobs.KIND_UPLOAD_IMPORT: _upload_import,
    jobs.KIND_BACKUPS_MOVE: _backups_moved,
    jobs.KIND_MEDIA_CAPTURE: _capture,
    jobs.KIND_TOOL_GET: _tool,
    jobs.KIND_TABLE_SETTINGS: _table_settings,
    jobs.KIND_MARKS: _marks,
    jobs.KIND_BACKGLASS_ORDER: _backglass_order,
    jobs.KIND_DOF_CONFIG: _dof_config,
}
