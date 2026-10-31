"""Convert the selected pictures, sounds and videos: ask first, then the conversion as a job.

The ask draws what the plan says. A choice is there only where the plan has something to
decide in, with the plan's options and its sentence for each, and a choice changed asks the
plan again because one answer changes what another may be.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from nicegui import ui

from common.failures import why
from common.games import media_convert
from common.host import tools
from common.i18n import t
from common.media.media_specs import CONVERTIBLE_AUDIO, CONVERTIBLE_VIDEO, VIDEO_FAMILY
from manager.devices import plays_on
from manager.kit import dialog as frame
from manager.kit import dropdown, media_ownership, offload, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.settings import settings

_POLL_S = 1.0
_POLLS = 7200

_KINDS = ((media_convert.PICTURE, "manager.media_convert.pictures"),
          (media_convert.SOUND, "manager.media_convert.sounds"),
          (media_convert.VIDEO, "manager.media_convert.videos"))

# What each option of each choice is called. The plan's own `label` names the choice and
# its `why` says what the option does.
_WORDS = {
    (media_convert.PICTURE, "png"): "manager.media_convert.picture_png",
    (media_convert.PICTURE, "webp"): "manager.media_convert.picture_webp",
    (media_convert.PICTURE, "jpg"): "manager.media_convert.picture_jpg",
    (media_convert.SOUND, "mp3"): "manager.media_convert.sound_mp3",
    (media_convert.SOUND, "ogg"): "manager.media_convert.sound_ogg",
    (media_convert.CONTAINER, "mp4"): "manager.media_convert.container_mp4",
    (media_convert.CONTAINER, "webm"): "manager.media_convert.container_webm",
    (media_convert.VIDEO, "h264"): "manager.media_convert.video_h264",
    (media_convert.VIDEO, "vp9"): "manager.media_convert.video_vp9",
    (media_convert.VIDEO, "av1"): "manager.media_convert.video_av1",
    (media_convert.QUALITY, "standard"): "manager.media_convert.quality_standard",
    (media_convert.QUALITY, "high"): "manager.media_convert.quality_high",
    (media_convert.QUALITY, "small"): "manager.media_convert.quality_small",
    (media_convert.SOUND_IN_VIDEO, "keep"): "manager.media_convert.sound_keep",
    (media_convert.SOUND_IN_VIDEO, "remove"): "manager.media_convert.sound_remove",
    (media_convert.ON_CLASH, "leave"): "manager.media_convert.clash_leave",
    (media_convert.ON_CLASH, "replace"): "manager.media_convert.clash_replace",
    (media_convert.KEEP, True): "manager.media_convert.keep_yes",
    (media_convert.KEEP, False): "manager.media_convert.keep_no",
    (media_convert.WAIT, True): "manager.media_convert.wait_yes",
    (media_convert.WAIT, False): "manager.media_convert.wait_no",
}


def local_id_of(library: Any) -> str:
    return str(library.discovery().get("install_id") or "")


def convertible(rows: list[dict[str, Any]], devices: list[dict[str, Any]] | None = None,
                local_id: str = "") -> list[tuple[str, str]]:
    """The files among these media rows a conversion applies to, once each: one in a format
    VPinFE cannot use, and a video a device that plays this library says it will not play.
    `devices` is the page's device list, as `plays_on` reads it."""
    found: dict[tuple[str, str], None] = {}
    for row in rows:
        if not row.get("present"):
            continue
        via = row.get("via")
        wont_play = via not in media_ownership.LOOSE and bool(
            plays_on.refusals(row.get("video_codec"), devices or [], local_id))
        if via == media_ownership.UNSUPPORTED or wont_play:
            found[(str(row["game_id"]), str(row.get("path") or ""))] = None
    return list(found)


def _word(key: str, value: Any) -> str:
    named = _WORDS.get((key, value))
    return t(named) if named else str(value)


