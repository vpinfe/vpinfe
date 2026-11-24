"""Views: a named set of columns, a sort and a filter over one grid.

Two kinds. **Built-ins are constants** - read-only, and nothing about them survives a
reload, which is what makes going back to one a reliable way out rather than a
convention. **Custom views** are the user's.

A built-in may filter, on one condition: its name has to be what somebody would predict
the filter from. "Gaps" showing only what is missing is the view doing what it says; a
filter a reader cannot derive from the name is a grid that lost rows. The funnel shows
what is applied either way, and picking another view clears it.

Physical layout - width, order, pinning - is the grid's, not a view's. See
`grid.Layout`.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from common.i18n import t
from common.ids import new_id

logger = logging.getLogger("vpinfe.manager.kit.views")

# Where the user's own views live, beside the layout the grid saves for itself.
VIEWS_SUFFIX = ".views"


@dataclass(frozen=True)
class View:
    """`columns` is the visible fields; `sort` and `filters` are AG Grid's own models,
    stored as given so applying one is a handoff rather than a translation."""

    id: str
    name: str
    builtin: bool = False
    columns: tuple[str, ...] = ()
    sort: tuple[dict[str, Any], ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    # What this view is for. Ours on a built-in, the user's on one they saved.
    help: str = ""
    # field -> the drawing it uses, where that is not the column's own.
    drawn: dict[str, str] = field(default_factory=dict)


def mint_id() -> str:
    """A new custom view's id.

    Minted rather than derived from the name. An id that is
    a slug of the name changes when the name does, and anything keyed to it - geometry,
    most of all - is orphaned by a rename. The name is a label; this is the identity.
    """
    return f"view:{new_id()}"


@dataclass(frozen=True)
class Preset:
    """A built-in that wants more than columns. Grids declaring only columns pass a
    plain list and never meet this."""

    columns: tuple[str, ...] = ()
    sort: tuple[dict[str, Any], ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    # What the view is for: the job somebody opens it to do. Not a restatement of the
    # filter - a reader can see which rows are here; what they cannot see is why this
    # was worth building a view for.
    help: str = ""
    drawn: dict[str, str] = field(default_factory=dict)
    # Words that arrived already read, from an extension.
    name: str = ""


def builtin_id(key: str) -> str:
    return f"builtin:{key}"


def builtins(presets: Mapping[str, list[str] | Preset]) -> list[View]:
    """The read-only starting points a grid declares, each under the catalog key of its
    name. A preset's own `name` is shown as given instead."""
    return [View(id=builtin_id(key),
                 name=(preset.name if isinstance(preset, Preset) and preset.name
                       else t(key)),
                 builtin=True,
                 columns=tuple(preset.columns if isinstance(preset, Preset) else preset),
                 sort=tuple(preset.sort) if isinstance(preset, Preset) else (),
                 filters=dict(preset.filters) if isinstance(preset, Preset) else {},
                 help=preset.help if isinstance(preset, Preset) else "",
                 drawn=dict(preset.drawn) if isinstance(preset, Preset) else {})
            for key, preset in presets.items()]


def to_record(view: View) -> dict[str, Any]:
    return {"id": view.id, "name": view.name, "builtin": view.builtin,
            "columns": list(view.columns), "sort": list(view.sort),
            "filters": view.filters, "help": view.help, "drawn": view.drawn}


def from_record(record: dict[str, Any]) -> View:
    """Tolerant of a record from an older build - refusing one would lose the user's
    work over a missing key."""
    return View(id=str(record.get("id") or ""),
                name=str(record.get("name") or t("manager.views.untitled")),
                builtin=bool(record.get("builtin")),
                columns=tuple(str(c) for c in (record.get("columns") or [])),
                sort=tuple(record.get("sort") or []),
                filters=dict(record.get("filters") or {}),
                help=str(record.get("help") or ""),
                drawn={str(k): str(v) for k, v in (record.get("drawn") or {}).items()})


