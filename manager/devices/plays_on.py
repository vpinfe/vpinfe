"""Which frontend devices won't play a video, from what each device's browser said.

Never the Manager's own browser: it is not the cabinet, and it previews perfectly a file
the cabinet cannot play at all. Only what a device's frontend reported about itself, as
the device registry keeps it.

Only the devices that play this install's library are judged: this install's own frontend,
and any device whose frontend said it plays this library. A device that is merely in the
registry, or on a version that does not say which library it plays, is left out - a file it
cannot play is no concern of this library's.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from common.host import frontend_browser
from common.i18n import t
from manager.devices.devices import device_label
from manager.kit import when
from manager.settings import settings

_NAMES = {one.id: one.name for one in frontend_browser.FORMATS}
_VIDEO = {one.id for one in frontend_browser.FORMATS if one.kind == frontend_browser.VIDEO}

# The states the Browser settings page leads with, and the codecs its finding is about.
_FIXED_BY_ITS_BROWSER = {frontend_browser.NO_H264: {"h264"},
                         frontend_browser.NO_VIDEO: {"h264", "vp9"}}


@dataclass(frozen=True)
class Refusal:
    label: str
    why: str


def _formats(device: dict[str, Any]) -> dict[str, Any]:
    return dict((device.get("browser") or {}).get("formats") or {})


def plays_here(device: dict[str, Any], local_id: str) -> bool:
    """Whether this device's frontend plays the library `local_id` holds. A device that
    said which library plays it only if that is this one, and one whose library did not
    answer is not judged. One that did not say counts only where it is this install, whose
    frontend plays its own library until it says otherwise."""
    said = (device.get("browser") or {}).get("library")
    if isinstance(said, str):
        return bool(said) and said == local_id
    return str(device.get("device_id") or "") == local_id


def asks_codecs(devices: Sequence[dict[str, Any]], local_id: str) -> bool:
    """Whether a device that plays this library said no to a video format: until one does,
    no file's codec can matter."""
    return any(_formats(device).get(one) is False for device in devices
               if plays_here(device, local_id) for one in _VIDEO)


def players(devices: Sequence[dict[str, Any]], local_id: str) -> list[dict[str, Any]]:
    """Each device that plays this library as a conversion reads it: a label and what its
    browser said of each video codec, `None` where it said nothing."""
    return [{"label": device_label(device),
             "plays": {one: _formats(device).get(one) for one in sorted(_VIDEO)}}
            for device in devices if plays_here(device, local_id)]


def refusals(codec: str | None, devices: Sequence[dict[str, Any]],
             local_id: str) -> list[Refusal]:
    """One for each of `devices` that plays this library and whose browser said it does
    not play `codec`, naming the codec."""
    if not codec:
        return []
    playing = [device for device in devices if plays_here(device, local_id)]
    alone = all(str(device.get("device_id") or "") == local_id for device in playing)
    name = _NAMES.get(codec, codec)
    found = []
    for device in playing:
        if _formats(device).get(codec) is not False:
            continue
        here = str(device.get("device_id") or "") == local_id
        label = t("manager.plays_on.wont_play_here", codec=name) if here and alone \
            else t("manager.plays_on.wont_play_on", device=device_label(device), codec=name)
        found.append(Refusal(label, _why(device, codec, here)))
    return found


def refusers(codecs: Iterable[str | None], devices: Sequence[dict[str, Any]],
             local_id: str) -> list[str]:
    """The name of each device that plays this library and whose browser said it does not
    play at least one of `codecs`."""
    asked = {one for one in codecs if one}
    return [device_label(device) for device in devices
            if plays_here(device, local_id)
            and any(_formats(device).get(one) is False for one in asked)]


def refused_here(codecs: Iterable[str | None], devices: Sequence[dict[str, Any]],
                 local_id: str) -> bool:
    """Whether this install's own frontend plays this library and said no to one of
    `codecs`: the case its own Browser settings can fix."""
    asked = {one for one in codecs if one}
    return any(str(device.get("device_id") or "") == local_id
               and plays_here(device, local_id)
               and any(_formats(device).get(one) is False for one in asked)
               for device in devices)


def _why(device: dict[str, Any], codec: str, here: bool) -> str:
    name = _NAMES.get(codec, codec)
    browser = device.get("browser") or {}
    state = str(browser.get("state") or "")
    if codec not in _FIXED_BY_ITS_BROWSER.get(state, set()):
        cause = t("manager.plays_on.why", codec=name)
    else:
        place = " › ".join(settings.place_of("chromium"))
        cause = t("manager.plays_on.why_fix_here" if here else "manager.plays_on.why_fix_there",
                  codec=name, place=place)
    checked = str(browser.get("checked_at") or "")
    if not checked:
        return cause
    return " ".join((cause, t("manager.plays_on.as_of", when=when.local(checked))))