def lines(plan: dict[str, Any]) -> list[str]:
    """What the plan will do, a line for each kind it has files of, and how the videos
    are made."""
    working = [item for item in plan["items"] if item["action"] != media_convert.SKIP]
    said: list[str] = []
    for kind, key in _KINDS:
        mine = [item for item in working if item.get("kind") == kind]
        if not mine:
            continue
        said.append(t(key, count=len(mine)))
        if kind == media_convert.VIDEO:
            repackaged = sum(1 for item in mine if item["action"] == media_convert.REPACKAGE)
            encoded = len(mine) - repackaged
            if repackaged and encoded:
                said.append(t("manager.media_convert.mixed", repackaged=repackaged,
                              encoded=encoded))
            else:
                said.append(t("manager.media_convert.repackaged" if repackaged
                              else "manager.media_convert.encoded"))
    return said


def left_out(plan: dict[str, Any], hide: frozenset[str] = frozenset()) -> tuple[int, str]:
    """How many files the plan leaves as they are, and the reasons, each said once. A
    reason in `hide` is said elsewhere on the page."""
    skipped = [item for item in plan["items"] if item["action"] == media_convert.SKIP]
    return len(skipped), "; ".join(dict.fromkeys(
        str(item["reason"]) for item in skipped
        if item.get("reason") and item["reason"] not in hide))


def has_work(plan: dict[str, Any]) -> bool:
    return any(item["action"] != media_convert.SKIP for item in plan["items"])


def needs_ffmpeg(plan: dict[str, Any]) -> bool:
    """Whether the plan has sounds or videos and no usable FFmpeg to make them with; a
    picture needs none. A file left out has no kind, so it is read from its extension."""
    if plan.get("ffmpeg") == tools.State.FOUND.value:
        return False
    needing = (*CONVERTIBLE_AUDIO, *CONVERTIBLE_VIDEO, *VIDEO_FAMILY)
    return any(Path(str(item["path"])).suffix.lower() in needing for item in plan["items"])


def outcome(result: dict[str, Any]) -> tuple[str, Literal["positive", "warning", "info"]]:
    """What the conversion says when it ends, and the notice type to say it with."""
    done = int(result.get("converted") or 0) + int(result.get("renamed") or 0)
    skipped = len(result.get("skipped") or ())
    said = t("manager.media_convert.converted", count=done)
    if skipped:
        return t("manager.media_convert.converted_skipped", converted=said,
                 skipped=skipped), "warning"
    return said, "positive"


@on_page
async def ask(picked: list[dict[str, Any]], library: Any, state: dict[str, Any],
              then: Callable[[], Any]) -> None:
    """Convert the files among `picked`, once the user has said what to make them."""
    known, local_id = list(state.get("devices") or []), local_id_of(library)
    files = convertible(picked, known, local_id)
    if not files:
        ui.notify(t("manager.media_convert.nothing"), type="info")
        return
    devices = plays_on.players(known, local_id)
    try:
        plan = await offload.io(ApiClient().convertible_media, files, {}, devices)
    except Exception as exc:  # noqa: BLE001 - said, and nothing was converted
        ui.notify(t("manager.media_convert.could_not_plan"), caption=why(exc), type="warning")
        return
    chosen = await _choose(files, plan, devices)
    if chosen is None:
        return

    async def converted() -> None:
        library.forget_watched()
        answer = then()
        if inspect.isawaitable(answer):
            await answer

    await run(lambda: ApiClient().convert_media(files, chosen, devices), state, converted)


