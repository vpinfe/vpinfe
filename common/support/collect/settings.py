"""Selected Settings: an allow-list of what decides how VPinFE behaves on this device.

A setting is here because it is named here. The files that hold secrets are never opened,
and a launcher's free text is left out because it is where a person types names and paths.
"""

from __future__ import annotations

from common.config.config_access import DisplayConfig, cfg_bool, cfg_get
from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config
from common.games import launchers
from common.security import encrypted_connections
from common.support.sections import Section, SectionText

# A launcher field of these types holds a switch or a number, never somebody's words.
PLAIN_TYPES = ("bool", "int", "float", "choice")
# Every address means "reachable from other devices", and the only other answer is this one.
THIS_DEVICE_ONLY = "127.0.0.1"


def collect() -> SectionText:
    config = get_ini_config()
    displays = DisplayConfig.from_config(config)
    lines = [
        f"- **Playfield screen:** {displays.playfield_screen_id_raw}",
        f"- **Playfield orientation:** {displays.playfield_orientation}",
        f"- **Playfield rotation:** {displays.playfield_rotation}",
        f"- **Backglass screen:** {displays.bg_screen_id or 'None'}",
        f"- **Score View screen:** {displays.dmd_screen_id or 'None'}",
        f"- **Cabinet mode:** {_word(displays.cab_mode)}",
        f"- **Log level:** {cfg_get(config, 'logger', 'level', 'debug') or 'debug'}",
        f"- **Log to the terminal:** {_word(cfg_bool(config, 'logger', 'terminal', True))}",
        f"- **Reachable from other devices:** {_word(_reachable(config))}",
        f"- **HTTPS:** {_word(encrypted_connections.serves_https())}",
    ]
    return SectionText.ok(lines + _launcher_lines())


def _word(on: bool) -> str:
    return "On" if on else "Off"


def _reachable(config: ConfigStore) -> bool:
    return cfg_get(config, "network", "http_bind", "0.0.0.0").strip() != THIS_DEVICE_ONLY


def _launcher_lines() -> list[str]:
    lines = []
    for number, launcher in enumerate(launchers.get_launcher_store().launchers(), start=1):
        state = "on" if launcher.enabled else "off"
        plain = [f"{field.key}={launcher.value(field.key)}" for field in launcher.fields()
                 if field.type in PLAIN_TYPES and not field.path]
        lines.append(f"- **Launcher {number}:** {launcher.app or 'unknown app'}, {state}"
                     + (f", {', '.join(plain)}" if plain else ""))
    return lines or ["- **Launchers:** None set up"]


SECTION = Section("settings", "Selected Settings", 60, collect)
