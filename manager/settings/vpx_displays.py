"""Launchers > Visual Pinball X > Displays: the screens picture over that launcher's ini.

The picture is the one VPinFE's own Displays page draws, with a part's menu offering to draw
it inside the playfield. It writes this launcher's ini only. Where it disagrees with
VPinFE's screens the page says so and offers to match them; a change to VPinFE's screens
moves this one only where it still matched (`follow`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from types import SimpleNamespace
from typing import Any

from nicegui import run, ui

from apps.vpx import screens as vpx
from common.devices import device_client
from common.failures import why
from common.host import display_service
from common.i18n import t
from common.values import quarter_turn
from manager.kit import offload, panel, units, verbs
from manager.kit.data import config_groups
from manager.kit.failed_together import Failures
from manager.kit.on_page import on_page
from manager.settings import displays, screens
from manager.settings import screens_picture as picture
from manager.settings.screens import Screen

logger = logging.getLogger(__name__)

Placement = picture.Placement
Entries = list[tuple[Any, Any]]

# A launcher whose app is this one has the keys this page draws.
VPX_APP = "vpx"
# What a placed number is rounded to when it is written back as centimeters.
_CM_PLACES = 2


def applies(group: Any) -> bool:
    """Whether a launcher's group is the Displays of a program that keeps its windows in
    the keys this page draws."""
    return group.key == "displays" and any(
        one.key == vpx.DISPLAY[vpx.PLAYFIELD] for one in group.settings)


def trimmed(shown: Sequence[tuple[Any, list[Any]]],
            on: Mapping[str, Placement]) -> list[tuple[Any, list[Any]]]:
    """The curated headings without the keys the picture or the Screen Size row draws, and
    without a part the picture holds nowhere. The playfield always shows."""
    out = []
    for heading, fields in shown:
        part = vpx.WINDOW_HEADINGS.get(heading.key)
        if part is not None:
            if part != vpx.PLAYFIELD and on.get(part) is None:
                continue
            drawn = {vpx.DISPLAY[part], vpx.OUTPUT.get(part)}
            fields = [one for one in fields if one.key not in drawn]
        elif heading.key == vpx.CABINET_HEADING:
            fields = [one for one in fields if one.key not in vpx.SCREEN_SIZE]
        if fields:
            out.append((heading, fields))
    return out


def pairs_for(shown: Sequence[tuple[Any, list[Any]]],
              on: Mapping[str, Placement]) -> list[Any]:
    """The headings' pairs, a part drawn inside the playfield saying its position is in
    the playfield's window."""
    found = []
    for heading, _ in shown:
        inside = on.get(vpx.WINDOW_HEADINGS.get(heading.key, "")) == vpx.INSIDE
        for pair in heading.pairs:
            found.append(SimpleNamespace(**{
                **vars(pair), "note": t("manager.vpx_displays.in_the_playfield_window")})
                if inside and pair.key == "position" else pair)
    return found


def size_row_goes_before(shown: Sequence[tuple[Any, list[Any]]]) -> str:
    """The key of the cabinet row that follows the screen's size, or "" where none does."""
    for heading, fields in shown:
        if heading.key == vpx.CABINET_HEADING:
            keys = [one.key for one in fields]
            last = max((keys.index(key) for key in vpx.SCREEN_SIZE if key in keys), default=-1)
            return keys[last + 1] if 0 <= last < len(keys) - 1 else ""
    return ""


def named(parts: Sequence[str]) -> str:
    """The parts as a person calls them: "A", "A and B", "A, B and C"."""
    labels = {part.key: part.label for part in picture.vpx_parts()}
    names = [labels.get(part, part) for part in parts]
    if len(names) < 2:
        return "".join(names)
    return t("manager.vpx_displays.and_last", most=", ".join(names[:-1]), last=names[-1])


def say_unnamed(written: vpx.Written) -> None:
    """Say which parts were left where they were because Visual Pinball has no name for them."""
    if written.unnamed:
        ui.notify(t("manager.vpx_displays.unnamed", parts=named(written.unnamed)),
                  type="warning")