async def _choose(files: list[tuple[str, str]], plan: dict[str, Any],
                  devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The choices the person made, or `None` where they cancelled. A choice not touched is
    absent, so it follows the recommendation as the others change."""
    held: dict[str, Any] = {}
    shown: dict[str, Any] = {"plan": plan, "asked": 0}

    @on_page
    async def picked(key: str, values: dict[str, Any], text: str) -> None:
        before = dict(held)
        held[key] = values[text]
        shown["asked"] += 1
        mine = shown["asked"]
        go.set_enabled(False)
        try:
            found = await offload.io(ApiClient().convertible_media, files, dict(held),
                                     devices)
        except Exception as exc:  # noqa: BLE001 - the last plan stands, with its choices
            if mine == shown["asked"]:
                held.clear()
                held.update(before)
                ui.notify(t("manager.media_convert.could_not_plan"), caption=why(exc),
                          type="warning")
                body.refresh()
                go.set_enabled(has_work(shown["plan"]))
            return
        if mine != shown["asked"]:
            return
        shown["plan"] = found
        body.refresh()
        go.set_enabled(has_work(found))

    @ui.refreshable
    def body() -> None:
        current = shown["plan"]
        for line in lines(current):
            ui.label(line).classes("manager-confirm-line")
        blocked = str(current.get("video_blocked") or "")
        said_here = {t("error.media_convert.no_ffmpeg")} if needs_ffmpeg(current) else set()
        if blocked:
            said_here.add(blocked)
            ui.label(blocked).classes("manager-help px-3")
        skipped, reasons = left_out(current, frozenset(said_here))
        with ui.grid(columns="max-content minmax(0, 1fr)") \
                .classes("w-full items-start gap-x-4 gap-y-3 px-3"):
            for choice in current["options"]:
                _draw_choice(choice, current["choices"], picked)
        if skipped:
            ui.label(t("manager.media_convert.left_out", count=skipped)) \
                .classes("manager-confirm-line")
            if reasons:
                ui.label(reasons).classes("manager-help px-3")
        if needs_ffmpeg(current):
            with ui.row().classes("items-center gap-2 px-3"):
                ui.label(t("manager.media_convert.no_ffmpeg")).classes("manager-help")
                frame.quiet(t("manager.media_convert.open_tools"),
                            lambda: ui.navigate.to(settings.address_for("tools")),
                            icon=verbs.GO)

    with frame.opened(t("manager.media_convert.ask", count=len(files)), wide=True) as box:
        body()
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("manager.media_convert.convert"),
                              lambda: box.submit(dict(held)), icon=verbs.REPLACE)
            go.set_enabled(has_work(plan))
        frame.enter_presses(go)
    return await box


def _draw_choice(choice: dict[str, Any], current: dict[str, Any],
                 picked: Callable[[str, dict[str, Any], str], Any]) -> None:
    """One choice as a labelled select over the options that can be made, the sentence for
    the one chosen under it, and why the others cannot be made."""
    key = str(choice["key"])
    options = choice["options"]
    values = {str(option["value"]): option["value"] for option in options
              if option.get("available", True)}
    named = {str(option["value"]): _label(key, option) for option in options
             if option.get("available", True)}
    on = str(current.get(key))
    ui.label(str(choice["label"])).classes("manager-label pt-2")
    with ui.column().classes("gap-1 min-w-0"):
        if named:
            dropdown.Select(named, value=on if on in named else next(iter(named)),
                            on_change=lambda event: picked(key, values, str(event.value)))
        for option in options:
            if str(option["value"]) == on or not option.get("available", True):
                ui.label(str(option["why"])).classes("manager-help")


def _label(key: str, option: dict[str, Any]) -> str:
    word = _word(key, option["value"])
    if option.get("preferred"):
        return t("manager.media_convert.your_preference", word=word)
    return t("manager.media_convert.recommended", word=word) if option.get("recommended") else word


@on_page
async def run(start: Callable[[], dict], state: dict[str, Any],
              then: Callable[[], Any]) -> None:
    """Start the conversion and say what it did once it ends; the footer line reports it
    while it runs. `then` runs when anything was converted."""
    client = ApiClient()
    try:
        job = await offload.io(start)
    except Exception as exc:  # noqa: BLE001 - said, and nothing was converted
        ui.notify(t("manager.media_convert.could_not_start"), caption=why(exc), type="warning")
        return
    watch = state.get("watch_jobs")
    if callable(watch):
        watch()
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        try:
            found = await offload.io(client.job, str(job.get("id") or ""))
        except Exception:  # noqa: BLE001 - the footer line still reports it
            return
        if found.get("state") == "running":
            continue
        if found.get("state") == "failed":
            ui.notify(t("manager.media_convert.failed"), caption=str(found.get("error") or ""),
                      type="negative")
            return
        result = found.get("result") or {}
        said, level = outcome(result)
        skipped = [str(one.get("reason") or "") for one in result.get("skipped") or ()]
        ui.notify(said, caption="; ".join(dict.fromkeys(skipped)), type=level)
        if any(int(result.get(key) or 0) for key in ("converted", "repackaged", "renamed")):
            answer = then()
            if inspect.isawaitable(answer):
                await answer
        return
