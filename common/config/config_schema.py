"""Every setting VPinFE has, declared once.

One place says what a setting is called, what it accepts, what it defaults to and what
it means, so the config file, the Manager and anything reading us over HTTP describe
it the same way.

`default` is the string the ini writes today, so this file can be checked against the
store it replaces rather than trusted.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from common import i18n
from common.apps.contract import Field
from common.config import input_registry, setting_groups
from common.config.labels import humanize
from common.i18n import t
from common.ids import new_id
from common.values import is_truthy

# What a page press groups by. `sort` takes the groups from whatever the list is ordered
# by - the next letter, year or rating. `count` is a group of a fixed size, which is the
# one grouping that works whatever the order is, because it reads no values at all.
#
# `alpha` and `numeric` were the 2.x names, from when letters were the only groups, and
# they are on disk in configs users already have.
PAGING_GROUPS = ("sort", "count")
PAGING_GROUP_ALIASES = {"alpha": "sort", "numeric": "count"}
PAGING_GROUP_DEFAULT = "sort"


def _input_options() -> tuple[ConfigOption, ...]:
    """`[input]` comes from the action registry, so the two cannot disagree.

    One option per action holding an ordered list of bindings - not a key per input,
    because a binding names its own input and a chord names two.
    """
    out = [
        ConfigOption(
            action.name,
            type="list",
            default=",".join(action.bindings),
            label_key=f"input.{action.name}",
            group=action.group,
            # A list of selectors is what it *is*; typing `pad:0/button:3` is not how
            # anybody wants to say which button they pressed.
            editor=EDITOR_BINDING,
            legacy=tuple(("Input", old) for old in action.legacy),
        )
        for action in input_registry.actions()
    ]
    return in_section(input_registry.SECTION, *(replace(o, applies=APPLIES_WINDOWS) for o in out))


# Sections renamed wholesale, old name to new. A section rename is not the same thing as
# `legacy` below: that records a setting moving between sections, this records every
# setting in a section staying put while the section itself is respelled, so one entry
# here covers keys and aliases alike.
SECTION_RENAMES = {
    'Settings': 'general',
    'Displays': 'displays',
    'Logger': 'logger',
    'Media': 'media',
    'Mobile': 'vpxmobile',
    'Network': 'network',
    'DOF': 'dof',
    'libdmdutil': 'real_dmd',
}


def canonical_section(section: str) -> str:
    """The section this one is called now. Any current name is returned unchanged."""
    return SECTION_RENAMES.get(str(section or ""), str(section or ""))


@dataclass(frozen=True)
class ConfigOption:
    """One setting. `type` says how to read it, not how to store it.

    Declared inside `in_section(...)`, which fills the section in - so an entry names
    its key and nothing else, and the section is stated once for the whole block.
    """

    key: str
    type: str
    default: str
    # Filled in by `in_section`, so an entry never states it.
    section: str = ""
    # Where this setting's words live, for the settings generated from another registry:
    # the `[input]` block is one option per action, and the action already owns the word.
    # Empty means the key follows the section and the key, which is every other setting.
    label_key: str = ""
    choices: tuple[str, ...] = ()
    # Where a choice with no word of its own under `keys` finds one, `{choice}` standing
    # for the value.
    choice_words: str = ""
    # How many rows a `text` setting gets. A paragraph and a one-line name want
    # different controls, and only the setting knows which it is.
    lines: int = 0
    # Spellings a stored file or an old call site may still use. Canonical-plus-alias is
    # how VPinFE already renames ini keys, and how Visual Pinball does it upstream: the
    # file is rewritten to the canonical name, and the old one keeps resolving forever.
    aliases: tuple[str, ...] = ()
    # Whole former locations, section and key, for a setting that moved between sections.
    # `aliases` covers a key that only changed spelling; this covers one that also moved,
    # which is what per-window config did to the fourteen prefix-simulated keys.
    legacy: tuple[tuple[str, str], ...] = ()
    # Runtime state that happens to live in the config file - a last-played pointer, a
    # cache marker. Nobody sets these, so nothing should offer them as settings.
    internal: bool = False
    home: str = ""
    # What this string names on disk, when it names something: `file`, `dir`, or `exe`.
    # Declared rather than inferred from the key - `vpx_bin_path` and `vpx_ini_path` end
    # the same way and want different answers, and a surface that guesses from a name is
    # one rename away from validating the wrong thing.
    path: str = ""
    # A heading to gather this setting under, within its section. Empty means it sits
    # above the first heading, which is where the ones that need no explaining go.
    #
    # Only on a page that draws one section: a page drawing several already heads each
    # of them, and a second level of identical headings would say which is which by
    # position alone.
    group: str = ""
    # A set of values worth offering that this file cannot hold, because it is not known
    # until the install is running - which installs are on the network, say. Declared
    # here rather than matched on the key by a surface, so the same rename that moves the
    # setting moves the control with it. Suggestions, not choices: the list is offered
    # and anything may still be typed, because the thing that produces it can be wrong -
    # all but `SUGGEST_SCREENS`, which only offers what can be picked.
    suggest: str = ""
    # A named tool this setting is edited with, where a control cannot do the job. The
    # type still says what the value *is*, so anything that only reads the setting is
    # unaffected and a surface with no such editor falls back to the control for its
    # type rather than showing nothing.
    editor: str = ""
    # One of `DEFAULTS_ARE`, where a surface reminds a person of the default.
    default_is: str = ""
    # One of `UNITS`: what the number counts.
    unit: str = ""
    # A choice of two or three short names, drawn as buttons rather than a list.
    as_buttons: bool = False
    # The lowest and highest a number may be. Empty is unbounded.
    minimum: int | None = None
    maximum: int | None = None
    # A change to this can put the Manager out of reach of the device making it, so a
    # surface on such a device asks before saving.
    cuts_off_manager: bool = False
    # One of `MOVES`: a folder this setting names holds files that belong to it, so a
    # surface asks, before saving a new folder, whether they go across.
    moves_with: str = ""
    # One of `APPLIES`: when a change reaches what it governs. `now` is a setting read
    # each time it is used, or one whose change is acted on as it is saved; anything
    # else is a claim about where the setting is read, so check that read before
    # declaring it.
    applies: str = "now"

    @property
    def keys(self) -> str:
        """Where this setting's words live in the catalog."""
        return self.label_key or f"config.{self.section}.{self.key}"

    @property
    def label(self) -> str:
        """What to call this setting on screen, or "" where it has no name of its own.

        A setting nobody sets - an install id, a last-played pointer - is never shown,
        so it has no entry and no translator is asked for one.
        """
        said = t(f"{self.keys}.label")
        return "" if said.endswith(".label") else said

    @property
    def choice_labels(self) -> dict[str, str]:
        """What to call each choice on screen, for the choices the catalog names."""
        found = {}
        for value in self.choices:
            key = f"{self.keys}.choice.{value}"
            said = t(key)
            if said == key and self.choice_words:
                key = self.choice_words.format(choice=value)
                said = t(key)
            if said != key:
                found[value] = said
        return found

    @property
    def choice_help(self) -> dict[str, str]:
        """A line saying what each choice does, for the choices the catalog describes."""
        found = {}
        for value in self.choices:
            said = t(f"{self.keys}.choice_help.{value}")
            if not said.endswith(f".choice_help.{value}"):
                found[value] = said
        return found

    @property
    def group_note(self) -> str:
        """The line under this setting's group heading, or "" where the group has none."""
        if not self.group:
            return ""
        said = t(f"config.group.{self.group}.note")
        return "" if said.endswith(".note") else said

    @property
    def blank(self) -> str:
        """What an empty field reads as, or "" where empty needs no words."""
        said = t(f"{self.keys}.blank")
        return "" if said.endswith(".blank") else said

    @property
    def description(self) -> str:
        """One line explaining it, or "" where nobody has written one.

        Prose, so a locale may leave it in English while the label is translated.
        """
        said = t(f"{self.keys}.description")
        return "" if said.endswith(".description") else said

    @property
    def group_label(self) -> str:
        """The heading this setting gathers under, or "" where it has none."""
        return t(f"config.group.{self.group}") if self.group else ""


