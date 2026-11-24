"""What the import sheet holds when a drop has more than one answer.

The server keeps nothing between plans, so the answers a person gave - the variant picked in
each group and the place chosen for each file nobody claimed - live here, and every plan and
import call carries all of them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nicegui import ui

from common import i18n
from common.i18n import t
from common.media.media_specs import CONVERTIBLE_IMAGE, IMAGE_FAMILY, media_label_map
from manager.kit import art, candidates, panel

LEAVE = "leave"
CHOOSE = "choose"
ORDER = ("table", "media", "with")


def choice_key(choice: dict[str, Any]) -> str:
    return "|".join(str(choice.get(name) or "") for name in ("kind", "media_kind", "table_id"))


@dataclass
class Section:
    key: str
    items: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    unclaimed: list[dict[str, Any]] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)


def _family(kind: str) -> str:
    return {"table": "table", "media": "media"}.get(kind, "with")


def _kind_label(kind: str) -> str:
    return t(f"asset.kind.{kind}.label")


def _blocked_label(one: dict[str, Any]) -> str:
    """A refused file named by what it is: a media kind by its own word, not "Media"."""
    media = str(one.get("media_kind") or "")
    return t(f"media.kind.{media}.label") if media else _kind_label(str(one.get("kind") or ""))


def _refused(blocked: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """`(family, names, reason)` with one line for each family and reason, so a cause that
    stops three files in a section is said once and not three times."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for one in blocked:
        family = _family(str(one.get("kind") or ""))
        names = grouped.setdefault((family, str(one.get("reason") or "")), [])
        label = _blocked_label(one)
        if label not in names:
            names.append(label)
    return [(family, ", ".join(names), reason) for (family, reason), names in grouped.items()]