def differing(vpinfe: displays.Standing, here: Sequence[Screen], on: Mapping[str, Placement],
              rotation: str | None) -> list[str]:
    """The parts `on` holds otherwise than VPinFE does. The playfield counts where it is on
    the same screen and turned another way, unless `rotation` is None: a turn the ini does
    not hold."""
    parts = vpx.differs(vpinfe.on, on)
    wanted = vpx.view_cab_rotation(
        next((one for one in here if one.number == vpinfe.on.get(vpx.PLAYFIELD)), None),
        vpinfe.turn[1])
    if (vpx.PLAYFIELD not in parts and rotation is not None
            and vpx.rotation_differs(wanted, rotation)):
        parts.append(vpx.PLAYFIELD)
    return parts


def conflicts(vpinfe: displays.Standing, here: Sequence[Screen],
              set_here: Mapping[str, str]) -> list[str]:
    """The parts the ini has a value of its own for that put them elsewhere than VPinFE does,
    given the keys the file sets. A part it holds nothing for is not one: `filling` writes it."""
    nothing = vpx.holds_nothing(set_here)
    turn = str(set_here.get(vpx.VIEW_CAB_ROTATION) or "").strip()
    return [part for part in differing(vpinfe, here, vpx.placed(set_here, here), turn or None)
            if part not in nothing]


def matching(vpinfe: displays.Standing, here: Sequence[Screen], logged: Sequence[str],
             flat: Mapping[str, str], on: Mapping[str, Placement],
             program: Mapping[str, Any] | None) -> tuple[dict[str, Placement], vpx.Written]:
    """The parts that move to put the ini where VPinFE has its screens and turn, given `on`
    as where it holds them, and the keys that say so. With no topper screen in VPinFE, the
    topper is left where it is unless a part now needs its screen."""
    wanted = {part: vpinfe.on.get(part)
              for part in (vpx.BACKGLASS, vpx.SCOREVIEW, vpx.PLAYFIELD)}
    has_topper = vpinfe.on.get(vpx.TOPPER) is not None
    if has_topper:
        wanted[vpx.TOPPER] = vpinfe.on[vpx.TOPPER]
    moved = {part: where for part, where in wanted.items() if on.get(part) != where}
    topper = on.get(vpx.TOPPER)
    if not has_topper and isinstance(topper, int) and topper in wanted.values():
        moved[vpx.TOPPER] = None
    return moved, vpx.answer(moved, {**on, **moved}, here, logged, flat, turn=vpinfe.turn,
                             program=program)