PATH_KINDS = ("file", "dir", "exe")

# Settings a read answers as set or not set, so a caller that may read the config cannot read
# the key. A write still takes the real value. A surface that draws one holds a true or
# false and not the text, so it draws a box that is never filled in.
SECRET_SETTINGS = frozenset({("dof", "config_tool_api_key")})

# What an `editor` may name. Closed for the same reason `suggest` is: a typo should be a
# setting drawn with its ordinary control, not a surface silently asking for a tool
# nobody registered.
EDITOR_BINDING = "binding"
# A palette is picked by looking at it. The names carry almost nothing on their own -
# "Synthwave" means something only once you have seen it - so the control shows each one.
EDITOR_MANAGER_APPEARANCE = "manager_appearance"
# Shown in Settings, chosen on the Themes page.
EDITOR_FRONTEND_THEME = "frontend_theme"
# The kinds of art the library keeps, which only a running install can say.
EDITOR_THUMBNAIL = "thumbnail"
# A Record or Encode Command, which only the device that runs it can check and show
# VPinFE's own for.
EDITOR_CAPTURE_COMMAND = "capture_command"
# VPinFE's own browser options, picked from the list rather than typed: the names a
# person would have to match are the ones VPinFE starts a window with.
EDITOR_CHROME_LEAVE_OUT = "chrome_leave_out"
# A choice that also has to reach the pages already open, which a plain write does not.
EDITOR_OPEN_LINKS = "open_links"
EDITORS = (EDITOR_BINDING, EDITOR_MANAGER_APPEARANCE, EDITOR_FRONTEND_THEME, EDITOR_THUMBNAIL,
           EDITOR_CAPTURE_COMMAND, EDITOR_CHROME_LEAVE_OUT, EDITOR_OPEN_LINKS)

# What a folder setting's files are, for `ConfigOption.moves_with`.
MOVES_BACKUPS = "backups"
MOVES = (MOVES_BACKUPS,)

# Whose a default is.
DEFAULT_COMMUNITY = "community"
DEFAULT_VPINFE = "vpinfe"
DEFAULTS_ARE = (DEFAULT_COMMUNITY, DEFAULT_VPINFE)
UNIT_SECONDS = "seconds"
UNIT_PORT = "port"
UNITS = (UNIT_SECONDS, UNIT_PORT)

# When a saved change takes effect. `table` is the next table to start, `windows` the
# next time the frontend's windows open, `restart` the next time VPinFE starts.
APPLIES_NOW = "now"
APPLIES_TABLE = "table"
APPLIES_WINDOWS = "windows"
APPLIES_RESTART = "restart"
APPLIES = (APPLIES_NOW, APPLIES_TABLE, APPLIES_WINDOWS, APPLIES_RESTART)

# What a `suggest` may name. Closed, so a typo is a setting with no suggestions rather
# than a surface quietly asking for a list nobody serves.
SUGGEST_LIBRARIES = "libraries"
SUGGEST_THEMES = "themes"
SUGGEST_COLLECTIONS = "collections"
SUGGEST_SCREENS = "screens"
SUGGEST_WHEEL_SETS = "wheel_sets"
SUGGESTIONS = (SUGGEST_LIBRARIES, SUGGEST_THEMES, SUGGEST_COLLECTIONS, SUGGEST_SCREENS,
               SUGGEST_WHEEL_SETS)


def in_section(section: str, *options: ConfigOption) -> tuple[ConfigOption, ...]:
    """Stamp a section onto the options declared under it.

    The section is written once, at the head of its block, rather than repeated on every
    entry - so what you scan down the left is the key, and a block cannot half-belong to
    two sections the way a comment header can drift.
    """
    return tuple(replace(option, section=section) for option in options)