class Sheet:
    """The plan on screen and the answers given to it."""

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan: dict[str, Any] = plan
        self.picks: dict[str, str] = {}
        self.places: dict[str, dict[str, Any]] = {}
        self.left: set[str] = set()
        self.convert: dict[str, dict[str, str]] = {}
        self.info_modes: dict[str, str] = {}
        self.open: set[str] = set()
        self.wait = True
        self.offered: dict[str, list[dict[str, Any]]] = {}
        self.tables: list[dict[str, Any]] = []
        self.seeded = False
        self.suggestions: set[str] = set()
        self.tried: set[str] = set()
        self.take(plan)

    def take(self, plan: dict[str, Any]) -> None:
        """Show a fresh plan. An answer the plan no longer asks about is dropped, because a
        pick naming a file outside its group is refused, and one the plan offers a default
        for is seeded so the plan that follows carries it. What was offered for a file stays
        offered once it is claimed, so its place can still be changed."""
        self.plan = plan
        members = {str(one["key"]): {str(c["member"]) for c in one.get("candidates") or ()}
                   for one in plan.get("groups") or ()}
        self.picks = {key: member for key, member in self.picks.items()
                      if key in members and (member == "" or member in members[key])}
        self.seeded = False
        for one in plan.get("groups") or ():
            key = str(one["key"])
            if key not in self.picks and one.get("preselected"):
                self.picks[key] = str(one["preselected"])
                self.seeded = True
        for one in plan.get("unclaimed") or ():
            member = str(one["member"])
            self.offered[member] = list(one.get("choices") or ())
            self._take_suggestion(member)
        if plan.get("tables"):
            self.tables = list(plan["tables"])

    def _take_suggestion(self, member: str) -> None:
        """Place a file where its shape says it goes, once: a person who changes it back to no
        place, or leaves it out, is not given the suggestion again."""
        if member in self.tried or member in self.places or member in self.left:
            return
        found = next((one for one in self.offered[member] if one.get("suggested")), None)
        if found is None:
            return
        self.tried.add(member)
        self.place(member, choice_key(found))
        self.suggestions.add(member)
        self.seeded = True

    def suggests(self, member: str) -> bool:
        """Whether the place the file has is one its shape suggested and nobody has changed."""
        return member in self.suggestions

    def asked(self) -> dict[str, Any]:
        """What a plan or an import is told, for the parts that have an answer."""
        found: dict[str, Any] = {}
        if self.places:
            found["assignments"] = list(self.places.values())
        if self.picks:
            found["picks"] = dict(self.picks)
        if self.convert or not self.wait:
            found["convert"] = {"rows": {member: dict(row) for member, row in
                                         self.convert.items()}, "wait": self.wait}
        if self.info_modes:
            found["info_modes"] = dict(self.info_modes)
        return found

    def view(self, member: str) -> dict[str, Any] | None:
        """What the row of a file that is converted on the way in shows and offers."""
        return next((one["convert"] for one in self.items()
                     if one.get("member") == member and one.get("convert")), None)

    def decide(self, member: str, key: str, value: str) -> None:
        self.convert.setdefault(member, {})[key] = value

    def ffmpeg_missing(self) -> bool:
        state = self.plan.get("convert") or {}
        return bool(state) and not state.get("usable", True)

    def reencodes(self) -> bool:
        return bool((self.plan.get("convert") or {}).get("reencodes"))

    def pick(self, key: str, member: str) -> None:
        self.picks[key] = member

    def groups(self) -> list[dict[str, Any]]:
        return list(self.plan.get("groups") or ())

    def items(self) -> list[dict[str, Any]]:
        return list(self.plan.get("items") or ())

    def open_groups(self) -> list[dict[str, Any]]:
        return [one for one in self.groups()
                if not one.get("extra") and str(one["key"]) not in self.picks]

    def reason(self) -> str:
        """Why the import cannot go yet, or empty."""
        found = self.open_groups()
        if not found:
            return ""
        label = str(found[0].get("label") or "")
        return t("manager.drop_sheet.choose_one", kind=label)

    def single(self) -> bool:
        plan = self.plan
        return (len(self.items()) == 1 and not self.places and not any(
            plan.get(name) for name in ("blocked", "groups", "unclaimed", "left_out")))

    def replaces(self, group: dict[str, Any]) -> str:
        member = self.picks.get(str(group["key"]), "")
        found = next((one for one in self.items() if member and one.get("member") == member), {})
        return str(found.get("replaces") or "")

    def sections(self) -> list[Section]:
        if self.single():
            return []
        picked = {member for member in self.picks.values() if member}
        held: dict[str, Section] = {}

        def into(key: str) -> Section:
            return held.setdefault(key, Section(key))

        for one in self.items():
            if one.get("member") in picked:
                continue
            into(_family(str(one.get("kind") or ""))).items.append(one)
        for one in self.groups():
            into(_family(str(one.get("kind") or ""))).groups.append(one)
        for one in self.plan.get("unclaimed") or ():
            first = (one.get("choices") or [{}])[0]
            into(_family(str(first.get("kind") or "media"))).unclaimed.append(one)
        for family, name, reason in _refused(self.plan.get("blocked") or []):
            into(family).refused.append({"name": name, "reason": reason})
        for one in self.plan.get("left_out") or ():
            into("media" if one.get("reason_key") == "arrival.left_out.doesnt_fit" else "with") \
                .refused.append({"name": str(one.get("name") or ""),
                                 "reason": str(one.get("reason") or "")})
        return [held[key] for key in ORDER if key in held]

    def refusals(self) -> list[str]:
        """What the plan refused, a line each, for a drop that has nothing else to show."""
        found = [(name, reason) for _, name, reason in _refused(self.plan.get("blocked") or [])]
        found += [(str(one.get("name") or ""), str(one.get("reason") or ""))
                  for one in self.plan.get("left_out") or ()]
        return [t("manager.drop_sheet.refusal", name=name, reason=reason)
                for name, reason in found]

    def alone(self, group: dict[str, Any]) -> bool:
        """Whether the group is all the sheet has, so its heading would only repeat the title."""
        return (len(self.groups()) == 1 and not self.items_shown()
                and not self.plan.get("unclaimed"))

    def items_shown(self) -> list[dict[str, Any]]:
        picked = {member for member in self.picks.values() if member}
        return [one for one in self.items() if one.get("member") not in picked]

    def options(self, member: str) -> dict[str, str]:
        """Where a file could go, as the select lists it: the places offered for it, and leaving
        it out. The first says no place has been chosen."""
        found = {CHOOSE: t("manager.drop_sheet.unplaced")}
        for one in self.offered.get(member, ()):
            found[choice_key(one)] = str(one.get("label") or "")
        found[LEAVE] = t("manager.drop_sheet.skip")
        return found

    def placed(self, member: str) -> str:
        return choice_key(self.places[member]) if member in self.places else ""

    def shown(self, member: str) -> str:
        if member in self.left:
            return LEAVE
        return self.placed(member) or CHOOSE

    def has_place(self, item: dict[str, Any]) -> bool:
        return str(item.get("member") or "") in self.places

    def place(self, member: str, key: str) -> None:
        if key == LEAVE:
            self.leave(member)
            return
        self.places.pop(member, None)
        self.left.discard(member)
        self.suggestions.discard(member)
        found = next((one for one in self.offered.get(member, ()) if choice_key(one) == key), None)
        if found is None:
            return
        where: dict[str, Any] = {"member": member, "kind": found.get("kind"),
                                 "media_kind": found.get("media_kind")}
        if found.get("table_id"):
            where["table_id"] = found["table_id"]
        self.places[member] = where

    def leave(self, member: str) -> None:
        self.places.pop(member, None)
        self.suggestions.discard(member)
        self.left.add(member)


