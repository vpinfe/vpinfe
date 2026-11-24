"""The play section: plays, high scores and the table chosen to play."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from nicegui import ui

from common.config.labels import field_label
from common.failures import why
from common.i18n import t
from manager.extensions import ext_groups
from manager.kit import offload, panel, play_facts, when
from manager.library import pictures
from manager.workbench.core import HEADING, _rows, _table_line


def _game_play_rows(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The game's own record."""
    return _play_rows(context["game"].get("user") or {})


def _table_play_rows(table: dict[str, Any]) -> list[tuple[Any, Any]]:
    """This file's own record."""
    return _play_rows(table.get("user") or {})


def _play_entries(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Under a table both levels show, each under its own heading."""
    chosen = _chosen_table(context)
    if chosen is None:
        return _game_play_rows(context)
    return [(HEADING, t("manager.workbench.game_section")),
            *_game_play_rows(context),
            (HEADING, t("manager.workbench.table_section")),
            *_table_play_rows(chosen)]


def _chosen_table(context: dict[str, Any]) -> dict[str, Any] | None:
    return next((row for row in context["tables"] if row.get("id") == context["lens"]),
                None)


async def _play_block(context: dict[str, Any]) -> None:
    """What has been done with this, the machine's high score table after it, the
    pictures players took, and the groups extensions add."""
    scores = await _high_scores(context)
    shots = await pictures.read(context)
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, _play_section(context, scores, shots))
        ext_groups.fill(context, "play")


def _play_section(context: dict[str, Any], scores: dict[str, Any],
                  shots: dict[str, Any]) -> list[tuple[Any, ...]]:
    return panel.headed([*_play_entries(context), *_high_score_rows(context, scores),
                         *pictures.rows(context, shots)],
                        t("manager.workbench.game_section"))


async def _high_scores(context: dict[str, Any]) -> dict[str, Any]:
    chosen = _chosen_table(context)
    try:
        return await offload.io(context["library"].high_scores, context["game_id"],
                                str((chosen or {}).get("id") or ""))
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        return {"state": "unreadable", "reason": why(exc), "sections": []}


def _high_score_rows(context: dict[str, Any],
                     scores: dict[str, Any]) -> list[tuple[Any, Any]]:
    """The High Scores group, or nothing where the table has no ROM and no score file."""
    if not scores:
        return []
    tables = context["tables"]
    table: dict[str, Any] = next(
        (row for row in tables if row.get("id") == scores.get("table_id")), {})
    lines: list[tuple[str, str]] = []
    roms = {str(((row.get("dependencies") or {}).get("pinmame") or {}).get("declared") or "")
            for row in tables} - {""}
    if not context["lens"] and table and len(roms) > 1:
        lines.append((t("manager.workbench.high_scores_from",
                        table=_table_line(table, tables)), ""))
    said = HIGH_SCORE_STATES.get(str(scores.get("state") or ""))
    if said is not None:
        text, why_key = said
        lines.append((t(text), t(why_key, rom=str(scores.get("rom") or "")) if why_key
                      else str(scores.get("reason") or "")))
    else:
        clears = bool((table.get("overrides") or {}).get("delete_nvram_on_close"))
        lines.append(_high_scores_read(str(scores.get("read_at") or ""), clears,
                                       str(scores.get("file") or "")))
    rows: list[tuple[Any, Any]] = [(HEADING, t("manager.workbench.high_scores")),
                                   (panel.LEDE, partial(_high_scores_aside, lines))]
    return rows if said is not None else [*rows, *_high_score_entries(scores)]


def _high_scores_aside(lines: list[tuple[str, str]]) -> None:
    with ui.column().classes("gap-0"):
        for text, hint in lines:
            panel.line(text, hint=hint)


# Each state with nothing to list: its line, and the key of its detail. None for the detail
# the answer carries itself.
HIGH_SCORE_STATES: dict[str, tuple[str, str | None]] = {
    "none": ("manager.workbench.high_scores_none", "manager.workbench.scores_pending"),
    "unsupported": ("manager.workbench.high_scores_unsupported",
                    "manager.workbench.scores_no_map"),
    "unreadable": ("manager.workbench.high_scores_unreadable", None),
}


def _high_scores_read(read_at: str, clears: bool, file: str = "") -> tuple[str, str]:
    """(line, detail). `file` is named where the reading came from a text file."""
    at = when.parsed(read_at)
    if clears:
        text, hint = t("manager.workbench.high_scores_cleared"), ""
    elif at is None:
        text = (t("manager.workbench.scores_undated_from", file=file) if file
                else t("manager.workbench.scores_undated"))
        hint = ""
    elif (datetime.now(UTC) - at).total_seconds() < 60:
        text = (t("manager.workbench.scores_fresh_from", file=file) if file
                else t("manager.workbench.scores_fresh"))
        hint = when.local(read_at)
    else:
        ago = when.ago(read_at, inline=True)
        text = (t("manager.workbench.scores_read_from", file=file, when=ago) if file
                else t("manager.workbench.high_scores_read", when=ago))
        hint = when.local(read_at)
    return text, hint


def _high_score_entries(scores: dict[str, Any]) -> list[tuple[Any, Any]]:
    """One row per entry, each column as wide as its longest entry across the whole group
    so every rank, name and score lines up from the first section to the last. A column no
    entry needs is not drawn."""
    sections = [section for section in scores.get("sections") or []
                if section.get("entries")]
    cells = [[play_facts.score_cells(entry) for entry in section["entries"]]
             for section in sections]
    ranking = [_ranks_entries(section) for section in sections]
    columns = _score_columns(sections, cells, ranking)
    at = when.parsed(scores.get("read_at"))
    day = when.day(at.astimezone()) if at else ""
    rows: list[tuple[Any, Any]] = []
    for section, held, ranks in zip(sections, cells, ranking, strict=True):
        for place, (entry, (value, unit, rest)) in enumerate(
                zip(section["entries"], held, strict=True)):
            rank = str(entry["rank"]) if ranks and entry.get("rank") is not None else ""
            rows.append((_section_label(sections, section) if place == 0 else "",
                         _score_row(entry, day, columns, rank=rank, value=value, unit=unit)))
            rows.extend((panel.ASIDE, partial(_score_caption, line)) for line in rest)
    return rows


def _ranks_entries(section: dict[str, Any]) -> bool:
    """Whether the section's places are ranked: a rank on a list of one says nothing."""
    return sum(1 for entry in section["entries"] if entry.get("rank") is not None) > 1


def _section_label(sections: list[dict[str, Any]], section: dict[str, Any]) -> str:
    """The machine's name for the section. A lone one named like the group repeats its
    heading, so it carries none."""
    name = str(section.get("name") or "")
    if len(sections) == 1 and field_label(name).casefold() == t(
            "manager.workbench.high_scores").casefold():
        return ""
    return name


def _score_columns(sections: list[dict[str, Any]], cells: list[list[tuple[str, str, list[str]]]],
                   ranking: list[bool]) -> dict[str, Any]:
    """Which columns the group draws, and the width of each as a count of `ch` that the
    stylesheet multiplies out."""
    entries = [entry for section in sections for entry in section["entries"]]
    values = [value for held in cells for value, _unit, _rest in held]
    units = [unit for held in cells for _value, unit, _rest in held]
    ranks = [str(entry["rank"]) for section, ranked in zip(sections, ranking, strict=True)
             if ranked for entry in section["entries"] if entry.get("rank") is not None]
    initials = [str(entry.get("initials") or "") for entry in entries]
    names = [str(entry.get("player") or "") for entry in entries]

    def widths(**columns: Iterable[float]) -> str:
        return "; ".join("--hs-" + name + ": " + format(max(longest, default=0), ".1f")
                         for name, longest in columns.items())

    return {"rank": bool(ranks), "initials": any(one.strip() for one in initials),
            "player": any(names), "unit": any(units),
            "style": widths(rank=map(len, ranks),
                            init=(1.2 * len(one) for one in initials),
                            player=(1.3 * len(one) for one in names),
                            score=map(_figures, values),
                            unit=(0.9 * len(one) for one in units))}


def _figures(text: str) -> float:
    """How many digits wide `text` draws: a separator is about a third, a letter a bit more."""
    return sum(0.35 if _SEPARATORS.match(one) else 1.2 if one.isalpha() else 1
               for one in text)


_SEPARATORS = re.compile(r"[,.\s\u00a0\u202f'\u2019]")


def _score_row(entry: dict[str, Any], day: str, columns: dict[str, Any], *, rank: str,
               value: str, unit: str) -> Callable[[], None]:
    def draw() -> None:
        with ui.element("div").classes("manager-score-row").style(columns["style"]):
            if columns["rank"]:
                ui.label(rank).classes("manager-score-rank")
            if columns["initials"]:
                ui.label(str(entry.get("initials") or "")) \
                    .classes("manager-score-initials manager-cell-identifier")
            if columns["player"]:
                ui.label(str(entry.get("player") or "")).classes("manager-score-player")
            ui.label(value).classes("manager-score-value")
            if columns["unit"]:
                ui.label(unit).classes("manager-score-unit")
            if entry.get("new"):
                ui.label(t("manager.workbench.high_scores_new")) \
                    .classes("manager-tier manager-tier--off manager-score-new") \
                    .tooltip(t("manager.workbench.scores_dated", day=day))

    return draw


def _score_caption(line: str) -> None:
    ui.label(line).classes("manager-help")


def _play_rows(record: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What has been done with this."""
    return [
        (t("word.last_played"), play_facts.played_when(record.get("last_played"))),
        (t("manager.workbench.times_played"),
                str(int(record.get("play_count") or 0) or t("word.never"))),
        (t("manager.workbench.play_time"),
                play_facts.played_for(int(record.get("play_time_seconds") or 0))),
    ]
