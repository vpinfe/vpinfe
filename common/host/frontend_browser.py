"""What this device's frontend browser can play, and how to fix what it cannot.

The frontend's controller window reports here through `record`; `current` is what the API
serves.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import threading
from dataclasses import dataclass
from typing import Any

from common.atomic_write import write_atomic
from common.config import paths
from common.devices import device_registry, install_identity
from common.i18n import t, t_source
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.host.frontend_browser")

VIDEO = "video"
AUDIO = "audio"


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    kind: str


# The page probes these ids; anything else it sends is dropped.
FORMATS = (
    Format("h264", "H.264", VIDEO),
    Format("hevc", "HEVC", VIDEO),
    Format("vp9", "VP9", VIDEO),
    Format("av1", "AV1", VIDEO),
    Format("aac", "AAC", AUDIO),
    Format("mp3", "MP3", AUDIO),
    Format("vorbis", "Vorbis", AUDIO),
    Format("opus", "Opus", AUDIO),
)
_IDS = {one.id for one in FORMATS}

PLAYS = "plays"
NO_H264 = "no_h264"
NO_VIDEO = "no_video"
NO_BROWSER = "no_browser"
UNKNOWN = "unknown"

USE_CHROME = "use_chrome"

# Whether this build ships a browser of its own.
FULL = "full"
SLIM = "slim"

_FINDINGS = {NO_H264: "frontend_browser.finding.no_h264",
             NO_VIDEO: "frontend_browser.finding.no_video",
             NO_BROWSER: "frontend_browser.finding.no_browser"}

_lock = threading.Lock()
_logged: tuple[str, str] | None = None


@dataclass(frozen=True)
class Fix:
    key: str
    action: str = ""
    chrome_path: str = ""


def _answer(can_play: Any, decoded: Any) -> bool | None:
    """A decode that ran is the answer; otherwise what `canPlayType` said, where it said
    anything."""
    if isinstance(decoded, bool):
        return decoded
    if can_play in ("probably", "maybe"):
        return True
    if can_play == "":
        return False
    return None


def _browser_name(brands: Any, user_agent: str) -> str:
    """"Chromium 145.0.7632.0" from the page's brand list, else the user agent's major
    version. The list carries a made-up "Not A Brand" entry on purpose, which is skipped."""
    named = [(str(one.get("brand") or ""), str(one.get("version") or ""))
             for one in brands if isinstance(one, dict)] if isinstance(brands, list) else []
    real = [one for one in named if one[0] and "brand" not in one[0].lower()]
    chosen = next((one for one in real if one[0] != "Chromium"), None) \
        or next(iter(real), None)
    if chosen:
        return f"{chosen[0]} {chosen[1]}".strip()
    found = re.search(r"(Edg|Chrome|Chromium)/(\d+)", user_agent or "")
    if not found:
        return ""
    name = {"Edg": "Microsoft Edge"}.get(found.group(1), "Chromium")
    return f"{name} {found.group(2)}"


RENDERER_CHARS = 120
# What a graphics chip's name is made of. The summary is Markdown, so the characters that
# make a link, emphasis or a table cell are not kept.
_RENDERER_DROPPED = re.compile(r"[^A-Za-z0-9 .,()/:+-]")
# A ceiling on every number the page sends, so a page that lies cannot fill the report.
PIXELS = 100_000
GAMEPADS = 32
FEATURE_FLAGS = ("webgl", "webgl2", "webgpu", "offscreen_canvas")
# Renderer names that mean the picture is drawn on the processor.
SOFTWARE_RENDERERS = ("swiftshader", "llvmpipe", "softpipe", "software", "basic render",
                      "microsoft basic")


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _whole(value: Any, ceiling: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if 0 <= value <= ceiling else None


def _decode(raw: Any) -> dict[str, bool | None] | None:
    if not isinstance(raw, dict):
        return None
    return {"supported": _bool(raw.get("supported")), "smooth": _bool(raw.get("smooth")),
            "power_efficient": _bool(raw.get("power_efficient"))}


def _screen(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    scale = raw.get("scale")
    return {"scale": round(float(scale), 2)
            if isinstance(scale, int | float) and not isinstance(scale, bool)
            and 0 < scale <= 16 else None,
            "width": _whole(raw.get("width"), PIXELS), "height": _whole(raw.get("height"), PIXELS),
            "extended": _bool(raw.get("extended"))}


def _features(raw: Any) -> dict[str, Any] | None:
    """What the page said it can draw with and decode with, read as plain values. None
    where it sent nothing, so a section can tell not tested from not available."""
    if not isinstance(raw, dict) or not raw:
        return None
    renderer = _RENDERER_DROPPED.sub("", str(raw.get("renderer") or ""))
    said = raw.get("decodes")
    decodes: dict[str, Any] = said if isinstance(said, dict) else {}
    return {**{name: _bool(raw.get(name)) for name in FEATURE_FLAGS},
            "renderer": renderer[:RENDERER_CHARS].strip(),
            "decodes": {one: _decode(decodes.get(one)) for one in ("h264", "vp9")},
            "gamepads": _whole(raw.get("gamepads"), GAMEPADS),
            "screen": _screen(raw.get("screen")),
            "took_ms": _whole(raw.get("took_ms"), 600_000)}


def software_renderer(renderer: str) -> bool:
    """Whether a renderer name is the processor drawing in place of a graphics chip."""
    name = renderer.lower()
    return any(word in name for word in SOFTWARE_RENDERERS)


def _in_use() -> tuple[str, bool]:
    from common.devices import device_client

    return device_client.local().browser_in_use()


def _unset() -> tuple[str, bool]:
    from common.devices import device_client

    return device_client.local().browser_in_use("")


def _variant() -> str:
    from common.devices import device_client

    return FULL if device_client.local().has_bundled_browser() else SLIM


def _google_chrome() -> str:
    from common.devices import device_client

    return device_client.local().google_chrome_path() or ""


def _read() -> dict[str, Any] | None:
    try:
        held = json.loads(paths.FRONTEND_BROWSER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read %s; the browser is probed again when the frontend opens",
                       paths.FRONTEND_BROWSER_PATH, exc_info=True)
        return None
    return held if isinstance(held, dict) else None


def state_of(formats: dict[str, bool | None]) -> str:
    """Which of the states a set of answers is. Only H.264 and VP9 decide it."""
    h264, vp9 = formats.get("h264"), formats.get("vp9")
    if h264 is True:
        return PLAYS
    if h264 is False and vp9 is True:
        return NO_H264
    if h264 is False and vp9 is False:
        return NO_VIDEO
    return UNKNOWN


def fix_for(state: str, *, system: str, bundled: bool, in_use: str,
            chrome: str) -> Fix | None:
    """What to do about a state, on this OS, with this browser. Mirrors the order
    `get_chromium_path` finds a browser in, so a change there changes this."""
    if state == NO_BROWSER:
        return Fix("frontend_browser.fix.no_browser")
    if state not in (NO_H264, NO_VIDEO):
        return None
    if chrome and os.path.realpath(chrome) != os.path.realpath(in_use or "."):
        return Fix("frontend_browser.fix.use_chrome", USE_CHROME, chrome)
    if system == "Darwin":
        return Fix("frontend_browser.fix.install_chrome_mac")
    if system == "Linux" and bundled:
        return Fix("frontend_browser.fix.install_chrome_linux")
    return Fix("frontend_browser.fix.set_chrome")


def _log(state: str, browser: str, path: str, bundled: bool,
         formats: dict[str, bool | None], fix: Fix | None) -> None:
    """Once per browser and state."""
    global _logged
    with _lock:
        if _logged == (path, state):
            return
        _logged = (path, state)
    plays = ", ".join(one.name for one in FORMATS if formats.get(one.id))
    missing = ", ".join(one.name for one in FORMATS if formats.get(one.id) is False)
    logger.info("Frontend browser: %s (%s). Plays %s; not %s", browser or "unknown",
                "bundled" if bundled else path, plays or "nothing", missing or "nothing")
    if state in _FINDINGS:
        logger.warning("%s. %s", t_source(_FINDINGS[state]),
                       t_source(fix.key) if fix else "")


def record(raw: Any) -> dict[str, Any]:
    """Keep what the page reported. Untrusted: only known format ids and plain values are
    read out of it. Answers the report as `current()` would."""
    raw = raw if isinstance(raw, dict) else {}
    can_play = raw.get("can_play") if isinstance(raw.get("can_play"), dict) else {}
    decoded = raw.get("decoded") if isinstance(raw.get("decoded"), dict) else {}
    formats = {one: _answer(can_play.get(one), decoded.get(one)) for one in _IDS}
    path, bundled = _in_use()
    browser = _browser_name(raw.get("brands"), str(raw.get("user_agent") or ""))[:80]
    held = {"browser": browser, "path": path, "bundled": bundled, "formats": formats,
            "features": _features(raw.get("features")), "reported_at": utc_now_iso()}
    try:
        paths.FRONTEND_BROWSER_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(paths.FRONTEND_BROWSER_PATH,
                     lambda handle: json.dump(held, handle, indent=2, sort_keys=True))
    except OSError:
        logger.warning("Could not keep the browser report in %s",
                       paths.FRONTEND_BROWSER_PATH, exc_info=True)
    answer = current(held)
    _log(answer["state"], browser, path, bundled, formats,
         Fix(**answer["fix"]) if answer["fix"] else None)
    _record_self(answer)
    return answer


def _record_self(answer: dict[str, Any]) -> None:
    """Into this install's own registry entry, which no probe of it asks about."""
    try:
        device_registry.get_device_registry().record_reachable(
            install_identity.install_id(),
            browser=device_registry.browser_said(answer))
    except Exception:
        logger.debug("Could not keep the browser report in this install's registry entry",
                     exc_info=True)