CONFIG_OPTIONS: tuple[ConfigOption, ...] = (
    *in_section(
        "windows.playfield",
        ConfigOption(
            "screen_id",
            applies=APPLIES_WINDOWS,
            type="int",
            default="0",
            legacy=(("Displays", "playfieldscreenid"),),
            suggest=SUGGEST_SCREENS,
        ),
        ConfigOption(
            "orientation",
            applies=APPLIES_WINDOWS,
            type="choice",
            default="landscape",
            choices=("landscape", "portrait"),
            legacy=(("Displays", "playfieldorientation"),),
        ),
        ConfigOption(
            "rotation",
            applies=APPLIES_WINDOWS,
            type="choice",
            default="0",
            choices=("0", "90", "180", "270"),
            legacy=(("Displays", "playfieldrotation"),),
        ),
    ),
    *in_section(
        "windows.backglass",
        ConfigOption(
            "screen_id",
            applies=APPLIES_RESTART,
            type="int",
            default="",
            legacy=(("Displays", "bgscreenid"),),
            suggest=SUGGEST_SCREENS,
        ),
        ConfigOption(
            "override",
            applies=APPLIES_WINDOWS,
            type="string",
            default="",
            legacy=(("Displays", "bgwindowoverride"),),
        ),
    ),
    *in_section(
        "windows.scoreview",
        ConfigOption(
            "screen_id",
            applies=APPLIES_RESTART,
            type="int",
            default="",
            legacy=(("Displays", "dmdscreenid"),),
            suggest=SUGGEST_SCREENS,
        ),
        ConfigOption(
            "override",
            applies=APPLIES_WINDOWS,
            type="string",
            default="",
            legacy=(("Displays", "dmdwindowoverride"),),
        ),
    ),
    *in_section(
        "windows.topper",
        ConfigOption(
            "screen_id",
            applies=APPLIES_RESTART,
            type="int",
            default="",
            suggest=SUGGEST_SCREENS,
        ),
    ),
    # Seven Visual Pinball settings left this section for the launcher that owns them:
    # the binary, its ini, the environment, the log-delete switch and the three override
    # keys. They are not settings an install has, they are fields of a way of running an
    # app, and a second way of running it would have collided with every one of them.
    # The `launchers` list holds them now; `launcher_migration` reads them out of an older
    # file once. What is left here belongs to the install itself.
    *in_section(
        "general",
        # Read only by the ini conversion, which turns it into the first location.
        ConfigOption(
            "game_root_dir",
            type="string",
            path="dir",
            default="",
            aliases=("gamerootdir",),
            internal=True,
        ),
    ),
    # What this library collects. Each list holds what is switched off, so empty means
    # everything and a kind added in a later version arrives switched on. The Manager
    # draws one switch per kind from its own registries, not a text box per list.
    *in_section(
        "library",
        ConfigOption(
            "hidden_media_kinds",
            type="list",
            default="",
            legacy=(("general", "hidden_media_kinds"),),
        ),
        ConfigOption(
            "hidden_asset_kinds",
            type="list",
            default="",
            legacy=(("general", "hidden_asset_kinds"),),
        ),
        ConfigOption("hidden_sources", type="list", default=""),
        ConfigOption("hidden_checks", type="list", default=""),
    ),
    # Extensions switched off, by name, so empty means every installed one runs and one
    # installed later arrives on. The Extensions page draws a switch per extension.
    *in_section("extensions", ConfigOption("switched_off", type="list", default="")),
    # Commands the install runs around itself. `table_commands` below is the same, around
    # a table, and a launcher's own are in the `launchers` list.
    *in_section(
        "commands",
        # Declared first because it governs every command here and in `table_commands`.
        ConfigOption(
            "timeout",
            type="int",
            default="15",
            legacy=(),
        ),
        ConfigOption(
            "on_vpinfe_start",
            applies=APPLIES_RESTART,
            type="text",
            lines=3,
            default="",
            legacy=(),
        ),
        ConfigOption(
            "on_vpinfe_exit",
            type="text",
            lines=3,
            default="",
            legacy=(),
        ),
    ),
    # The same, around a table, and run by whichever launcher plays it.
    *in_section(
        "table_commands",
        ConfigOption(
            "on_start",
            applies=APPLIES_TABLE,
            type="text",
            lines=3,
            default="",
            legacy=(),
        ),
        ConfigOption(
            "on_exit",
            applies=APPLIES_TABLE,
            type="text",
            lines=3,
            default="",
            legacy=(),
        ),
        ConfigOption(
            "start_required",
            applies=APPLIES_TABLE,
            type="bool",
            default="false",
            legacy=(),
        ),
    ),
    # The browser the frontend runs in.
    *in_section(
        "chromium",
        ConfigOption(
            "path",
            applies=APPLIES_WINDOWS,
            type="string",
            path="exe",
            default="",
            group=setting_groups.BROWSER_PROGRAM,
        ),
        ConfigOption(
            "defaults",
            applies=APPLIES_WINDOWS,
            type="bool",
            default="true",
            group=setting_groups.CHROME_OPTIONS,
        ),
        ConfigOption(
            "options_exclude",
            applies=APPLIES_WINDOWS,
            type="text",
            default="",
            editor=EDITOR_CHROME_LEAVE_OUT,
            group=setting_groups.CHROME_OPTIONS,
            legacy=(("general", "chromeoptionsexclude"),),
        ),
        ConfigOption(
            "options",
            applies=APPLIES_WINDOWS,
            # Parsed with shell-style quoting, so a newline is whitespace.
            type="text",
            lines=3,
            default="",
            group=setting_groups.CHROME_OPTIONS,
            legacy=(("general", "chromeoptions"),),
        ),
    ),
    # What the Manager looks like, as against what the frontend shows a player.
    *in_section(
        "manager",
        ConfigOption(
            "appearance",
            type="choice",
            # Not `auto` like `device.language`: this one defers to the operating system
            # rather than to a language the OS names outright, and the two neutral
            # palettes are the only answers an OS preference can give.
            default="synthwave",
            choices=("synthwave", "dark", "light", "system"),
            editor=EDITOR_MANAGER_APPEARANCE,
            legacy=(),
            group=setting_groups.APPEARANCE,
        ),
        ConfigOption(
            "thumbnail",
            type="choice",
            default="wheel",
            choices=("none", "wheel", "backglass", "playfield", "logo"),
            choice_words="media.kind.{choice}.label",
            editor=EDITOR_THUMBNAIL,
            group=setting_groups.THUMBNAILS,
        ),
        ConfigOption(
            "thumbnail_shape",
            type="choice",
            default="auto",
            choices=("auto", "square", "wide"),
            group=setting_groups.THUMBNAILS,
        ),
        ConfigOption(
            "thumbnail_height",
            type="choice",
            default="small",
            choices=("small", "medium", "large"),
            group=setting_groups.THUMBNAILS,
        ),
        ConfigOption(
            "thumbnail_frame",
            type="bool",
            default="true",
            group=setting_groups.THUMBNAILS,
        ),
        ConfigOption(
            "thumb_cache_max_mb",
            type="int",
            default="500",
            group=setting_groups.THUMBNAILS,
            legacy=(("Media", "thumbcachemaxmb"), ("media", "thumb_cache_max_mb")),
        ),
        ConfigOption(
            "dates",
            type="choice",
            default="language",
            choices=("language", "iso", "mm/dd/yyyy", "dd/mm/yyyy", "dd.mm.yyyy",
                     "yyyy/mm/dd"),
            group=setting_groups.DATES_AND_TIMES,
        ),
        ConfigOption(
            "times",
            type="choice",
            default="24h",
            choices=("24h", "12h"),
            group=setting_groups.DATES_AND_TIMES,
        ),
        ConfigOption(
            "relative_dates",
            type="bool",
            default="true",
            group=setting_groups.DATES_AND_TIMES,
        ),
        ConfigOption(
            "units",
            type="choice",
            default="region",
            choices=("region", "cm", "in"),
            group=setting_groups.MEASUREMENTS,
        ),
        ConfigOption(
            "open_links",
            type="choice",
            default="new",
            choices=("new", "reuse"),
            editor=EDITOR_OPEN_LINKS,
            group=setting_groups.LINKS,
        ),
        # Switches on the Manager page, so the schema does not also draw it as a line of text.
        ConfigOption("hidden_overview", type="list", default="", home="vpinfe.manager",
                     group=setting_groups.OVERVIEW),
        # Arranged on the Discovery page, so the schema does not also draw them as text.
        ConfigOption("discovery_shelf_order", type="list", default="", home="library.discovery",
                     group=setting_groups.DISCOVERY),
        ConfigOption("hidden_discovery_shelves", type="list", default="",
                     home="library.discovery", group=setting_groups.DISCOVERY),
    ),
    # Programs on this machine VPinFE shells out to. Each is discovered first, and set
    # here only where discovery finds nothing or picks the wrong one.
    *in_section(
        "tools",
        ConfigOption(
            "rar_path",
            type="string",
            path="exe",
            default="",
            legacy=(("general", "rartoolpath"),),
        ),
        ConfigOption(
            "ffmpeg_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "grim_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "wf_recorder_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "gstreamer_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "wtype_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "ydotool_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "nvidia_smi_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "nvtop_path",
            type="string",
            path="exe",
            default="",
            legacy=(),
        ),
    ),
    # Recording this cabinet's own screens into its tables' media.
    *in_section(
        "capture",
        ConfigOption("length", type="int", default="20", group=setting_groups.TIMING,
                     default_is=DEFAULT_COMMUNITY, unit=UNIT_SECONDS, legacy=()),
        ConfigOption("wait", type="int", default="15", group=setting_groups.TIMING,
                     default_is=DEFAULT_VPINFE, unit=UNIT_SECONDS, legacy=()),
        ConfigOption("picture_at", type="int", default="5", group=setting_groups.TIMING,
                     default_is=DEFAULT_VPINFE, unit=UNIT_SECONDS, legacy=()),
        ConfigOption("fps", type="choice", default="30", choices=("30", "60"),
                     group=setting_groups.VIDEO, default_is=DEFAULT_COMMUNITY, legacy=()),
        ConfigOption("size", type="choice", default="1920", choices=("1920", "screen"),
                     group=setting_groups.VIDEO, default_is=DEFAULT_COMMUNITY, legacy=()),
        ConfigOption("video_codec", type="choice", default="auto",
                     choices=("auto", "h264", "vp9"), group=setting_groups.VIDEO,
                     default_is=DEFAULT_COMMUNITY, legacy=()),
        ConfigOption("quality", type="choice", default="standard",
                     choices=("standard", "high"), group=setting_groups.VIDEO,
                     default_is=DEFAULT_COMMUNITY, legacy=()),
        ConfigOption("playfield_orientation", type="choice", default="bottom_right",
                     choices=("bottom_right", "bottom_left", "upright"),
                     group=setting_groups.VIDEO, default_is=DEFAULT_COMMUNITY, legacy=()),
        ConfigOption("sound", type="bool", default="false", group=setting_groups.AUDIO,
                     default_is=DEFAULT_COMMUNITY, legacy=()),
        ConfigOption("sound_source", type="string", default="",
                     group=setting_groups.AUDIO, legacy=()),
        ConfigOption("record_command", type="string", default="",
                     group=setting_groups.COMMANDS, editor=EDITOR_CAPTURE_COMMAND, legacy=()),
        ConfigOption("encode_command", type="string", default="",
                     group=setting_groups.COMMANDS, editor=EDITOR_CAPTURE_COMMAND, legacy=()),
    ),
    # What this install does to the library on its own, as against `media`, which is what
    # it collects. Drawn in this order; the options with a `home` are drawn on that page.
    *in_section(
        "updates",
        # The choices are minutes between reloads, and the reader takes the value as a number.
        ConfigOption(
            "refresh_minutes",
            applies=APPLIES_RESTART,
            type="choice",
            default="0",
            choices=("0", "60", "360", "1440"),
            legacy=(),
        ),
        ConfigOption(
            "match_new_games",
            type="bool",
            default="true",
            legacy=(),
        ),
        # Separate from `download_spreadsheet`: a machine can want current data for
        # matching without wanting its games rewritten.
        ConfigOption(
            "update_game_details",
            type="bool",
            default="false",
        ),
        ConfigOption(
            "get_media_for_new_games",
            type="bool",
            default="true",
            legacy=(),
        ),
        ConfigOption(
            "update_downloaded_media",
            type="bool",
            default="true",
            legacy=(),
        ),
        ConfigOption(
            "ask_where_new_games_go",
            type="bool",
            default="true",
            home="library.folders",
            legacy=(),
        ),
        ConfigOption(
            "new_games_location",
            type="string",
            default="",
            home="library.folders",
            legacy=(),
        ),
        ConfigOption(
            "download_spreadsheet",
            type="choice",
            default="daily",
            choices=("never", "daily", "weekly", "monthly"),
            home="library.discovery",
        ),
        ConfigOption(
            "show_update_count",
            type="bool",
            default="true",
            home="library.discovery",
        ),
    ),
    # What the frontend shows, as against what it does. Every one is served to themes
    # over the bridge, so renaming a key here changes what a published theme reads.
    *in_section(
        "presentation",
        ConfigOption(
            "cab_mode",
            applies=APPLIES_WINDOWS,
            type="bool",
            default="false",
            aliases=("cabmode",),
            legacy=(("displays", "cabmode"), ("Settings", "cabmode")),
        ),
        ConfigOption(
            "playfield_media_rotation",
            applies=APPLIES_WINDOWS,
            type="choice",
            default="auto",
            choices=("auto", "0", "90", "180", "270"),
            legacy=(("Media", "playfieldmediarotation"),),
        ),
        ConfigOption(
            "playfield_media_priority",
            applies=APPLIES_WINDOWS,
            type="choice",
            default="video",
            choices=("video", "image"),
            legacy=(("Media", "playfieldmediapriority"),),
        ),
        ConfigOption(
            "backglass_media_priority",
            applies=APPLIES_WINDOWS,
            type="choice",
            default="video",
            choices=("video", "image"),
            legacy=(("Media", "bgmediapriority"),),
        ),
        ConfigOption(
            "scoreview_media_priority",
            applies=APPLIES_WINDOWS,
            type="choice",
            default="video",
            choices=("video", "image"),
            legacy=(("Media", "dmdmediapriority"),),
        ),
        ConfigOption(
            "real_dmd_media_priority",
            type="choice",
            default="color",
            choices=("color", "standard"),
            legacy=(("media", "realdmdmediapriority"),),
        ),
    ),
    # How the frontend behaves, as against `themes`, which is what is installed. Both of
    # these were in `general` beside genuinely global settings.
    *in_section(
        "behavior",
        # How far a page press moves the wheel. Not in `input`, which is which button
        # does what: these say what the frontend does when one is pressed.
        ConfigOption(
            "paging_group",
            group=setting_groups.NAVIGATION,
            type="choice",
            default=PAGING_GROUP_DEFAULT,
            choices=PAGING_GROUPS,
            legacy=(("Input", "pagingtype"),),
        ),
        ConfigOption(
            "paging_size",
            group=setting_groups.NAVIGATION,
            type="int",
            default="10",
            default_is=DEFAULT_VPINFE,
            minimum=1,
            maximum=100,
            legacy=(("Input", "pagingsize"),),
        ),
        ConfigOption(
            "startup_collection",
            applies=APPLIES_RESTART,
            group=setting_groups.STARTUP,
            type="string",
            default="",
            # Only a collection that exists can be opened on, but the list is not closed:
            # a cabinet can be set up before the collection it will open on is made.
            suggest=SUGGEST_COLLECTIONS,
            legacy=(("general", "startup_collection"),),
        ),
        ConfigOption(
            "restore_last_table",
            applies=APPLIES_WINDOWS,
            group=setting_groups.STARTUP,
            type="bool",
            default="true",
            # `restorelastgame` was 3.0's and never shipped; 2.x wrote `restorelasttable`,
            # which is also what this restores - a row is a table.
            aliases=("restorelasttable",),
            # As above: 2.x's location first, then the two spellings a 3.0 build wrote -
            # `restore_last_game` into the JSON, and `restorelastgame` into the ini it
            # converted from.
            legacy=(("Settings", "restorelasttable"), ("Settings", "restorelastgame")),
        ),
        ConfigOption(
            "splashscreen",
            applies=APPLIES_WINDOWS,
            group=setting_groups.STARTUP,
            type="bool",
            default="false",
            legacy=(("general", "splashscreen"),),
        ),
        ConfigOption(
            "confirm",
            group=setting_groups.EXIT,
            type="bool",
            default="false",
        ),
        ConfigOption(
            "hide_quit_button",
            group=setting_groups.EXIT,
            type="bool",
            default="false",
            aliases=("MMhideQuitButton",),
            # The 2.x location, then the one 3.0 briefly used. The second is not
            # compatibility for a shipped release - 3.0 has none - it is so an install
            # that already ran a 3.0 build keeps the setting instead of silently
            # defaulting. It can go once no such install is left.
            legacy=(("Settings", "MMhideQuitButton"),),
        ),
        ConfigOption(
            "mute_audio",
            group=setting_groups.AUDIO,
            type="bool",
            default="false",
            legacy=(("general", "muteaudio"),),
        ),
        # Core moves the wheel on its own once nobody has pressed anything. Whether it does
        # so is the person's switch and the theme's `attract.enabled` together; either one
        # off is off. The Manager draws the switch and the idle time as one picker, Start
        # After, so neither is shown on its own.
        ConfigOption(
            "attract_enabled",
            group=setting_groups.ATTRACT,
            type="bool",
            default="true",
        ),
        ConfigOption(
            "attract_idle",
            group=setting_groups.ATTRACT,
            type="int",
            default="60",
            default_is=DEFAULT_VPINFE,
            unit=UNIT_SECONDS,
            minimum=10,
            maximum=3600,
        ),
        ConfigOption(
            "attract_step_time",
            group=setting_groups.ATTRACT,
            type="int",
            default="8",
            default_is=DEFAULT_VPINFE,
            unit=UNIT_SECONDS,
            minimum=2,
            maximum=300,
        ),
        ConfigOption(
            "attract_order",
            group=setting_groups.ATTRACT,
            as_buttons=True,
            type="choice",
            default="random",
            choices=("random", "in_order"),
        ),
        ConfigOption(
            "attract_on_return",
            group=setting_groups.ATTRACT,
            type="choice",
            default="restore",
            choices=("restore", "stay"),
        ),
        ConfigOption(
            "attract_audio",
            group=setting_groups.ATTRACT,
            type="bool",
            default="false",
        ),
    ),
    *in_section(
        "themes",
        # Which one is running, as against the two lists below, which say where themes
        # are found.
        ConfigOption(
            "active",
            applies=APPLIES_RESTART,
            type="string",
            default="Revolution",
            suggest=SUGGEST_THEMES,
            editor=EDITOR_FRONTEND_THEME,
            legacy=(("general", "theme"),),
        ),
        ConfigOption(
            "registries",
            type="list",
            default="https://raw.githubusercontent.com/vpinfe/themes/main/themes.json",
        ),
        ConfigOption(
            "repositories",
            type="list",
            default="",
        ),
        ConfigOption(
            "refresh",
            type="choice",
            default="daily",
            choices=("never", "daily", "weekly", "monthly"),
        ),
    ),
    *in_section(
        "logger",
        ConfigOption(
            "level",
            applies=APPLIES_RESTART,
            type="choice",
            default="debug",
            choices=("debug", "info", "warning", "error"),
        ),
        ConfigOption(
            "terminal",
            applies=APPLIES_RESTART,
            type="bool",
            default="true",
            aliases=("console",),
        ),
    ),
    *in_section(
        "backups",
        ConfigOption(
            "folder",
            type="string",
            path="dir",
            default="",
            moves_with=MOVES_BACKUPS,
        ),
    ),
    *in_section(
        "media",
        ConfigOption(
            "playfield_variant",
            applies=APPLIES_WINDOWS,
            group=setting_groups.PLAYFIELD_ARTWORK,
            type="choice",
            default="table",
            choices=("table", "fss"),
            legacy=(("Media", "playfieldvariant"),),
        ),
        ConfigOption(
            "playfield_resolution",
            group=setting_groups.PLAYFIELD_ARTWORK,
            type="choice",
            default="4k",
            choices=("4k", "1k"),
            legacy=(("Media", "playfieldresolution"),),
        ),
        ConfigOption(
            "playfield_video_resolution",
            group=setting_groups.PLAYFIELD_ARTWORK,
            type="choice",
            default="1k",
            choices=("4k", "1k"),
            legacy=(("Media", "playfieldvideoresolution"),),
        ),
        # What Convert preselects. `auto` holds no preference, so the ask recommends.
        ConfigOption("preferred_picture", type="choice", default="auto",
                     choices=("auto", "png", "webp", "jpg"),
                     group=setting_groups.PREFERRED_FORMATS, legacy=()),
        ConfigOption("preferred_sound", type="choice", default="auto",
                     choices=("auto", "mp3", "ogg"),
                     group=setting_groups.PREFERRED_FORMATS, legacy=()),
        ConfigOption("keep_originals", type="choice", default="auto",
                     choices=("auto", "keep", "discard"),
                     group=setting_groups.PREFERRED_FORMATS, legacy=()),
        # Art keyed by a game's manufacturer rather than owned by one game.
        ConfigOption(
            "manufacturer_logos_dir",
            applies=APPLIES_RESTART,
            group=setting_groups.LOGOS,
            home="library.folders",
            type="string",
            path="dir",
            default="",
            legacy=(),
        ),
        ConfigOption(
            "wheelset",
            applies=APPLIES_WINDOWS,
            group=setting_groups.WHEELS,
            type="string",
            default="",
            suggest=SUGGEST_WHEEL_SETS,
        ),
    ),
    *in_section(
        "device",
        ConfigOption(
            "setup_pending",
            type="bool",
            default="false",
            # Written true only by a fresh install's own first save
            # (common/config/config_store.py). Absent or false reads the same: setup is done.
            internal=True,
        ),
        ConfigOption(
            "name",
            type="string",
            default="",
        ),
        ConfigOption(
            "features",
            type="list",
            default="library,frontend,devices,overview",
            aliases=("roles",),
        ),
        ConfigOption(
            "language",
            applies=APPLIES_RESTART,
            type="choice",
            # `auto` reads the operating system, which is right on a cabinet somebody
            # set up in their own language and never opened this page.
            default="auto",
            choices=("auto",) + i18n.available(),
            legacy=(("general", "language"),),
        ),
        ConfigOption(
            "update_channel",
            type="choice",
            default="stable",
            choices=("stable", "beta"),
        ),
    ),
    *in_section(
        "network",
        ConfigOption(
            "http_port",
            applies=APPLIES_RESTART,
            unit=UNIT_PORT,
            group=setting_groups.CONNECTIONS,
            type="int",
            default="8001",
            aliases=("hub_port", "manager_ui_port", "manageruiport"),
        ),
        ConfigOption(
            "http_bind",
            applies=APPLIES_RESTART,
            cuts_off_manager=True,
            group=setting_groups.CONNECTIONS,
            type="choice",
            default="0.0.0.0",
            choices=("0.0.0.0", "127.0.0.1"),
            aliases=("hub_bind", "manager_ui_bind"),
        ),
        ConfigOption(
            "ws_port",
            applies=APPLIES_RESTART,
            unit=UNIT_PORT,
            group=setting_groups.CONNECTIONS,
            type="int",
            default="8002",
            aliases=("wsport",),
        ),
        ConfigOption(
            "library_url",
            applies=APPLIES_RESTART,
            group=setting_groups.SHARED_LIBRARY,
            type="string",
            default="",
            suggest=SUGGEST_LIBRARIES,
        ),
        ConfigOption(
            "verify_shared_library",
            applies=APPLIES_RESTART,
            group=setting_groups.SHARED_LIBRARY,
            type="bool",
            default="false",
        ),
    ),
    *in_section(
        "dof",
        ConfigOption(
            "enabled",
            group=setting_groups.DOF,
            type="bool",
            default="false",
            aliases=("enabledof",),
        ),
        ConfigOption(
            "config_tool_api_key",
            group=setting_groups.DOF,
            type="string",
            default="",
            aliases=("dofconfigtoolapikey",),
        ),
        ConfigOption(
            "download_config",
            group=setting_groups.DOF,
            type="choice",
            default="weekly",
            choices=("never", "daily", "weekly", "monthly"),
        ),
    ),
    *in_section(
        "real_dmd",
        ConfigOption(
            "enabled",
            type="bool",
            default="false",
        ),
        ConfigOption(
            "pin2dmd_enabled",
            type="bool",
            default="false",
            aliases=("pin2dmdenabled",),
        ),
        ConfigOption(
            "pixelcade_serial_port",
            type="string",
            default="",
            aliases=("pixelcadedevice",),
        ),
        ConfigOption(
            "zedmd_serial_port",
            type="string",
            default="",
            aliases=("zedmddevice",),
        ),
        ConfigOption(
            "zedmd_wifi_address",
            type="string",
            default="",
            aliases=("zedmdwifiaddr",),
        ),
    ),
    *in_section(
        "vpxmobile",
        ConfigOption(
            "chunk_size",
            type="int",
            default="1048576",
            aliases=("chunksize",),
        ),
        ConfigOption(
            "send_masked_config",
            type="bool",
            default="false",
            aliases=("renamemasktodefaultini",),
        ),
        ConfigOption(
            "config_mask",
            type="string",
            default="mobile",
            aliases=("renamemasktodefaultinimask",),
        ),
    ),
    *in_section(
        "vpinplay",
        # The extension owns these. Declared here so a 2.x file converts, and read
        # once: by the handover, and `initials` to make the owner a player.
        # `sync_on_exit` is read by nothing.
        ConfigOption(
            "sync_on_exit",
            type="bool",
            default="false",
            aliases=("synconexit",),
            internal=True,
        ),
        ConfigOption(
            "api_endpoint",
            type="string",
            default="https://api.vpinplay.com:8888",
            aliases=("apiendpoint",),
            internal=True,
        ),
        ConfigOption(
            "user_id",
            type="string",
            default="",
            aliases=("userid",),
            internal=True,
        ),
        ConfigOption(
            "initials",
            type="string",
            default="",
            internal=True,
        ),
        ConfigOption(
            "machine_id",
            type="string",
            default="",
            aliases=("machineid",),
            internal=True,
        ),
    ),
) + _input_options()


