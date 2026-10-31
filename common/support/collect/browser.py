"""Frontend Browser: what the browser the frontend runs in can draw and play.

The numbers are what the frontend's controller window probed and reported, so a device whose
frontend has not opened since the probe was added says Not tested rather than guessing.
"""

from __future__ import annotations

import shlex
from typing import Any

from common.config.config_access import SettingsConfig, cfg_get
from common.config.paths import get_ini_config
from common.host import frontend_browser
from common.support.sections import Section, SectionText

# A value here is a switch or a name Chromium documents, so it is safe to print. Every other
# option is listed by name alone, because its value may be a path or an address.
VALUE_OPTIONS = ("--use-gl", "--use-angle", "--ozone-platform", "--ozone-platform-hint",
                 "--enable-features", "--disable-features", "--force-device-scale-factor",
                 "--high-dpi-support", "--enable-gpu-rasterization", "--disable-gpu",
                 "--in-process-gpu", "--use-vulkan", "--disable-software-rasterizer")


def collect() -> SectionText:
    report = frontend_browser.current()
    if report["state"] == frontend_browser.NO_BROWSER:
        return SectionText.ok(["No browser on this device."])
    lines = [f"- **Browser:** {report['browser'] or 'Not tested'}"
             + (" (bundled)" if report["bundled"] else "")]
    lines += _plays(report) + _features(report["features"]) + [""] + _flags()
    return SectionText.ok(lines)


def _plays(report: dict[str, Any]) -> list[str]:
    if report["state"] == frontend_browser.UNKNOWN:
        return ["- **Plays:** Not tested"]
    lines = [f"- **Plays:** {', '.join(report['plays']) or 'Nothing'}"]
    if report["does_not_play"]:
        lines.append(f"- **Does not play:** {', '.join(report['does_not_play'])}")
    return lines


def _available(found: bool | None) -> str:
    return "Not tested" if found is None else "Available" if found else "Not available"


def _features(held: dict[str, Any] | None) -> list[str]:
    if held is None:
        return ["- **Graphics and decoding:** Not tested"]
    lines = [f"- **WebGL 2:** {_available(held['webgl2'])}",
             f"- **WebGL:** {_available(held['webgl'])}",
             f"- **WebGPU:** {_available(held['webgpu'])}",
             f"- **Offscreen canvas:** {_available(held['offscreen_canvas'])}"]
    if held["renderer"]:
        lines.append(f"- **Graphics renderer:** {held['renderer']}")
        lines.append("- **Drawn on the processor:** "
                     + ("Yes" if frontend_browser.software_renderer(held["renderer"]) else "No"))
    for ident, name in (("h264", "H.264"), ("vp9", "VP9")):
        lines.append(f"- **{name} at 1080p, 60 frames a second:** "
                     f"{_decode(held['decodes'][ident])}")
    if held["gamepads"] is not None:
        lines.append(f"- **Gamepads seen by the browser:** {held['gamepads']}")
    if held["took_ms"] is not None:
        lines.append(f"- **Probe time:** {held['took_ms']} ms")
    return lines


def _decode(found: dict[str, bool | None] | None) -> str:
    if found is None or found["supported"] is None:
        return "Not tested"
    if not found["supported"]:
        return "Not available"
    words = ["Available"]
    if found["smooth"] is not None:
        words.append("smooth" if found["smooth"] else "not smooth")
    if found["power_efficient"] is not None:
        words.append("hardware" if found["power_efficient"] else "software")
    return ", ".join(words)


def _flags() -> list[str]:
    """The options the person added, the defaults they dropped, and whether VPinFE's own are
    on."""
    config = get_ini_config()
    settings = SettingsConfig.from_config(config)
    lines = ["### Browser Options", "",
             f"- **VPinFE's own options:** {'On' if settings.default_chrome_options else 'Off'}"]
    dropped = _names(cfg_get(config, "Settings", "chromeoptionsexclude", ""))
    if dropped:
        lines.append(f"- **Own options turned off:** {', '.join(dropped)}")
    added = _added(settings.chrome_options)
    lines.append(f"- **Options added:** {', '.join(added) if added else 'None'}")
    return lines


def _split(raw: str) -> list[str]:
    try:
        return shlex.split(raw or "", comments=False, posix=True)
    except ValueError:
        return []


def _options(raw: str) -> list[str]:
    """Only the tokens that are options: a value after a space is a path or an address, and it
    is not one of Chromium's `--name=value` forms, so it is dropped rather than named."""
    return [one for one in _split(raw) if one.startswith("-")]


def _names(raw: str) -> list[str]:
    return [one.partition("=")[0] for one in _options(raw)]


def _added(raw: str) -> list[str]:
    shown = []
    for option in _options(raw):
        name, _, value = option.partition("=")
        shown.append(option if name in VALUE_OPTIONS and value else name)
    return shown


SECTION = Section("browser", "Frontend Browser", 40, collect)
