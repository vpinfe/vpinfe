"""A kept report's games as groups, and the addresses that lead to them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from common.i18n import t
from manager.kit import deeplink

# The Manager names its window with this, and a report opened from it keeps the opener, so a
# link in the report's tab that targets this name lands in the Manager's tab.
MANAGER_WINDOW = "vpinfe-manager"

ADDRESS = "/manager/report"

# A group longer than this is shown as a few rows and a link to all of it in Tables.
SHOW_ALL_OVER = 3

IMPORTED = "imported"
ALREADY_HERE = "already_here"
# The groups the importer records per game in the result's `items`, each with its label.
NEED_ROM = "need_rom"
NOT_MATCHED = "not_matched"
NO_WHEEL = "no_wheel"
DMD_ALONE = "dmd_alone"
# Listed in the report only: it is files, so Tables has no group of games to narrow to.
NOT_BROUGHT = "not_brought"
# Counted from the library when the report is shown, not recorded by the importer.
UNSUPPORTED = "unsupported"
FROM_ITEMS = {NEED_ROM: "manager.report.need_rom", NOT_MATCHED: "manager.report.not_matched",
              NO_WHEEL: "manager.report.no_wheel", DMD_ALONE: "manager.report.dmd"}


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    game_ids: tuple[str, ...]


def groups(job: dict[str, Any]) -> dict[str, Group]:
    """The groups of games a job's result names, by key. A group with no game is absent.

    Imported holds the games that came with a game file: Tables lists one row per file, so
    a game that came as artwork alone has nothing there to show.
    """
    result = job.get("result") or {}
    made = [str(row["game_id"]) for row in _made(job) if row.get("table") is not False]
    held = [str(row["game_id"]) for row in result.get("already_here") or []
            if row.get("game_id")]
    found = {IMPORTED: Group(IMPORTED, t("manager.report.imported"), tuple(dict.fromkeys(made))),
             ALREADY_HERE: Group(ALREADY_HERE, t("manager.report.already_here"),
                                 tuple(dict.fromkeys(held)))}
    for key, label in FROM_ITEMS.items():
        asked = [str(one["game_id"]) for one in result.get("items") or []
                 if one.get("group") == key and one.get("game_id")]
        found[key] = Group(key, t(label), tuple(dict.fromkeys(asked)))
    return {key: one for key, one in found.items() if one.game_ids}


def _made(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows this run made, whatever they hold: no error, not a game left alone."""
    result = job.get("result") or {}
    return [row for row in result.get("rows") or []
            if row.get("game_id") and not row.get("error") and not row.get("existing")]


def made_games(job: dict[str, Any]) -> tuple[str, ...]:
    """Every game this run made, whether or not it came with a game file."""
    return tuple(dict.fromkeys(str(row["game_id"]) for row in _made(job)))


def tables_made(job: dict[str, Any]) -> int:
    """The game files this run made, which is how many rows Imported shows in Tables."""
    return sum(1 for row in _made(job) if row.get("table") is not False)


def artwork_alone(job: dict[str, Any]) -> int:
    """The games that came as artwork with no game file, so Tables has no row for them."""
    held = {str(row["game_id"]) for row in _made(job) if row.get("table") is not False}
    return len({str(row["game_id"]) for row in _made(job)} - held)


def report_address(job_id: str) -> str:
    """The report in a tab of its own."""
    return f"{ADDRESS}?{urlencode({'job': job_id})}"


def tables_address(job_id: str, group: str) -> str:
    """Tables, narrowed to one group of one report."""
    return "/manager?" + deeplink.query({"view": "tables", "report": job_id, "group": group})


def media_address(group: str) -> str:
    """Media, on the view that holds one group of files."""
    return "/manager?" + urlencode({"view": "media", "group": group})
