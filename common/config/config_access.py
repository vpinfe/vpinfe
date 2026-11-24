"""Reading and writing a setting, under any spelling it has ever had.

Everything goes through `cfg_get` and friends rather than the parser: keys and
sections were renamed at schema 2, and this is the one place that knows the old
names still resolve. The typed views at the bottom group the settings a subsystem
reads together, so a caller asks for what it needs instead of a section name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.config import config_schema
from common.values import is_truthy

# A ConfigStore, the ConfigParser inside one, or nothing at all - a caller with no
# config yet passes None and every reader below falls through to its fallback. Named
# rather than left as a bare Any so a signature elsewhere can say it takes any of them;
# it is Any because coping with whatever arrives is the point of this file.
type ConfigSource = Any


def _parser(source: ConfigSource) -> Any:
    return getattr(source, "config", source)


def _candidates(section: str, key: str) -> list[tuple[str, str]]:
    """Every place this setting could be, most current first.

    Where the schema says it lives now comes first, then every spelling it has had
    there, then whatever the caller actually asked for - because a parser built by hand
    in a test holds only the names it was given, not the ones we migrated to.
    """
    here, name = config_schema.locate(section, key)
    out = [(here, spelling) for spelling in config_schema.spellings(here, name)]
    # Then wherever it used to live. A file the store has migrated holds the current
    # location, but a parser built by hand - or one an older build wrote - does not.
    entry = config_schema.option(here, name)
    for pair in (entry.legacy if entry else ()):
        if pair not in out:
            out.append(pair)
    asked = config_schema.spellings(section, key)
    for pair in ((section, key), *((section, spelling) for spelling in asked)):
        if pair not in out:
            out.append(pair)
    return out


def _has(parser: Any, section: str, key: str) -> bool:
    try:
        return bool(parser.has_option(section, key))
    except Exception:
        try:
            return key in parser[section]
        except Exception:
            return False


def cfg_has(source: ConfigSource, section: str, key: str) -> bool:
    """Whether a setting is written down at all, under any spelling it has ever had.

    Apart from `cfg_get` returning "": a value can be deliberately blank, and for a list
    that is the difference between "I have not said" and "none of them".
    """
    parser = _parser(source)
    return any(_has(parser, candidate_section, candidate_key)
               for candidate_section, candidate_key in _candidates(section, key))


def cfg_get(source: ConfigSource, section: str, key: str, fallback: str = "") -> str:
    """Read a setting under any spelling it has ever had.

    Keys moved to snake_case at schema 2 and the old ones stay aliases, so a caller
    written against `gamerootdir` keeps working against a file that says
    `game_root_dir`.
    """
    parser = _parser(source)
    # Each candidate is its own (section, key) pair - a key that moved sections has both
    # spellings in the list. Named apart from the arguments because reusing `section`
    # for the loop meant the parameter was gone after the first pass, and only the fact
    # that _candidates had already read it kept that working.
    for candidate_section, candidate_key in _candidates(section, key):
        try:
            if parser.has_option(candidate_section, candidate_key):
                return str(parser.get(candidate_section, candidate_key, fallback=fallback))
        except Exception:
            pass
        try:
            values = parser[candidate_section]
            if candidate_key in values:
                return str(values.get(candidate_key, fallback))
        except Exception:
            pass
    return fallback


def cfg_options(source: ConfigSource, section: str) -> list[str]:
    """Every key present in a section, or [] when it has none."""
    parser = _parser(source)
    try:
        return list(parser.options(section))
    except Exception:
        try:
            return list(parser[section].keys())
        except Exception:
            return []


def cfg_bool(source: ConfigSource, section: str, key: str, fallback: bool = False) -> bool:
    parser = _parser(source)
    section, key = next(((sec, name) for sec, name in _candidates(section, key)
                         if _has(parser, sec, name)), (section, key))
    try:
        return bool(parser.getboolean(section, key, fallback=fallback))
    except Exception:
        return is_truthy(
            cfg_get(parser, section, key, "true" if fallback else "false"), default=fallback)


def cfg_int(source: ConfigSource, section: str, key: str, fallback: int = 0) -> int:
    raw = cfg_get(source, section, key, str(fallback)).strip()
    if raw == "":
        return fallback
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return fallback


def cfg_list(source: ConfigSource, section: str, key: str) -> list[str]:
    """A list setting. JSON holds a real array; the parser above it holds them joined."""
    return [part.strip() for part in cfg_get(source, section, key).split(",") if part.strip()]


def cfg_set(source: ConfigSource, section: str, key: str, value: Any) -> None:
    """Write a setting to wherever it lives now, under whatever name the caller knows.

    Writes resolve through the schema as reads do. Without that, a caller naming a
    section that has since been renamed writes a second copy under the old name - or
    raises, where the section has gone.
    """
    parser = _parser(source)
    section, key = config_schema.locate(section, key)
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, "true" if value is True else "false" if value is False else str(value))


@dataclass(frozen=True)
class SettingsConfig:
    """What the install itself is set to.

    Visual Pinball's seven settings are not here any more: a binary, its ini, the
    environment and the three overrides belong to a launcher, and `common/games/launchers.py`
    holds them. Leaving them would have meant a second way of running Visual Pinball
    colliding with every one of these names.
    """

    rar_tool_path: str = ""
    theme: str = "Revolution"
    startup_collection: str = ""
    mute_audio: bool = False
    splashscreen: bool = False
    chrome_options: str = ""
    default_chrome_options: bool = True
    hide_quit_button: bool = False
    restore_last_table: bool = True
    manufacturer_logos_dir: str = ""

    @classmethod
    def from_config(cls, source: Any) -> SettingsConfig:
        theme = cfg_get(source, "Settings", "theme", "Revolution").strip() or "Revolution"
        return cls(
            rar_tool_path=cfg_get(source, "Settings", "rartoolpath", "").strip(),
            theme=theme,
            startup_collection=cfg_get(source, "behavior", "startup_collection", "").strip(),
            mute_audio=cfg_bool(source, "Settings", "muteaudio", False),
            splashscreen=cfg_bool(source, "Settings", "splashscreen", False),
            chrome_options=cfg_get(source, "Settings", "chromeoptions", ""),
            default_chrome_options=cfg_bool(source, "chromium", "defaults", True),
            hide_quit_button=cfg_bool(source, "Settings", "MMhideQuitButton", False),
            restore_last_table=cfg_bool(source, "Settings", "restorelasttable", True),
            # Canonical section, not the "Settings" alias the rest of these carry: these
            # are new in 3.0 and never lived anywhere else.
            manufacturer_logos_dir=cfg_get(source, "media", "manufacturer_logos_dir",
                                           "").strip(),
        )


@dataclass(frozen=True)
class MediaConfig:
    playfield_variant: str = "table"
    playfield_resolution: str = "4k"
    playfield_video_resolution: str = "1k"
    playfield_media_priority: str = "video"
    bg_media_priority: str = "video"
    dmd_media_priority: str = "video"
    realdmd_media_priority: str = "color"
    playfield_media_rotation: str = "auto"
    wheelset: str = ""

    @classmethod
    def from_config(cls, source: Any) -> MediaConfig:
        return cls(
            wheelset=cfg_get(source, "Media", "wheelset", "").strip(),
            playfield_variant=(cfg_get(source, "Media", "playfieldvariant", "table").strip().lower()
                            or "table"),
            playfield_resolution=(
                cfg_get(source, "Media", "playfieldresolution", "4k").strip().lower() or "4k"),
            playfield_video_resolution=(
                cfg_get(source, "Media", "playfieldvideoresolution", "1k").strip().lower()
                or "1k"),
            playfield_media_priority=_media_priority(
                source, "playfieldmediapriority", ("image", "video"), "video"),
            bg_media_priority=_media_priority(
                source, "bgmediapriority", ("image", "video"), "video"),
            dmd_media_priority=_media_priority(
                source, "dmdmediapriority", ("image", "video"), "video"),
            realdmd_media_priority=_media_priority(
                source, "realdmdmediapriority", ("standard", "color"), "color"),
            playfield_media_rotation=_media_rotation(
                cfg_get(source, "Media", "playfieldmediarotation", "auto")),
        )

    def priority_payload(self) -> dict[str, str]:
        return {
            # Canonical media kind keys, because core looks these up with a normalized
            # name. The ini keys they come from are frozen and do not match either.
            "playfield": self.playfield_media_priority,
            "backglass": self.bg_media_priority,
            "scoreview": self.dmd_media_priority,
            "real_dmd": self.realdmd_media_priority,
            # A contract 1 theme reading vpin.mediaPriorities.bg directly still finds it.
            "bg": self.bg_media_priority,
            "dmd": self.dmd_media_priority,
        }


def _media_priority(source: Any, key: str, allowed: tuple[str, ...], fallback: str) -> str:
    value = cfg_get(source, "Media", key, fallback).strip().lower()
    aliases = {
        "png": "image",
        "mp4": "video",
        "realdmd": "standard",
        "realdmd.png": "standard",
        "realdmd-color": "color",
        "realdmd-color.png": "color",
        "colour": "color",
        "colorized": "color",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in allowed else fallback


@dataclass(frozen=True)
class NetworkConfig:
    ws_port: int = int(config_schema.default_of("network", "ws_port"))
    http_port: int = int(config_schema.default_of("network", "http_port"))
    # The window channel has no bind setting at all until it has authentication: it
    # reaches shutdown_system.
    http_bind: str = config_schema.default_of("network", "http_bind")
    # Empty means this install holds its own library. Set, it names the install this one
    # reads its catalog from - the one setting that makes this process read somebody
    # else's.
    library_url: str = ""
    # Off by default: it costs a walk of the local library at startup, and an install
    # that is working does not need to be told so.
    verify_shared_library: bool = False

    @classmethod
    def from_config(cls, source: Any) -> NetworkConfig:
        return cls(
            ws_port=cfg_int(source, "Network", "wsport", cls.ws_port),
            # Asked for by the oldest spelling, as the sibling ports are: resolution
            # walks forward to `network.http_port`, and a parser built by hand with the
            # pre-PAR-44 section still answers.
            http_port=cfg_int(source, "Network", "manageruiport", cls.http_port),
            http_bind=(cfg_get(source, "network", "http_bind", cls.http_bind).strip()
                       or cls.http_bind),
            library_url=cfg_get(source, "network", "library_url", "").strip(),
            verify_shared_library=cfg_bool(source, "network", "verify_shared_library",
                                           False),
        )


def _media_rotation(value: Any) -> str:
    """How far to turn playfield art, or `auto` to measure it.

    Measuring is the default because there is no reliable authoring convention: a library
    may be landscape desktop captures, portrait FSS renders, or a mix. This states the
    turn for what measuring cannot see - art that is upside down, or art meant to be
    letterboxed rather than turned.
    """
    name = str(value or "").strip().lower()
    if name == "auto":
        return "auto"
    try:
        turned = int(name) % 360
    except ValueError:
        return "auto"
    return str(turned) if turned in QUARTER_TURNS else "auto"


ORIENTATIONS = ("landscape", "portrait")
QUARTER_TURNS = (0, 90, 180, 270)


def _orientation(value: Any) -> str:
    """The playfield's mounting, or landscape when the ini says something else.

    `[Media] playfieldvariant` has always normalized this way; `[Displays]` never did, so
    a capitalized `Portrait` reached themes that compare it exactly and meant landscape.
    """
    name = str(value or "").strip().lower()
    return name if name in ORIENTATIONS else "landscape"


def _quarter_turn(degrees: int) -> int:
    """Rotation as one of four turns. Anything else would leave a theme guessing."""
    turned = int(degrees) % 360
    return turned if turned in QUARTER_TURNS else 0


def _extra_screen_ids(source: Any) -> dict:
    """Monitors for windows beyond the three that have a field of their own.

    A theme can declare windows VPinFE has never heard of, so these cannot be named in
    advance. Both shapes are read: `windows.<name>.screen_id`, which is where one goes
    now, and `<name>screenid` in `[Displays]`, which is where one written before
    schema 3 still is.
    """
    known = {"bgscreenid", "dmdscreenid", "playfieldscreenid", "tablescreenid"}
    found = {}
    parser = _parser(source)
    try:
        sections = [s for s in parser.sections() if s.startswith("windows.")]
    except Exception:
        sections = []
    for section in sections:
        window = section.split(".", 1)[1]
        if window in {"playfield", "backglass", "scoreview"}:
            continue
        value = cfg_get(source, section, "screen_id").strip()
        if value:
            found[f"{window}screenid"] = value
    for key in cfg_options(source, "Displays"):
        name = str(key).strip().lower()
        if name.endswith("screenid") and name not in known:
            found[name] = cfg_get(source, "Displays", name, "").strip()
    return found


@dataclass(frozen=True)
class DisplayConfig:
    playfield_screen_id: int = 0
    playfield_screen_id_raw: str = "0"
    bg_screen_id: str = ""
    dmd_screen_id: str = ""
    playfield_orientation: str = "landscape"
    playfield_rotation: int = 0
    cab_mode: bool = False
    # Monitors for windows beyond the three VPinFE has always opened.
    extra_screen_ids: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, source: Any) -> DisplayConfig:
        playfield_screen_id_raw = cfg_get(source, "Displays", "playfieldscreenid", "0").strip()
        return cls(
            playfield_screen_id=cfg_int(source, "Displays", "playfieldscreenid", 0),
            playfield_screen_id_raw=playfield_screen_id_raw,
            bg_screen_id=cfg_get(source, "Displays", "bgscreenid", "").strip(),
            dmd_screen_id=cfg_get(source, "Displays", "dmdscreenid", "").strip(),
            playfield_orientation=_orientation(
                cfg_get(source, "Displays", "playfieldorientation", "landscape")),
            playfield_rotation=_quarter_turn(
                cfg_int(source, "Displays", "playfieldrotation", 0)),
            cab_mode=cfg_bool(source, "presentation", "cab_mode", False),
            extra_screen_ids=_extra_screen_ids(source),
        )

    def window_screen_id(self, config_key: str) -> str:
        """The monitor for a `<window>screenid` token, or "" when the user set none.

        Keyed by token rather than by location, because a theme can declare a window
        VPinFE has never heard of and there is nothing here that could know its name.
        The three VPinFE always opens are read from their own fields; anything else
        comes from whichever shape the file uses.
        """
        known = {
            "backglassscreenid": self.bg_screen_id,
            "scoreviewscreenid": self.dmd_screen_id,
            "playfieldscreenid": self.playfield_screen_id_raw,
        }
        if config_key in known:
            return known[config_key]
        return str(self.extra_screen_ids.get(config_key, "") or "").strip()