def options() -> tuple[ConfigOption, ...]:
    """Every option, in declaration order."""
    return CONFIG_OPTIONS


def settable() -> tuple[ConfigOption, ...]:
    """Everything a person is meant to set - what a UI or a doc should show."""
    return tuple(option for option in CONFIG_OPTIONS if not option.internal)


class ConfigListError(ValueError):
    """An item a list refuses. The message names the list, the field and why."""


# Where an item of a list with a `variant` keeps the fields its kind declares.
VARIANT_SETTINGS = "settings"


@dataclass(frozen=True)
class ListVariant:
    """Items of different kinds. Both functions are called when needed, never stored:
    extensions add kinds after the schema loads. `fields` answers None for a kind this
    install lacks, and that item's `settings` are kept as written."""

    field: str
    kinds: Callable[[], Sequence[str]]
    fields: Callable[[str], Sequence[Field] | None]


@dataclass(frozen=True)
class ConfigList:
    """A top-level key of `vpinfe.json` that holds a list of objects.

    Every item has an `id`, minted when a write brings none. Its other keys are `fields`,
    and a write naming any other key is refused. A file without the key holds `default()`,
    which the store writes at once; a file with it, even empty, is what the person has.
    """

    key: str
    fields: tuple[Field, ...]
    # The Manager page that edits the list.
    home: str = ""
    variant: ListVariant | None = None
    default: Callable[[], list[dict[str, Any]]] = list

    def clean(self, item: Any) -> dict[str, Any]:
        """`item` with every declared field present and typed, or a ConfigListError."""
        if not isinstance(item, dict):
            raise ConfigListError(t("error.config.list.item_object", list=self.key))
        held = {field.key: field for field in self.fields}
        extra = {VARIANT_SETTINGS} if self.variant else set()
        unknown = sorted(str(k) for k in item if k != "id" and k not in held and k not in extra)
        if unknown:
            raise ConfigListError(t("error.config.list.unknown_field", list=self.key,
                                    fields=", ".join(unknown)))
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ConfigListError(t("error.config.list.needs_id", list=self.key))
        out: dict[str, Any] = {"id": item_id}
        for field in self.fields:
            out[field.key] = _typed_field(self.key, field,
                                          item.get(field.key, _blank(field)))
        if self.variant:
            kind = str(out.get(self.variant.field) or "")
            given = item.get(VARIANT_SETTINGS, {})
            if not isinstance(given, dict):
                raise ConfigListError(t("error.config.list.settings_object", list=self.key))
            kind_fields = self.variant.fields(kind) if kind else None
            if kind_fields is None:
                out[VARIANT_SETTINGS] = dict(given)
            else:
                known = {field.key: field for field in kind_fields}
                missing = sorted(str(k) for k in given if k not in known)
                if missing:
                    raise ConfigListError(t("error.config.list.unknown_setting", list=self.key,
                                            settings=", ".join(missing), kind=kind))
                out[VARIANT_SETTINGS] = {
                    field.key: _typed_field(self.key, field,
                                            given.get(field.key, _blank(field)))
                    for field in kind_fields}
        return out