class Page:
    """One launcher's Displays, with VPinFE's screens beside it.

    `flat` is the launcher's ini as keys to text. `rebuild` draws the rows from a fresh
    read, which is what a move needs: the rows under the picture follow the parts placed.
    """

    def __init__(self, library: Any, launcher_id: str, rebuild: Callable[[], Any], *,
                 flat: dict[str, str], vpinfe: displays.Standing, here: Sequence[Screen],
                 logged: Sequence[str], cabinet: bool, units_now: str,
                 defaults: Mapping[str, str],
                 program: Mapping[str, Any] | None = None) -> None:
        self.library = library
        self.launcher_id = launcher_id
        self.rebuild = rebuild
        self.flat = flat
        self.vpinfe = vpinfe
        self.here = list(here)
        self.logged = list(logged)
        self.cabinet = cabinet
        self.units = units_now
        self.defaults = dict(defaults)
        self.program = program
        self.on = vpx.placed(flat, self.here)
        self.size = {key: self._cm(key) for key in vpx.SCREEN_SIZE}
        self.refill: Callable[[], None] = lambda: None

    def screen(self, number: Placement) -> Screen | None:
        return next((one for one in self.here if one.number == number), None)

    def rotation(self) -> str:
        return self.flat.get(vpx.VIEW_CAB_ROTATION, "")

    def turned(self) -> str | None:
        """The VPinFE rotation that VPX's turn on the playfield's screen amounts to, or None
        where it faces none of the screen's four edges."""
        return vpx.vpinfe_rotation(self.screen(self.on.get(vpx.PLAYFIELD)), self.rotation())

    def differences(self) -> list[str]:
        """The parts held otherwise than VPinFE holds them."""
        return differing(self.vpinfe, self.here, self.on, self.rotation())

    def _cm(self, key: str) -> float:
        for said in (self.flat.get(key), self.defaults.get(key)):
            try:
                return float(str(said or "").strip())
            except ValueError:
                continue
        return 0.0

    @on_page
    async def write(self, keys: dict[str, str]) -> bool:
        try:
            await run.io_bound(self.library.write_launcher_config, self.launcher_id, keys)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            return False
        self.flat.update(keys)
        return True

    def _refused(self, written: vpx.Written) -> bool:
        """Say which parts were left where they were, and whether any were."""
        say_unnamed(written)
        if written.unsized:
            ui.notify(t("manager.vpx_displays.unsized", parts=named(written.unsized)),
                      type="warning")
        return bool(written.unnamed or written.unsized)

    def _turn_to(self, number: Placement) -> tuple[str, str] | None:
        """The playfield's (orientation, rotation) on the screen it is moving to, with the
        turn it has now, else VPinFE's."""
        screen = self.screen(number)
        if screen is None:
            return None
        found = picture.turn(quarter_turn(self.turned() or self.vpinfe.turn[1]), screen)
        return found["orientation"], found["rotation"]

    async def move(self, moved: dict[str, Placement]) -> bool:
        after = {**self.on, **moved}
        turn = self._turn_to(moved[vpx.PLAYFIELD]) if vpx.PLAYFIELD in moved else None
        written = vpx.answer(moved, after, self.here, self.logged, self.flat, turn=turn,
                             program=self.program)
        if self._refused(written):
            return False
        if written.keys and not await self.write(written.keys):
            return False
        self.on = after
        self.rebuild()
        return True

    async def match(self) -> None:
        moved, written = matching(self.vpinfe, self.here, self.logged, self.flat, self.on,
                                  self.program)
        if self._refused(written):
            return
        if written.keys and not await self.write(written.keys):
            return
        self.on = {**self.on, **moved}
        self.rebuild()

    @on_page
    async def show(self, _on: Mapping[str, Placement]) -> None:
        try:
            await run.io_bound(device_client.local().show_numbers, self.here)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.screens.show_failed"), caption=why(exc), type="negative")

    def picture_rows(self) -> Entries:
        def draw() -> None:
            picture.Picture(self.here, self.on, move=self.move, parts=picture.vpx_parts(),
                            cabinet=self.cabinet, rotation=self.turned(), show=self.show,
                            differs=self.differences(), match=self.match)

        return [(panel.HEADING, t("manager.settings.section_screens")), (panel.FULL, draw)]

    def size_row(self, joiner: str, labels: Sequence[str]) -> Entries:
        """The playfield screen's width and height in the units this install writes a
        length in, with the choice of those units beside them."""
        def draw() -> None:
            box = ui.element("div").classes("manager-fact-edit")

            def fill() -> None:
                box.clear()
                with box:
                    with ui.element("div").classes("manager-field-pair"):
                        for at, key in enumerate(vpx.SCREEN_SIZE):
                            self._size_field(key, at, joiner, labels[at])
                    panel.segmented({units.CM: t("manager.units.cm"),
                                     units.INCH: t("manager.units.in")},
                                    self.units, self._chose_units).classes("manager-units")

            self.refill = fill
            fill()

        return [(t("manager.screens.screen_size"), draw)]

    def _size_field(self, key: str, at: int, joiner: str, label: str) -> None:
        with ui.element("div").classes("manager-field-part"):
            control = ui.number(value=units.shown(self.size[key], self.units), format="%.1f",
                                step=0.1, suffix=t(f"manager.units.{self.units}")) \
                .props("dense borderless").classes("manager-edit-field manager-edit-narrow")
            control.props["aria-label"] = label
            control.tooltip(t("manager.screens.screen_short_side" if at
                              else "manager.screens.screen_long_side"))
            control.on("blur", partial(self._sized, key, control))
            control.on("keydown.enter", partial(self._sized, key, control))
            if joiner and not at:
                ui.label(joiner).classes("manager-cell-join").props("aria-hidden=true")

    @on_page
    async def _sized(self, key: str, control: Any, _event: Any = None) -> None:
        typed = control.value
        if typed is None or typed == units.shown(self.size[key], self.units):
            return
        cm = round(units.to_cm(float(typed), self.units), _CM_PLACES)
        if await self.write({key: f"{cm:g}"}):
            self.size[key] = cm
        self.refill()

    @on_page
    async def _chose_units(self, event: Any) -> None:
        chosen = str(event.value)
        try:
            await run.io_bound(self.library.put_config, {"manager": {"units": chosen}})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
        else:
            self.units = chosen
        self.refill()