def stored(library: Any, scope: str) -> tuple[list[View], str]:
    """The user's views for this grid, and which view was last showing."""
    try:
        value = library.preferences(scope + VIEWS_SUFFIX) or {}
    except Exception:
        logger.warning("manager: could not read views for %s", scope, exc_info=True)
        return [], ""
    return ([from_record(r) for r in (value.get("views") or [])],
            str(value.get("active") or ""))


def remember(library: Any, scope: str, views: list[View], active: str) -> None:
    """Write the user's views back. Built-ins are never among them."""
    try:
        library.put_preferences(scope + VIEWS_SUFFIX, {
            "views": [to_record(v) for v in views if not v.builtin],
            "active": active,
        })
    except Exception:
        logger.warning("manager: could not save views for %s", scope, exc_info=True)


def visible_columns(view: View, all_fields: list[str]) -> list[str] | None:
    """Which columns this view shows, or None when it names only fields that are gone.

    None is the stale case and is not the same as showing everything: a view saved
    against a library whose fields were renamed resolves nothing, and answering that
    with every column reads as the view misbehaving. A view that names no columns at
    all is a built-in carrying only a sort or a filter, and does mean all of them.
    """
    resolved = [field_name for field_name in view.columns if field_name in all_fields]
    if view.columns and not resolved:
        return None
    return resolved or list(all_fields)


def arrived(view: View, over: Mapping[str, Any] | None, all_fields: list[str]) -> View:
    """The view as a page arrived at it: `over` for its filters, each column `over`
    filters shown. The view itself where the address asked for nothing on this grid,
    or where the view is stale."""
    over = {name: model for name, model in (over or {}).items() if name in all_fields}
    shown = visible_columns(view, all_fields)
    if not over or shown is None:
        return view
    return replace(view, columns=tuple(shown + [name for name in over if name not in shown]),
                   filters=over)


# The id of the view a page was opened on, which the page makes and the user never saved.
OPENED = "opened"


def opened(start: View, name: str, over: Mapping[str, Any],
           all_fields: list[str], help: str = "",
           sort: tuple[dict[str, Any], ...] = ()) -> View:
    """A named starting point for a grid that was sent here: `start` with `over` as its
    filters and each column `over` filters shown, and `sort` in place of its own when given.
    Read-only like a built-in, so it is never among the user's views, and saving it is what
    keeps it."""
    over = {column: model for column, model in over.items() if column in all_fields}
    shown = list(visible_columns(start, all_fields) or all_fields)
    return replace(start, id=OPENED, name=name, builtin=True, help=help,
                   columns=tuple(shown + [column for column in over if column not in shown]),
                   filters=over, sort=sort or start.sort)


def without_filters(view: View, fields: Collection[str]) -> View:
    """`view` with no filter on `fields`; the view itself where it has none. A copy, so what
    is stored is never the stripped form."""
    if not any(name in fields for name in view.filters):
        return view
    return replace(view, filters={name: model for name, model in view.filters.items()
                                  if name not in fields})


def differs(view: View, columns: tuple[str, ...], sort: tuple[dict, ...],
            filters: dict[str, Any], drawn: dict[str, str] | None = None) -> bool:
    """Whether the screen has drifted from the selected view. Column *order* is not
    compared: dragging one is layout, which the grid keeps for itself."""
    return (set(view.columns) != set(columns)
            or _sort_key(view.sort) != _sort_key(sort)
            or (view.filters or {}) != (filters or {})
            or (view.drawn or {}) != (drawn or {}))


def _sort_key(sort: Any) -> list[tuple[str, str]]:
    """Sort reduced to what a user chose. AG Grid's state carries an entry per column,
    most of them null; comparing those raw reports a difference for every one."""
    ordered = sorted((entry for entry in (sort or []) if entry.get("sort")),
                     key=lambda entry: entry.get("sortIndex") or 0)
    return [(str(entry.get("colId")), str(entry.get("sort"))) for entry in ordered]