def _blank(field: Field) -> Any:
    """A field's default, in the type it holds."""
    if field.type == "list":
        return [part.strip() for part in field.default.split(",") if part.strip()]
    if field.type == "map":
        return {}
    return field.default


def _typed_field(list_key: str, field: Field, value: Any) -> Any:
    where = f"{list_key}.{field.key}"
    if field.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false", ""):
            return is_truthy(value)
        raise ConfigListError(t("error.config.list.bool", field=where))
    if field.type == "int":
        if value is None or value == "":
            return ""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ConfigListError(t("error.config.list.int", field=where))
        try:
            number = value if isinstance(value, int) else int(float(value))
        except (ValueError, OverflowError) as exc:
            raise ConfigListError(t("error.config.list.int", field=where)) from exc
        if field.minimum is not None and number < field.minimum:
            raise ConfigListError(t("error.config.list.minimum", field=where,
                                    minimum=f"{field.minimum:g}"))
        if field.maximum is not None and number > field.maximum:
            raise ConfigListError(t("error.config.list.maximum", field=where,
                                    maximum=f"{field.maximum:g}"))
        return number
    if field.type == "list":
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigListError(t("error.config.list.text_list", field=where))
        return list(value)
    if field.type == "map":
        if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ConfigListError(t("error.config.list.text_map", field=where))
        return dict(value)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ConfigListError(t("error.config.list.text", field=where))
    if field.type == "choice" and value not in {stored for stored, _ in field.choices}:
        raise ConfigListError(t(
            "error.config.list.choice", field=where,
            choices=", ".join(stored for stored, _ in field.choices)))
    return value