async def entries(context: dict[str, Any], group: Any, shown: Sequence[tuple[Any, list[Any]]],
                  values: Mapping[str, Any],
                  rows: Callable[..., Awaitable[Entries]],
                  redraw: Callable[[], Coroutine[Any, Any, Any]] | None = None) -> Entries:
    """The page's rows. `rows` draws the settings of some headings as the launcher's
    other areas do, given `pairs` and `ahead`. `redraw` is awaited after a move in place of
    the page's rebuild."""
    library = context["library"]
    try:
        vpinfe, schema, monitors, names = await _beside(library)
    except Exception as exc:  # noqa: BLE001 - a page says why, never 500s
        return [(panel.HEADING, group.label),
                panel.intro(t("said.settings_unreadable"), hint=why(exc))]
    by_key = {one.key: one for one in group.settings}
    flat = {key: str((held or {}).get("value") or "") for key, held in values.items()}

    def rebuild() -> None:
        context.pop("config_values", None)
        asyncio.create_task((redraw or context["rebuild"])())

    here = screens.numbered(monitors, names)
    try:
        program = await offload.io(library.launcher_program, context["launcher"]["launcher_id"])
    except Exception:  # noqa: BLE001 - the answer is worked out without the program
        logger.debug("No program for the displays answer", exc_info=True)
        program = None
    page = Page(
        library, context["launcher"]["launcher_id"], rebuild, flat=flat,
        vpinfe=displays.standing(vpinfe, schema), here=here,
        logged=sorted({name for key in vpx.DISPLAY.values() if key in by_key
                       for name in by_key[key].reported}),
        cabinet=displays.cabinet(vpinfe, schema),
        units_now=units.resolved(displays.stored(vpinfe, schema, "manager", "units")),
        defaults={key: str(by_key[key].default or "") for key in vpx.SCREEN_SIZE
                  if key in by_key}, program=program)
    kept = trimmed(shown, page.on)
    after = size_row_goes_before(shown)
    joiner = next((pair.joiner for heading, _ in shown for pair in heading.pairs
                   if pair.key == "size" and pair.joiner), "")
    sized = page.size_row(joiner, [by_key[key].label if key in by_key else key
                                   for key in vpx.SCREEN_SIZE])
    heading, *picture_rows = page.picture_rows()
    out: Entries = [heading, panel.intro(t("manager.vpx_displays.intro")),
                    panel.intro(t("manager.vpx_displays.intro_where")), *picture_rows]
    out += await rows([(heading.label, heading.note, fields) for heading, fields in kept],
                      pairs=pairs_for(shown, page.on), ahead={after: sized} if after else {})
    return out if after else [*out, *sized]


async def _beside(library: Any) -> tuple[dict[str, Any], list[dict], list[Any], list[str]]:
    """What the page compares with: VPinFE's settings, and this device's screens."""
    schema = await offload.io(library.config_schema)
    vpinfe = await offload.io(library.config_values)
    monitors = await offload.io(screens.connected)
    names = await offload.io(display_service.get_display_names)
    return vpinfe, schema, monitors, list(names)


async def _launchers(library: Any) -> list[dict[str, Any]]:
    """The enabled Visual Pinball launchers whose program has settings."""
    found = await offload.io(library.launchers)
    return [one for one in found.get("launchers") or []
            if one.get("app") == VPX_APP and one.get("enabled") and one.get("has_config")]


def _reported(found: dict[str, Any]) -> list[str]:
    """The screen names Visual Pinball has logged, as its Displays settings carry them."""
    return sorted({name for group in config_groups(found) if applies(group)
                   for one in group.settings if one.key in vpx.DISPLAY.values()
                   for name in one.reported})


@on_page
async def _each_launcher(library: Any, act: Callable[[str], Awaitable[None]]) -> None:
    """`act` for each enabled Visual Pinball launcher whose program has settings. One
    that has never run has no file yet, so the writes make it. One that fails is named on
    the page and the rest still run."""
    try:
        found = await _launchers(library)
    except Exception:  # noqa: BLE001 - VPinFE's own change is already kept
        logger.warning("Could not list the launchers to follow VPinFE", exc_info=True)
        return
    failed = Failures()
    for one in found:
        try:
            await act(one["launcher_id"])
        except Exception as exc:  # noqa: BLE001 - say which, and carry on with the rest
            logger.warning("Could not move %s to follow VPinFE", one.get("launcher_id"),
                           exc_info=True)
            failed.add(str(one.get("display_name") or one["launcher_id"]), why(exc))
    failed.say("manager.vpx_displays.follow_failed")


