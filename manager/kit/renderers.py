"""How a grid cell is drawn, by name.

A column names the drawing it uses and the others it allows; a view records which it is
using. The JavaScript behind every name is here and nowhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from nicegui import Client, ui

from common.devices import install_identity
from common.i18n import t
from manager.kit import media_ownership, tag_chips, verbs

# Ours, not AG Grid's: the drawings a column allows, its default first. Stripped by
# `grid.for_grid` before the definitions reach the grid.
CHOICES_KEY = "drawings"


@dataclass(frozen=True)
class Renderer:
    name: str
    # The catalog key of what a person calls this way of drawing.
    label: str
    # `params => html`.
    js: str
    # The row height the drawing needs, or 0 for whatever the grid already uses.
    row_px: int = 0


# What every column drawn by name renders through; `draw_as` chooses the drawing.
DISPATCH = (
    "params => {"
    " const scope = (params.context || {}).scope;"
    " const chosen = ((window.__vpinfeDrawAs || {})[scope] || {})[params.colDef.field];"
    " const draw = (window.__vpinfeDraw || {})[chosen || params.drawn];"
    " return draw ? draw(params) : (params.value == null ? '' : String(params.value)); }"
)

# Word -> how to draw it. The cell holds the word and the mark is drawn from it here;
# `manager/kit/data.py` has why.
_MARK_BY_WORD = {
    tier.noun: {"mark": tier.mark, "why": t(tier.why), "word": t(tier.noun)}
    for tier in (media_ownership.tier_for(key)
                 for key in (*media_ownership.STATES, media_ownership.UNUSED))
}

MARK = Renderer("mark", "manager.renderers.mark", (
    "params => {"
    " const m = " + json.dumps(_MARK_BY_WORD) + ";"
    " const t = m[params.value]; if (!t) return '';"
    " const tip = t.word + ' - ' + t.why;"
    " return '<span class=\"manager-mark ' + t.mark + '\" title=\"' + tip"
    " + '\"></span>'; }"
))

# A kind with no picture - audio, a rule sheet - keeps its mark: the file is there
# either way, and a column that empties when you ask to see the art reads as one that
# lost its files.
PICTURE = Renderer("picture", "manager.renderers.picture", (
    "params => {"
    " const kind = params.colDef.field.slice(6);"
    " const row = params.data || {}; const art = row['thumb_' + kind];"
    " if (!art) return window.__vpinfeDraw.mark(params);"
    " return '<span class=\"manager-cell-art\">' + art"
    " + '<i class=\"material-icons manager-cell-zoom\" title=\"' + "
    + json.dumps(t("word.enlarge")) + " + '\" data-game=\"'"
    " + row.id + '\" data-kind=\"' + kind + '\">open_in_full</i></span>'; }"
), row_px=74)

_ESCAPE = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
           ".replace(/\"/g, '&quot;');")

# A state as a word colored by what it costs, or as the character a state of this kind is
# drawn as: a tick for in place, a question mark for not known yet. `params.states` maps each
# token to its `label`, `tone`, `glyph` and `why`, the tooltip. A token it does not hold draws
# nothing, or the value itself, quiet, where the column says `plain`.
STATE = Renderer("state", "manager.renderers.state", (
    "params => {" + _ESCAPE +
    " const one = (params.states || {})[params.value];"
    " if (!one) return params.plain && params.value ? '<span class=\"manager-cell-quiet\">'"
    " + esc(params.value) + '</span>' : '';"
    " const tip = one.why ? ' title=\"' + esc(one.why) + '\"' : '';"
    " const tone = one.tone ? ' manager-state--' + esc(one.tone) : '';"
    " const body = one.icon ? '<i class=\"material-icons manager-cell-mark\">' + esc(one.icon)"
    " + '</i>' : esc(one.glyph || one.label);"
    " return '<span class=\"manager-state' + tone + '\"' + tip + '>' + body + '</span>'; }"
))

# The order chips are drawn in, which is also the order a list column sorts by.
ORDER = ("(a, b) => String(a).localeCompare(String(b), undefined,"
         " {numeric: true, sensitivity: 'base'})"
         " || (String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0)")

# Where a chip takes its looks from, by name: `value => {chip, dot, mark, tip, label}`.
# A column naming none draws the value alone, and so does a look without a `label`.
TAG_LOOKS = "tags"
COLLECTION_LOOKS = "collections"
SETTING_LOOKS = "settings"
_COLLECTION_LOOKS_DATA = "__vpinfeCollectionLooks"
_COLLECTION_LOOK = (
    f"name => ((window.{_COLLECTION_LOOKS_DATA} || {{}})[name] || {{}}).smart"
    f" ? {{mark: {json.dumps(verbs.SMART)}}} : {{}}"
)
_SETTING_NAMES_DATA = "__vpinfeSettingNames"
_SETTING_LOOK = (
    f"key => {{ const name = (window.{_SETTING_NAMES_DATA} || {{}})[key];"
    " return name ? {label: name} : {}; }"
)
FEATURE_LOOKS = "features"
_FEATURE_LOOK = (
    "name => ({chip: 'manager-member-chip manager-chip-quiet',"
    f" label: ({json.dumps({name: t(key) for name, key in install_identity.LABELS.items()})}"
    ")[name] ?? name})"
)
LOOKS = {TAG_LOOKS: tag_chips.LOOK, COLLECTION_LOOKS: _COLLECTION_LOOK,
         SETTING_LOOKS: _SETTING_LOOK, FEATURE_LOOKS: _FEATURE_LOOK}


def named(looks: str) -> str:
    """`value => what its chip says`, for the column whose chips take their looks from
    `looks`."""
    return (f"(value => (((window.__vpinfeChipLooks || {{}})[{json.dumps(looks)}]"
            " || (() => ({})))(value) || {}).label || value)")

# The mark a chip carries, drawn in the chip and in the filter's leading slot alike.
MARK_CLASS = "material-icons manager-tag-mark"

# A list as chips. The list is the cell's value, or the row field `params.list` names.
CHIPS = Renderer("chips", "manager.renderers.chips", (
    "params => {" + _ESCAPE +
    f" const order = {ORDER};"
    " const held = params.list ? (params.data || {})[params.list] : params.value;"
    " const looks = (window.__vpinfeChipLooks || {})[params.looks] || (() => ({}));"
    " const said = value => (looks(value) || {}).label || value;"
    f" return '<span class=\"{tag_chips.BOX}\">' + (Array.isArray(held) ? [...held] : [])"
    ".sort((a, b) => order(said(a), said(b))).map(value => {"
    "  const look = looks(value) || {};"
    "  const tip = look.tip ? ' title=\"' + esc(look.tip) + '\"' : '';"
    "  const dot = look.dot ? '<span class=\"' + look.dot + '\"></span>' : '';"
    f"  const mark = look.mark ? '<i class=\"{MARK_CLASS}\">' + look.mark + '</i>' : '';"
    f"  return '<span class=\"' + (look.chip || '{tag_chips.CHIP}') + '\"' + tip + '>'"
    " + dot + mark + esc(look.label || value) + '</span>'; }).join('') + '</span>'; }"
))

# A web address as a link that leaves the Manager, the look of `panel.link_out`. It reads
# as the row field `params.says` names, else as `params.header`. Anything that is not an
# http or https address draws nothing, so a row cannot make the cell run a script.
LINK = Renderer("link", "manager.renderers.link", (
    "params => {" + _ESCAPE +
    " const href = String(params.value == null ? '' : params.value).trim();"
    " if (!/^https?:\\/\\//i.test(href)) return '';"
    " const said = (params.says ? (params.data || {})[params.says] : '') || params.header;"
    " return '<a class=\"manager-link manager-link-out\" href=\"' + esc(href)"
    " + '\" target=\"_blank\" rel=\"noopener\"><span>' + esc(said)"
    " + '</span><i class=\"q-icon notranslate material-icons\" aria-hidden=\"true\">'"
    " + 'open_in_new</i></a>'; }"
))

# A picture of the row's own, at the address core serves it from. One that will not load
# draws nothing rather than a broken image.
IMAGE = Renderer("image", "manager.renderers.image", (
    "params => {" + _ESCAPE +
    " const src = String(params.value == null ? '' : params.value);"
    " if (!src) return '';"
    " return '<img class=\"manager-cell-image\" loading=\"lazy\" alt=\"\" src=\"' + esc(src)"
    " + '\" onerror=\"this.remove()\">'; }"
))

REGISTRY: dict[str, Renderer] = {one.name: one
                                 for one in (MARK, PICTURE, STATE, CHIPS, LINK, IMAGE)}


def install() -> None:
    """Every drawing, and every source of a chip's looks, where the dispatcher can find
    them. Harmless to repeat."""
    body = ", ".join(f"{json.dumps(name)}: {one.js}" for name, one in REGISTRY.items())
    looks = ", ".join(f"{json.dumps(name)}: {js}" for name, js in LOOKS.items())
    ui.run_javascript(
        f"window.__vpinfeDraw = Object.assign(window.__vpinfeDraw || {{}}, {{{body}}});"
        f" window.__vpinfeChipLooks = Object.assign(window.__vpinfeChipLooks || {{}},"
        f" {{{looks}}});")


def install_collection_looks(smart: set[str], page: Client) -> None:
    """Which collections' chips carry the Smart mark, on `page`. Given rather than read
    from the current slot, for a caller whose own slot a rebuild may have deleted."""
    held = {name: {"smart": True} for name in sorted(smart)}
    code = f"window.{_COLLECTION_LOOKS_DATA} = {json.dumps(held)};"
    page.run_javascript(code)


def install_setting_names(names: dict[str, str]) -> None:
    """What a setting's chip says, by its key."""
    code = f"window.{_SETTING_NAMES_DATA} = {json.dumps(dict(sorted(names.items())))};"
    ui.context.client.run_javascript(code)


def drawable(default: str, *others: str, **params: Any) -> dict[str, Any]:
    """What makes a column drawn by name: `default` until a view says otherwise, and
    `others` as the choices it offers. `params` reach the drawing as it runs."""
    return {":cellRenderer": DISPATCH, ":equals": "() => false",
            "cellRendererParams": {**params, "drawn": default},
            CHOICES_KEY: (default, *others)}


def choices(definition: dict[str, Any]) -> tuple[str, ...]:
    return tuple(definition.get(CHOICES_KEY) or ())


def draw_as(scope: str, drawing: dict[str, str]) -> None:
    """Say how this grid's columns are drawn now. The grid redraws itself separately."""
    ui.run_javascript(
        "(window.__vpinfeDrawAs = window.__vpinfeDrawAs || {})"
        f"[{json.dumps(scope)}] = {json.dumps(drawing)};")


def row_px(drawing: dict[str, str], shown: list[str], base: int) -> int:
    """The row height the drawings on screen need, never less than the grid's own."""
    return max([base, *(REGISTRY[name].row_px for field, name in drawing.items()
                        if field in shown and name in REGISTRY)])