def title(*, game: str, asked: dict[str, Any], new_game: bool = False, source: str = "") -> str:
    """The target in the person's words."""
    if new_game:
        if source:
            return t("manager.drop_sheet.title_new_named", name=source)
        return t("manager.drop_sheet.title_new_drop")
    if asked.get("media_kind"):
        kind = str(asked["media_kind"])
        return t("manager.drop_sheet.slot_for", slot=media_label_map().get(kind) or kind,
                 game=game)
    if asked.get("asset_kind"):
        return t("manager.drop_sheet.asset_for", kind=_kind_label(str(asked["asset_kind"])),
                 game=game)
    return t("manager.drop_sheet.add_to", game=game)


def go_label(sheet: Sheet, base: str, wanted: int) -> str:
    """The button: what is still to choose, else what it does."""
    reason = sheet.reason()
    if reason:
        return reason
    if not sheet.items() and sheet.plan.get("unclaimed"):
        return t("manager.drop_sheet.unplaced")
    if wanted > 1 and base == t("manager.import_dialog.import"):
        return t("manager.drop_sheet.import_n", count=wanted)
    return base


def _picture(upload_id: str, member: str) -> str:
    return (art.upload_picture(upload_id, member, size=art.CELL)
            if upload_id and Path(member).suffix.lower() in IMAGE_FAMILY + CONVERTIBLE_IMAGE
            else "")


def _candidate(sheet: Sheet, group: dict[str, Any], one: dict[str, Any], upload_id: str,
               changed: Callable[[], Any]) -> None:
    key, member = str(group["key"]), str(one["member"])

    def choose() -> Any:
        if sheet.picks.get(key) == member:
            return None
        sheet.pick(key, member)
        return changed()

    with ui.row().classes("items-center gap-3 w-full no-wrap manager-import-row") as row:
        radio = ui.radio({"on": ""}, value="on" if sheet.picks.get(key) == member else None) \
            .props("dense")
        radio.on_value_change(lambda e: choose() if e.value == "on" else None)
        candidates.thumb(_picture(upload_id, member), "")
        with ui.column().classes("gap-0 grow min-w-0"):
            name = ui.label(str(one.get("name") or member)).classes("manager-member-name truncate")
            if member != one.get("name"):
                name.tooltip(member)
            if one.get("width") and one.get("height"):
                ui.label(t("manager.drop_sheet.dimensions", width=one["width"],
                           height=one["height"])).classes("manager-member-table")
        ui.label(i18n.size(int(one.get("size") or 0))).classes("manager-member-qualifier")
    row.on("click", choose)


def draw_group(sheet: Sheet, group: dict[str, Any], *, upload_id: str,
               changed: Callable[[], Any]) -> None:
    """The variants of one kind of file, one of which is imported: a radio and a picture for
    each, and an answer that imports none of them."""
    key = str(group["key"])
    with ui.column().classes("gap-0 w-full"):
        if not sheet.alone(group):
            ui.label(str(group.get("label") or "")).classes("manager-member-name")
        for one in group.get("candidates") or ():
            _candidate(sheet, group, one, upload_id, changed)

        def none() -> Any:
            if sheet.picks.get(key) == "":
                return None
            sheet.pick(key, "")
            return changed()

        with ui.row().classes("items-center gap-3 w-full no-wrap manager-import-row") as row:
            radio = ui.radio({"on": ""}, value="on" if sheet.picks.get(key) == "" else None) \
                .props("dense")
            radio.on_value_change(lambda e: none() if e.value == "on" else None)
            ui.label(t("manager.drop_sheet.pick_none")).classes("grow")
        row.on("click", none)
        replaces = sheet.replaces(group)
        if replaces:
            ui.label(replaces).classes("manager-help")


