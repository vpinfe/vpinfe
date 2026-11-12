"""Settings > Hardware > Displays: the screens picture, over the windows' config."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import background_tasks, run, ui

from common.devices import device_client
from common.failures import why
from common.i18n import t
from common.settings import settings_waiting
from common.values import quarter_turn
from manager.kit import panel
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.settings import screens
from manager.settings import screens_picture as picture
from manager.settings.screens import Screen

KEY = "hardware.displays"

# What the picture draws, by config section.
DRAWN = {"windows.playfield": frozenset({"screen_id", "orientation", "rotation"}),
         "windows.backglass": frozenset({"screen_id"}),
         "windows.scoreview": frozenset({"screen_id"}),
         "windows.topper": frozenset({"screen_id"})}

Write = Callable[[dict[str, dict[str, Any]]], Awaitable[bool]]


@dataclass(frozen=True)
class Standing:
    """Where the screens are and how the playfield is turned: the answer other places
    (a launcher's own screens) are compared with, and move with."""
    on: Mapping[str, picture.Placement]
    turn: tuple[str, str]


# Told what the screens were and are after a change that was kept.
Follow = Callable[[Standing, Standing, Sequence[Screen]], Awaitable[Any]]


def without_drawn(blocks: Sequence[dict]) -> list[dict]:
    """The schema's sections with the settings the picture draws taken out, and any
    section left with nothing in it dropped."""
    kept = [{**block, "options": [one for one in block.get("options") or []
                                  if one.get("key") not in DRAWN.get(str(block.get("name")), ())]}
            for block in blocks]
    return [block for block in kept if block["options"]]


def stored(values: Mapping[str, Any], schema: Sequence[dict], section: str, key: str) -> Any:
    """A setting as the page reads it: what is stored, else what the schema defaults to."""
    held = values.get(section) or {}
    if key in held:
        return held[key]
    return next((one.get("default") for block in schema if block.get("name") == section
                 for one in block.get("options") or [] if one.get("key") == key), None)


def number_of(screen_id: Any) -> int | None:
    """The number a person counts a stored screen id as, or None for a window with no
    screen."""
    said = str(screen_id if screen_id is not None else "").strip()
    return int(said) + 1 if said.isdigit() else None


def placed(values: Mapping[str, Any], schema: Sequence[dict]) -> dict[str, picture.Placement]:
    return {part.key: number_of(stored(values, schema, f"windows.{part.key}", "screen_id"))
            for part in picture.vpinfe_parts()}


def standing(values: Mapping[str, Any], schema: Sequence[dict]) -> Standing:
    return Standing(placed(values, schema),
                    (str(stored(values, schema, "windows.playfield", "orientation")),
                     str(stored(values, schema, "windows.playfield", "rotation"))))


def cabinet(values: Mapping[str, Any], schema: Sequence[dict]) -> bool:
    return str(stored(values, schema, "presentation", "cab_mode")).strip().lower() in (
        "true", "1", "yes", "on")


def writes(moved: Mapping[str, picture.Placement], screens_here: Sequence[Screen],
           rotation: Any) -> dict[str, dict[str, Any]]:
    """The config for parts whose screen changed.

    A screen put on the playfield may stand in another shape, so its mounting is worked
    out again from the turn the playfield already has.
    """
    found = {f"windows.{part}": {"screen_id": number - 1 if isinstance(number, int) else ""}
             for part, number in moved.items()}
    number = moved.get(picture.PLAYFIELD)
    if isinstance(number, int):
        screen = next((one for one in screens_here if one.number == number), None)
        found["windows.playfield"]["orientation"] = picture.turn(
            quarter_turn(rotation), screen)["orientation"]
    return found


def test_card() -> picture.Test:
    """The rotation check's card, opened on this device by the Manager."""
    @on_page
    async def open_card(screen: Screen, rotation: int) -> bool:
        device = device_client.local()
        try:
            await run.io_bound(device.show_test_card, screen, rotation)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.screens.test_card_failed"), caption=why(exc), type="negative")
            return False
        if await run.io_bound(device.test_card_open):
            return True
        ui.notify(t("manager.screens.test_card_failed"), type="negative")
        return False

    async def turn(rotation: int) -> None:
        await run.io_bound(device_client.local().turn_test_card, rotation)

    async def close() -> None:
        await run.io_bound(device_client.local().close_test_card)

    async def hold() -> bool:
        return bool(await run.io_bound(device_client.local().keep_test_card))

    @on_page
    async def apply() -> None:
        client = ApiClient()
        try:
            answer = await run.io_bound(client.config_waiting) or {}
            if not waits_for_reopen(answer):
                return
            done = await run.io_bound(client.perform_action, "frontend", "restart") or {}
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        if done.get("performed"):
            ui.notify(t("manager.devices.windows_reopened"), type="positive")
        else:
            ui.notify(t("manager.devices.not_happen",
                        label=t("manager.waiting.reopen_windows")), type="warning")

    return picture.Test(open_card, turn, close, hold, apply)


def waits_for_reopen(answer: Mapping[str, Any]) -> bool:
    """Whether the saved playfield turn is waiting on windows being reopened, which can be
    done here. A turn that waits on a restart, or on nothing, is left to the bar over the
    page."""
    turned = [one for one in answer.get("waiting") or []
              if one.get("section") == "windows.playfield"
              and one.get("key") in ("orientation", "rotation")]
    return bool(answer.get("reopen")) and bool(turned) and all(
        one.get("waits_for") == settings_waiting.WAITS_REOPEN for one in turned)


def rows(values: Mapping[str, Any], schema: Sequence[dict], monitors: Sequence[Any],
         names: Sequence[str], write: Write,
         refill: Callable[[], None], follow: Follow | None = None,
         presets: Callable[[], Awaitable[Any]] | None = None) -> list[tuple[Any, Any]]:
    """The picture as a heading and a row spanning the page. `follow` is told of each
    change that was kept, and `presets` opens the preset dialog."""
    here = screens.numbered(monitors, names)
    held = placed(values, schema)
    turned = {"orientation": standing(values, schema).turn[0],
              "rotation": stored(values, schema, "windows.playfield", "rotation")}
    on_cabinet = cabinet(values, schema)

    def now() -> Standing:
        return Standing(dict(held), (str(turned["orientation"]), str(turned["rotation"])))

    behind = asyncio.Lock()

    def follows(before: Standing) -> None:
        if follow is None:
            return
        # Called here and awaited below: `follow` runs under the page that is current when it
        # is called, and the task that awaits it has none.
        following = follow(before, now(), here)

        async def go() -> None:
            async with behind:
                await following

        background_tasks.create(go(), name="manager-follow-screens")

    async def move(moved: dict[str, picture.Placement]) -> bool:
        lost = any(isinstance(number, int) and not any(one.number == number for one in here)
                   for number in held.values())
        before = now()
        found = writes(moved, here, turned["rotation"])
        if not await write(found):
            return False
        held.update(moved)
        turned["orientation"] = found.get("windows.playfield", {}).get(
            "orientation", turned["orientation"])
        if lost:
            refill()
        follows(before)
        return True

    async def point(degrees: int, screen: Screen) -> bool:
        found = picture.turn(degrees, screen)
        before = now()
        if not await write({"windows.playfield": found}):
            return False
        turned.update(found)
        follows(before)
        return True

    @on_page
    async def show(uses: dict[int, list[str]]) -> None:
        try:
            await run.io_bound(device_client.local().show_numbers, here, uses)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.screens.show_failed"), caption=why(exc), type="negative")

    def draw() -> None:
        picture.Picture(here, held, move=move, cabinet=on_cabinet, show=show,
                        rotation=turned["rotation"],
                        point=point, test=test_card(), presets=presets)

    return [(panel.HEADING, t("manager.settings.section_screens")), (panel.FULL, draw)]