def current(held: dict[str, Any] | None = None) -> dict[str, Any]:
    """The report as the API serves it: state, the browser, what it plays and does not,
    and the finding and fix in words."""
    path, bundled = _in_use()
    default_path, default_bundled = _unset()
    held = _read() if held is None else held
    if held and str(held.get("path") or "") != path:
        held = None
    if not path or not os.path.exists(path):
        state = NO_BROWSER
    elif held is None:
        state = UNKNOWN
    else:
        state = state_of(held.get("formats") or {})
    formats = {one.id: (held or {}).get("formats", {}).get(one.id) for one in FORMATS}
    fix = fix_for(state, system=platform.system(), bundled=bundled, in_use=path,
                  chrome=_google_chrome())
    return {
        "state": state,
        "browser": str((held or {}).get("browser") or ""),
        "path": path,
        "bundled": bundled,
        "default_path": default_path,
        "default_bundled": default_bundled,
        "variant": _variant(),
        "formats": formats,
        "plays": [one.name for one in FORMATS if formats[one.id]],
        "does_not_play": [one.name for one in FORMATS if formats[one.id] is False],
        "features": (held or {}).get("features"),
        "reported_at": (held or {}).get("reported_at"),
        "finding": t(_FINDINGS[state]) if state in _FINDINGS else "",
        "fix": ({"key": fix.key, "action": fix.action, "chrome_path": fix.chrome_path}
                if fix else None),
        "fix_text": t(fix.key) if fix else "",
    }


def available() -> bool | tuple[bool, str]:
    """For the `media_playback` capability: false only where nothing can play."""
    found = current()
    if found["state"] in (NO_VIDEO, NO_BROWSER):
        return False, found["finding"]
    return True


def reset_for_tests() -> None:
    global _logged
    with _lock:
        _logged = None