async def follow(library: Any, before: displays.Standing, after: displays.Standing,
                 here: Sequence[Screen]) -> None:
    """Move each Visual Pinball launcher to where VPinFE's screens now are, wherever the
    launcher still matched where they were."""
    await _each_launcher(library, partial(_follow_one, library, before=before, after=after,
                                          here=here))


async def follow_dmd(library: Any, kind: str) -> None:
    """Tell each Visual Pinball launcher which kind of DMD panel is on the cabinet."""
    await _each_launcher(library, partial(_follow_panel, library, kind))


async def fill(client: Any, launcher_id: str, standing: displays.Standing,
               here: Sequence[Screen], kind: str) -> dict[str, str]:
    """Writes the answer into what the launcher holds no value for, making its settings file
    where the program has never run. A part it already answers stays where it was put.
    Returns the keys written."""
    found = await offload.io(client.launcher_config, launcher_id)
    held = dict(found.get("values") or {})
    keys = filling(standing, here, _reported(found), held, kind, found.get("program"))
    if keys:
        await offload.io(client.write_launcher_config, launcher_id, keys, create=True)
    return keys


def filling(standing: displays.Standing, here: Sequence[Screen], logged: Sequence[str],
            held: Mapping[str, Any], kind: str,
            program: Mapping[str, Any] | None = None) -> dict[str, str]:
    """The ini keys `fill` writes. `held` is the launcher's settings as read, each with
    whether the file sets it; `kind` is the DMD panel's, or "" with no panel; `program` is
    what the launcher reports of the installed program."""
    flat = {key: str((one or {}).get("value") or "") for key, one in held.items()}

    def set_here(key: str) -> bool:
        one = held.get(key) or {}
        return bool(one.get("set_here") and str(one.get("value") or "").strip())

    def open_to_answer(part: str) -> bool:
        return not any(set_here(key) for key in (vpx.DISPLAY[part], vpx.OUTPUT.get(part, "")))

    moved = {part: where for part, where in standing.on.items()
             if where is not None and open_to_answer(part)}
    keys = vpx.answer(moved, {**vpx.placed(flat, here), **moved}, here, logged, flat,
                      turn=standing.turn if vpx.PLAYFIELD in moved
                      and not set_here(vpx.VIEW_CAB_ROTATION) else None,
                      program=program).keys \
        if moved else {}
    if not vpx.dmd_kind(flat):
        keys.update(vpx.dmd_util(kind))
    return keys


async def disagreeing(client: Any, launcher_id: str, standing: displays.Standing,
                       here: Sequence[Screen]) -> list[str]:
    """The parts the launcher's file sets somewhere other than VPinFE has them. Read after
    `fill`, so what is left is what someone put there."""
    found = await offload.io(client.launcher_config, launcher_id)
    return conflicts(standing, here, {
        key: str((one or {}).get("value") or "")
        for key, one in (found.get("values") or {}).items() if (one or {}).get("set_here")})


async def match_launcher(client: Any, launcher_id: str, standing: displays.Standing,
                         here: Sequence[Screen]) -> vpx.Written:
    """Puts the launcher's screens where VPinFE has them. What it left out is in the
    answer."""
    found = await offload.io(client.launcher_config, launcher_id)
    flat = _flat_values(found)
    _, written = matching(standing, here, _reported(found), flat, vpx.placed(flat, here),
                          found.get("program"))
    if written.keys:
        await offload.io(client.write_launcher_config, launcher_id, written.keys, create=True)
    return written


@dataclass(frozen=True)
class Apart:
    """A launcher whose screens are not VPinFE's: the parts, and those Match leaves for
    when Visual Pinball has named their screens."""
    launcher_id: str
    name: str
    parts: tuple[str, ...]
    waiting: tuple[str, ...]
    can_match: bool