def _launcher_kinds() -> Sequence[str]:
    from common import apps

    return [app.id for app in apps.all_apps()]


def _launcher_fields(kind: str) -> Sequence[Field] | None:
    from common import apps

    return apps.launcher_fields(kind)


def _default_launchers() -> list[dict[str, Any]]:
    """The shipped launcher, under a fresh id."""
    from common import apps

    return [{"id": new_id(), "app": "vpx", "name": apps.app_name("vpx"), "enabled": True,
             "owns_ini": False, "tables": [], "settings": {}}]


CONFIG_LISTS: tuple[ConfigList, ...] = (
    ConfigList(
        "launchers",
        (
            Field("app", type="string"),
            Field("name", type="string"),
            Field("enabled", type="bool", default="true"),
            Field("owns_ini", type="bool", default="false"),
            Field("tables", type="list"),
        ),
        home="launchers",
        variant=ListVariant("app", _launcher_kinds, _launcher_fields),
        default=_default_launchers,
    ),
    ConfigList(
        "locations",
        (
            Field("path", type="string", path="dir"),
            Field("kind", type="choice", default="root",
                  choices=(("root", "Folder of games"), ("game", "One game folder"))),
        ),
        home="library.folders",
    ),
    ConfigList(
        "mobile_devices",
        (
            Field("name", type="string"),
            Field("address", type="string"),
            Field("port", type="int", default="2112", minimum=1, maximum=65535),
        ),
        home="devices",
    ),
)