def draw_suggested(sheet: Sheet, member: str) -> None:
    """The chip that says a place was guessed from the picture's shape. A place the person
    chose, or one the file was named for, carries none."""
    if sheet.suggests(member):
        ui.label(t("manager.drop_sheet.shape_guess")) \
            .classes("manager-member-chip manager-chip-quiet") \
            .tooltip(t("manager.drop_sheet.shape_guess_why"))


def place_select(sheet: Sheet, member: str, changed: Callable[[], Any]) -> Callable[[], None]:
    def chose(e: Any) -> Any:
        sheet.place(member, str(e.value or CHOOSE))
        return changed()

    select = panel.select(sheet.options(member), sheet.shown(member), chose)

    def draw() -> None:
        select()
        draw_suggested(sheet, member)

    return draw


def draw_unclaimed(sheet: Sheet, one: dict[str, Any], *, upload_id: str,
                   changed: Callable[[], Any]) -> None:
    """A file no rule claimed. It cannot be ticked until it has a place, and choosing a place
    plans it in."""
    member = str(one["member"])
    with ui.row().classes("items-center gap-3 w-full no-wrap manager-import-row"):
        with ui.element("div").tooltip(t("manager.drop_sheet.needs_a_place")):
            ui.checkbox(value=False).props("dense disable")
        if _picture(upload_id, member):
            candidates.thumb(_picture(upload_id, member), "")
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(str(one.get("name") or member)).classes("manager-member-name truncate")
        ui.label(i18n.size(int(one.get("size") or 0))).classes("manager-member-qualifier")
        place_select(sheet, member, changed)()


def draw_table_line(sheet: Sheet, asked: dict[str, Any], changed: Callable[[], Any]) -> None:
    """Which table the files go with, for the drops that do not say: media dropped on one table
    of several is named for that table or for the whole game, and a file that belongs to a
    table, dropped on a game, is given the table it is for."""
    if not sheet.tables:
        return
    labels = {str(one["id"]): str(one.get("label") or "") for one in sheet.tables}
    default = next((str(one["id"]) for one in sheet.tables if one.get("default")),
                   next(iter(labels)))
    scope = str(sheet.plan.get("scope") or "")
    if scope:
        named = str(asked.get("table_id") or default)
        options = {"table": labels.get(named, ""), "game": t("manager.drop_sheet.whole_game")}
        value = str(asked.get("scope") or scope)

        def named_for(e: Any) -> Any:
            asked["scope"] = str(e.value or "")
            return changed()

        name, chooser = t("manager.drop_sheet.named_for"), panel.select(options, value, named_for)
    else:
        def for_table(e: Any) -> Any:
            asked["table_id"] = str(e.value or "")
            return changed()

        name = t("manager.drop_sheet.for_table")
        chooser = panel.select(labels, str(asked.get("table_id") or default), for_table)
    with ui.row().classes("items-center gap-3 w-full no-wrap"):
        ui.label(name).classes("manager-member-name")
        chooser()


def draw_refused(one: dict[str, str]) -> None:
    """A file with no place in this drop, shown so it is not a mystery: a tick that cannot
    be ticked and the reason beside it."""
    with ui.row().classes("items-center gap-3 w-full no-wrap manager-import-row "
                          "manager-import-row--refused"):
        with ui.element("div").tooltip(one["reason"]):
            ui.checkbox(value=False).props("dense disable")
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(one["name"]).classes("manager-member-name truncate")
            ui.label(one["reason"]).classes("manager-member-table")


def heading(key: str) -> str:
    return t(f"manager.drop_sheet.section_{key}")


def hold_scroll(rows: Any, run: Callable[[str], Any]) -> tuple[Callable[[], Any],
                                                                Callable[[], Any]]:
    """Keep a list where it was scrolled to across a redraw, which empties it and would
    send it back to the top. `freeze` goes before the rows are cleared and `thaw` after they
    are drawn."""
    target = f"document.getElementById('c{rows.id}')"
    run(f"(e => {{ if (!e) return; const s = {{top: 0, frozen: false}}; e.__held = s;"
        " e.addEventListener('scroll', () => { if (!s.frozen) s.top = e.scrollTop; },"
        f" {{passive: true}}); }})({target})")

    def freeze() -> Any:
        return run(f"(e => {{ if (e && e.__held) e.__held.frozen = true; }})({target})")

    def thaw() -> Any:
        return run(f"(e => {{ if (e && e.__held) setTimeout(() => {{ e.scrollTop = e.__held.top;"
                   f" e.__held.frozen = false; }}, 80); }})({target})")

    return freeze, thaw