async def apart(library: Any, standing: displays.Standing,
                here: Sequence[Screen]) -> list[Apart]:
    """Each Visual Pinball launcher that holds a screen somewhere VPinFE does not."""
    out = []
    for one in await _launchers(library):
        found = await offload.io(library.launcher_config, one["launcher_id"])
        flat = _flat_values(found)
        on = vpx.placed(flat, here)
        parts = differing(standing, here, on, flat.get(vpx.VIEW_CAB_ROTATION, ""))
        if not parts:
            continue
        _, written = matching(standing, here, _reported(found), flat, on, found.get("program"))
        out.append(Apart(one["launcher_id"], str(one.get("display_name") or one["launcher_id"]),
                         tuple(parts), written.unnamed,
                         any(flat.get(key) != value for key, value in written.keys.items())))
    return out


async def apart_rows(library: Any, standing: displays.Standing, here: Sequence[Screen],
                     refill: Callable[[], None]) -> Entries:
    """The row under VPinFE's screens for each launcher that disagrees with them, with a
    Match for it. None where they all agree, or where they cannot be read."""
    try:
        found = await apart(library, standing, here)
    except Exception:  # noqa: BLE001 - the page is drawn without the row
        logger.warning("Could not compare the launchers with VPinFE's screens", exc_info=True)
        return []

    @on_page
    async def match(one: Apart) -> None:
        try:
            written = await match_launcher(library, one.launcher_id, standing, here)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            return
        say_unnamed(written)
        refill()

    def draw() -> None:
        for one in found:
            with ui.element("div").classes("manager-screens-differs"):
                with ui.column().classes("gap-0 min-w-0 grow"):
                    ui.label(t("manager.screens.different")) \
                        .classes("manager-screens-differs-title")
                    ui.label(t("manager.screens.apart", launcher=one.name,
                               parts=named(one.parts))).classes("manager-help")
                    if one.waiting:
                        ui.label(t("manager.screens.match_waits",
                                   parts=named(one.waiting))).classes("manager-help")
                panel.action(t("manager.screens.match_vpinfe"), partial(match, one),
                             icon=verbs.REVERT, hint=t("manager.screens.match_vpinfe.help"),
                             enabled=one.can_match)()

    return [(panel.FULL, draw)] if found else []


def _flat_values(found: Mapping[str, Any]) -> dict[str, str]:
    return {key: str((one or {}).get("value") or "")
            for key, one in (found.get("values") or {}).items()}


async def _follow_panel(library: Any, kind: str, launcher_id: str) -> None:
    keys = vpx.dmd_util(kind)
    if keys:
        await run.io_bound(library.write_launcher_config, launcher_id, keys, create=True)


async def _follow_one(library: Any, launcher_id: str, *, before: displays.Standing,
                      after: displays.Standing, here: Sequence[Screen]) -> None:
    found = await offload.io(library.launcher_config, launcher_id)
    keys = following(before, after, here, _reported(found), _flat_values(found),
                     found.get("program"))
    if keys:
        await run.io_bound(library.write_launcher_config, launcher_id, keys, create=True)


def following(before: displays.Standing, after: displays.Standing, here: Sequence[Screen],
              logged: Sequence[str], flat: Mapping[str, str],
              program: Mapping[str, Any] | None = None) -> dict[str, str]:
    """The ini keys that move a launcher with VPinFE's screens: the parts it had where
    VPinFE had them, and the turn too if it still held what VPinFE's gave it."""
    mine = vpx.placed(flat, here)
    changed = {part: where for part, where in after.on.items() if before.on.get(part) != where}
    moved = vpx.followed(before.on, changed, mine, vpx.holds_nothing(flat))
    now = {**mine, **moved}
    given = vpx.view_cab_rotation(
        next((one for one in here if one.number == before.on.get(vpx.PLAYFIELD)), None),
        before.turn[1])
    turned = after.turn != before.turn or vpx.PLAYFIELD in changed
    held_turn = flat.get(vpx.VIEW_CAB_ROTATION, "")
    holds = not held_turn.strip() or vpx.rotation_follows(given, held_turn)
    turn = after.turn if turned and holds \
        and now.get(vpx.PLAYFIELD) == after.on.get(vpx.PLAYFIELD) else None
    if not moved and turn is None:
        return {}
    return vpx.answer(moved, now, here, logged, flat, turn=turn, program=program).keys
