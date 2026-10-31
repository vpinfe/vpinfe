"""The settings a theme is allowed to read, answered one question at a time."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any

from common.config.config_access import (
    ConfigSource,
    DisplayConfig,
    MediaConfig,
    SettingsConfig,
    cfg_bool,
    cfg_get,
    cfg_int,
    cfg_set,
)
from common.devices import serving
from common.extensions.store import get_extension_store
from common.host import addresses
from common.security import access_keys
from common.values import is_truthy

if TYPE_CHECKING:
    from frontend.api import API


def get_mainmenu_config(iniconfig: ConfigSource) -> dict[str, bool]:
    # No re-read: the store holds the live config and every write goes through save().
    # This used to reload the ini here, which under JSON meant handing a configparser a
    # JSON file - it raised on every menu open, the caller fell back to its default, and
    # hide_quit_button silently did nothing.
    return {
        "hideQuitButton": SettingsConfig.from_config(iniconfig).hide_quit_button,
    }


def get_attract_config(iniconfig: ConfigSource) -> dict[str, Any]:
    """What the person set for attract mode, in the units core's timers use.

    An order that is neither of the two comes back as `random`: it is a typed setting,
    and a typo must not stop the wheel.
    """
    order = cfg_get(iniconfig, "behavior", "attract_order", "random").strip().lower()
    return {
        "enabled": cfg_bool(iniconfig, "behavior", "attract_enabled", True),
        "idle_ms": max(1, cfg_int(iniconfig, "behavior", "attract_idle", 60)) * 1000,
        "step_ms": max(1, cfg_int(iniconfig, "behavior", "attract_step_time", 8)) * 1000,
        "order": "in_order" if order == "in_order" else "random",
        "on_return": ("stay" if cfg_get(iniconfig, "behavior", "attract_on_return",
                                        "restore").strip().lower() == "stay" else "restore"),
        "audio": cfg_bool(iniconfig, "behavior", "attract_audio", False),
    }


def _remote_urls(config: ConfigSource) -> list[str]:
    """The Remote's addresses, each carrying the Remote pass so a scan signs the phone in.
    None while this install answers only itself, since no phone can reach it."""
    network = serving.network(config)
    if not serving.reachable_from_elsewhere(network.http_bind):
        return []
    _, secret = access_keys.remote_pass()
    return [f"{url}?{access_keys.PASS_PARAM}={secret}"
            for url in addresses.urls(network.http_port, "/remote")]


def _build_remote_qr_svg(url: str) -> str:
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except Exception:
        return ""

    stream = BytesIO()
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    image.save(stream)
    return stream.getvalue().decode("utf-8")


def get_splashscreen_enabled(config: ConfigSource) -> str:
    return "true" if SettingsConfig.from_config(config).splashscreen else "false"


def set_audio_muted(api: API, muted: Any) -> bool:
    muted_flag = muted if isinstance(muted, bool) else is_truthy(muted)
    cfg_set(api._ini_config, "behavior", "mute_audio", bool(muted_flag))
    api._ini_config.save()
    api.send_event_all_windows_incself({
        "type": "AudioMuteChanged",
        "muted": muted_flag,
    })
    return muted_flag


def get_vpinplay_endpoint(config: ConfigSource) -> str:
    """Where VPinPlay is, for a theme that asks.

    The extension's answer, or the config's - the default, once a 2.x value has gone to the
    extension - where the extension holds none. A published
    theme may still call this to build a URL of its own, so it answers whether or not
    the extension is loaded.
    """
    held = str(get_extension_store().settings("vpinplay").get("endpoint") or "").strip()
    if held:
        return held
    return cfg_get(config, "vpinplay", "api_endpoint", "")


def get_media_priorities(config: ConfigSource) -> dict[str, str]:
    return MediaConfig.from_config(config).priority_payload()


def get_playfield_orientation(config: ConfigSource) -> str:
    return DisplayConfig.from_config(config).playfield_orientation


def get_playfield_rotation(config: ConfigSource) -> int:
    return DisplayConfig.from_config(config).playfield_rotation


def get_playfield_media_rotation(config: ConfigSource) -> str:
    return MediaConfig.from_config(config).playfield_media_rotation


def get_cab_mode(config: ConfigSource) -> bool:
    return DisplayConfig.from_config(config).cab_mode


def get_theme_assets_port(config: ConfigSource) -> int:
    return serving.own_port(config)


def get_http_port(config: ConfigSource) -> int:
    return serving.own_port(config)


def get_remote_link(config: ConfigSource) -> dict[str, Any]:
    urls = _remote_urls(config)
    preferred_url = addresses.to_scan(urls)
    return {
        "url": preferred_url,
        "urls": urls,
        "qr_svg": _build_remote_qr_svg(preferred_url) if preferred_url else "",
    }


def get_manager_link(config: ConfigSource) -> dict[str, Any]:
    """Where the Manager is from another device, for the main menu to show and scan.

    Empty while this install answers only itself. The Manager asks for its own sign-in, so
    unlike the Remote's addresses these carry no pass.
    """
    network = serving.network(config)
    resolving = addresses.resolving()
    urls = (addresses.urls(network.http_port, "/manager")
            if serving.reachable_from_elsewhere(network.http_bind) else [])
    preferred_url = addresses.to_scan(urls)
    return {
        "url": preferred_url,
        "urls": urls,
        "qr_svg": _build_remote_qr_svg(preferred_url) if preferred_url else "",
        "resolving": bool(urls) and resolving,
    }


def get_vpinplay_multi_link(config: ConfigSource) -> dict[str, Any]:
    """The main menu's second QR: the Remote's Join screen."""
    urls = [f"{url}&screen=join" for url in _remote_urls(config)]
    preferred_url = addresses.to_scan(urls)
    return {
        "url": preferred_url,
        "urls": urls,
        "qr_svg": _build_remote_qr_svg(preferred_url) if preferred_url else "",
    }