# The one top-level key that holds an object rather than a list: what the person chose on
# each theme, by the theme's folder name. A theme declares its own options, so what an
# entry may hold is checked against the theme, not against this schema.
THEME_OPTIONS = "theme_options"

_NORMALIZERS: dict[str, Callable[[list[dict[str, Any]]], list[dict[str, Any]]]] = {}
_SEEDS: dict[str, Callable[[Any], None]] = {}


def lists() -> tuple[ConfigList, ...]:
    """Every list, in declaration order."""
    return CONFIG_LISTS


def config_list(key: str) -> ConfigList | None:
    return next((one for one in CONFIG_LISTS if one.key == key), None)


def register_normalizer(key: str,
                        fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
    """`fn` runs on a list's items at every write and answers the list to keep. It must be
    registered before the store is made, so the owning module is imported first."""
    _NORMALIZERS[key] = fn


def normalize(key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fn = _NORMALIZERS.get(key)
    return fn(items) if fn else items


_VALIDATORS: dict[str, Callable[[list[dict[str, Any]], list[dict[str, Any]]], None]] = {}


def register_validator(
        key: str, fn: Callable[[list[dict[str, Any]], list[dict[str, Any]]], None]) -> None:
    """`fn(items, held)` runs on every write, after typing, and raises `ConfigListError` to
    refuse it. `held` is what the store holds now."""
    _VALIDATORS[key] = fn


def validate(key: str, items: list[dict[str, Any]],
             held: list[dict[str, Any]] | None = None) -> None:
    fn = _VALIDATORS.get(key)
    if fn:
        fn(items, held or [])


def register_seed(name: str, fn: Callable[[Any], None]) -> None:
    """`fn(store)` runs once, while a 2.x ini is converted. It reads 2.x keys from
    `store.config`, writes with `store.set_list` and removes what it read."""
    _SEEDS[name] = fn


def seeds() -> tuple[Callable[[Any], None], ...]:
    return tuple(_SEEDS.values())


_WINDOW_SECTION = re.compile(r"windows\.([A-Za-z0-9_-]+)")


def window_name(section: str) -> str:
    """The window a `windows.<name>` section is for, or "" when it is not one."""
    found = _WINDOW_SECTION.fullmatch(str(section or ""))
    return found[1] if found else ""


def window_screen(name: str) -> ConfigOption:
    """The screen a window opens on, for a window a theme declares."""
    return ConfigOption("screen_id", type="int", default="", section=f"windows.{name}",
                        label_key="config.windows.screen_id", suggest=SUGGEST_SCREENS,
                        applies=APPLIES_RESTART)


def default_of(section: str, key: str) -> str:
    """The default the schema gives a setting, for a caller that needs it before any
    config is read."""
    return defaults()[section][key]


def option(section: str, key: str) -> ConfigOption | None:
    section = canonical_section(section)
    for candidate in CONFIG_OPTIONS:
        if candidate.section == section and candidate.key == key:
            return candidate
    if key == "screen_id" and window_name(section):
        return window_screen(window_name(section))
    return None


def canonical(section: str, key: str) -> str:
    """The name this setting is stored under, given any spelling it has ever had."""
    wanted = str(key or "").strip().lower()
    section = canonical_section(section)
    for candidate in CONFIG_OPTIONS:
        if candidate.section != section:
            continue
        if candidate.key.lower() == wanted:
            return candidate.key
        if any(a.lower() == wanted for a in candidate.aliases):
            return candidate.key
    return key


@lru_cache(maxsize=4096)
def locate(section: str, key: str) -> tuple[str, str]:
    """Where a setting lives now, given any section and key it has ever lived at.

    Per-window config moved fourteen settings out of `[Displays]` and `[Media]` into a
    section each, so a caller can be wrong about the section as well as the spelling.
    """
    wanted = (canonical_section(section), str(key or "").strip().lower())
    for candidate in CONFIG_OPTIONS:
        here = [(candidate.section, candidate.key.lower())]
        here += [(candidate.section, a.lower()) for a in candidate.aliases]
        # A former location names the section as it was spelled then, and some of those
        # sections have since been renamed too - so both sides are normalized or a
        # setting that moved out of a renamed section stops resolving.
        here += [(canonical_section(s), k.lower()) for s, k in candidate.legacy]
        if wanted in here:
            return candidate.section, candidate.key
    return canonical_section(section), key


def spellings(section: str, key: str) -> tuple[str, ...]:
    """Every name this setting has gone by, canonical first.

    A reader tries them in order because a parser is not always one the store filled:
    a caller can hand-build one, and a stored file written by an older build has the
    old names in it until the store rewrites them.
    """
    wanted = str(key or "").strip().lower()
    section = canonical_section(section)
    for candidate in CONFIG_OPTIONS:
        if candidate.section != section:
            continue
        names = (candidate.key, *candidate.aliases)
        if any(n.lower() == wanted for n in names):
            return names
    return (key,)


def by_key(key: str) -> ConfigOption | None:
    """Look up without knowing the section.

    Needed because configparser lowercases option names on read, so a caller holding a
    key off the file has neither the section nor the original casing.

    A key is not unique across sections - `screen_id` and `media_priority` are each in
    several - so this answers with the first declared. Pass the section wherever you have
    it; `label_for` says the same.
    """
    wanted = str(key or "").strip().lower()
    for candidate in CONFIG_OPTIONS:
        if candidate.key.lower() == wanted:
            return candidate
        if any(a.lower() == wanted for a in candidate.aliases):
            return candidate
    return None


def label_for(key: str, section: str = "") -> str:
    """What to call a setting on screen. Falls back to a readable form of the key.

    Pass the section when you have it: since each window got a section of its own,
    `screen_id` exists three times with a different label each, and a key-only lookup
    cannot tell a backglass monitor from a playfield one.
    """
    entry = option(section, key) if section else None
    if entry is None:
        entry = by_key(key)
    if entry is not None and entry.label:
        return entry.label
    return humanize(key)


def description_for(key: str, section: str = "") -> str:
    """One line explaining a setting, or "" when nobody has written one yet."""
    entry = (option(section, key) if section else None) or by_key(key)
    return entry.description if entry is not None else ""


def defaults() -> dict[str, dict[str, str]]:
    """The nested section/key/value shape the config store fills a new file from."""
    out: dict[str, dict[str, str]] = {}
    for entry in CONFIG_OPTIONS:
        out.setdefault(entry.section, {})[entry.key] = entry.default
    return out
