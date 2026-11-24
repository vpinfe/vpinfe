"""Where Visual Pinball X's settings are found, and which of them are drawn first.

The areas are the pages of the program's own settings menu, so a setting somebody changed
at the table is where they already look for it. Each area names every setting it holds,
and a few of them as its curated rows; the rest are found by search.
"""

from __future__ import annotations

from collections.abc import Mapping

from common.apps.contract import Heading, Pair, Switched

from . import bindings, build
from .plugins import Plugin

DISPLAYS = "displays"
INPUT = "input"
SOUND = "sound"
GRAPHICS = "graphics"
PLUGINS = "plugins"
POINT_OF_VIEW = "point_of_view"
REST = "more"

# In the order the rail draws them.
AREAS = (DISPLAYS, INPUT, SOUND, GRAPHICS, PLUGINS)


def _window(key: str, prefix: str, parts: tuple[str, ...]) -> Heading:
    video_mode = (f"{prefix}FSWidth", f"{prefix}FSHeight")
    position = (f"{prefix}WndX", f"{prefix}WndY")
    size = (f"{prefix}Width", f"{prefix}Height")
    return Heading(key, (*(f"{prefix}{part}" for part in parts), *video_mode, *position,
                         *size),
                   pairs=(Pair("video_mode", video_mode), Pair("position", position),
                          Pair("size", size)),
                   switched=(Switched(f"{prefix}FullScreen", video_mode),
                             Switched(f"{prefix}FullScreen", (*position, *size), on=False)))


CURATED: dict[str, tuple[Heading, ...]] = {
    DISPLAYS: (
        # The playfield always shows, so it has no output mode.
        _window("playfield", "Player.Playfield", ("Display", "FullScreen")),
        _window("backglass", "Backglass.Backglass", ("Output", "Display", "FullScreen")),
        _window("scoreview", "ScoreView.ScoreView", ("Output", "Display", "FullScreen")),
        _window("topper", "Topper.Topper", ("Output", "Display", "FullScreen")),
        Heading("cabinet", ("Player.BGSet", "Player.CabinetAutofitMode",
                            "Player.CabinetAutofitPos", "Player.ScreenWidth",
                            "Player.ScreenHeight", "Player.ScreenInclination")),
    ),
    INPUT: tuple(Heading(heading, tuple(one.qualified for one in bindings.ACTIONS
                                        if one.heading == heading))
                 for heading in bindings.HEADINGS),
    SOUND: (
        Heading("backglass", ("Player.PlayMusic", "Player.MusicVolume",
                              "Player.SoundDeviceBG")),
        Heading("playfield", ("Player.PlaySound", "Player.SoundVolume",
                              "Player.SoundDevice", "Player.Sound3D")),
    ),
    GRAPHICS: (
        Heading("", ("Player.SyncMode", "Player.MaxFramerate", "Player.AAFactor",
                     "Player.MSAASamples", "Player.FXAA", "Player.Sharpen",
                     "Player.PFReflection", "Player.ShowFPS")),
    ),
    REST: (
        _window("vr_preview", "PlayerVR.Preview", ("Display", "FullScreen")),
    ),
}


_ARTS =(("Backglass", "backglass_dmd_position", "backglass_dmd_size"),
         ("ScoreView", "scoreview_dmd_position", "scoreview_dmd_size"))


def _overlays(plugin: str) -> tuple[Switched, ...]:
    found: list[Switched] = []
    for art, _, _ in _ARTS:
        prefix = f"Plugin.{plugin}.{art}DMD"
        box = tuple(f"{prefix}{part}" for part in "XYWH")
        found += (Switched(f"{prefix}Overlay", (f"{prefix}AutoPos", *box)),
                  Switched(f"{prefix}AutoPos", box, on=False))
    return tuple(found)


def _dmd(plugin: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(key for one in _overlays(plugin)
                               for key in (one.enabled_by, *one.keys)))


def _dmd_pairs(plugin: str) -> tuple[Pair, ...]:
    found: list[Pair] = []
    for art, position, size in _ARTS:
        prefix = f"Plugin.{plugin}.{art}DMD"
        found += (Pair(position, (f"{prefix}X", f"{prefix}Y")),
                  Pair(size, (f"{prefix}W", f"{prefix}H")))
    return tuple(found)


# The plugins the catalog has words for, and each one's rows after its Enable. A plugin
# not named here shows Enable alone.
PLUGIN_ROWS: dict[str, tuple[str, ...]] = {
    "AlphaDMD": (),
    "DOF": (),
    "FlexDMD": (),
    "RemoteControl": (),
    "WMP": (),
    "B2S": ("Plugin.B2S.ShowGrill", *_dmd("B2S")),
    "B2SLegacy": ("Plugin.B2SLegacy.B2SHideGrill", "Plugin.B2SLegacy.B2SHideB2SDMD",
                  "Plugin.B2SLegacy.B2SHideDMD", *_dmd("B2SLegacy")),
    "ScoreView": ("Plugin.ScoreView.LayoutFolder",),
    "Nixie": ("Plugin.Nixie.LayoutFolder",),
    "PinMAME": ("Plugin.PinMAME.Sound", "Plugin.PinMAME.PinMAMEPath"),
    "AltSound": ("Plugin.AltSound.Folder",),
    "Serum": ("Plugin.Serum.SerumPath",),
    "VNI": ("Plugin.VNI.VniPath",),
    "PUP": ("Plugin.PUP.PUPFolder", "Plugin.PUP.MainVol"),
    "DMDUtil": ("Plugin.DMDUtil.ZeDMD", "Plugin.DMDUtil.Pixelcade",
                "Plugin.DMDUtil.PIN2DMD", "Plugin.DMDUtil.DMDServer"),
    "UpscaleDMD": ("Plugin.UpscaleDMD.UpscaleMode",),
}

# Settings that name a folder, so the manager offers Browse beside the typed path.
FOLDERS = frozenset({
    "Plugin.ScoreView.LayoutFolder", "Plugin.Nixie.LayoutFolder",
    "Plugin.PinMAME.PinMAMEPath", "Plugin.AltSound.Folder",
    "Plugin.Serum.SerumPath", "Plugin.VNI.VniPath", "Plugin.PUP.PUPFolder",
})

# Settings that name an image file, which Visual Pinball decodes from disk.
IMAGES = frozenset({
    "Player.BallImage", "Player.DecalImage", "Plugin.PUP.BGFrameOverlay",
    "Plugin.PUP.SVFrameOverlay", "Plugin.PUP.TopperFrameOverlay",
})
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

# Plugins for writing plugins. In the rest.
NOT_PLUGINS = frozenset({"HelloScript", "HelloWorld", "Inspector"})

# Plugins a table gets only one of while both are on.
RIVALS: dict[str, tuple[str, ...]] = {"B2S": ("B2SLegacy",), "B2SLegacy": ("B2S",)}

PLUGIN_PAIRS: dict[str, tuple[Pair, ...]] = {plugin: _dmd_pairs(plugin)
                                             for plugin in ("B2S", "B2SLegacy")}

PLUGIN_SWITCHED: dict[str, tuple[Switched, ...]] = {plugin: _overlays(plugin)
                                                    for plugin in ("B2S", "B2SLegacy")}

# Rows a file of the plugin's kinds draws only when the file holds two versions.
PLUGIN_VERSION_ROWS: dict[str, tuple[str, ...]] = {
    "B2SLegacy": ("Plugin.B2SLegacy.B2SDualMode",),
    "B2S": ("Plugin.B2S.DualMode",)}

# Settings VPX declares as a number that are a choice between a few: the stored values,
# with the words for them in the catalog as `field.<key>.choice.<value>.label`.
CHOICES: dict[str, tuple[str, ...]] = {"Plugin.B2SLegacy.B2SDualMode": ("1", "2"),
                                       "Plugin.B2S.DualMode": ("1", "2")}

# The asset kinds a plugin's settings are about.
PLUGIN_KINDS: dict[str, tuple[str, ...]] = {"B2S": ("backglass",),
                                            "B2SLegacy": ("backglass",)}

# Keys of `[Player]` by the page of the program's menu that holds them. The view mode and
# autofit sit on its Graphic page, and are here because they are about the screen.
_DISPLAYS = frozenset({
    "Player.PlayfieldDisplay", "Player.PlayfieldFullScreen", "Player.PlayfieldWndX",
    "Player.PlayfieldWndY", "Player.PlayfieldWidth", "Player.PlayfieldHeight",
    "Player.PlayfieldFSWidth", "Player.PlayfieldFSHeight", "Player.PlayfieldRefreshRate",
    "Player.PlayfieldColorDepth",
    "Player.BGSet", "Player.CabinetAutofitMode", "Player.CabinetAutofitPos",
    "Player.ScreenWidth", "Player.ScreenHeight", "Player.ScreenInclination",
    "Player.LockbarWidth", "Player.LockbarHeight",
    "Player.ScreenPlayerX", "Player.ScreenPlayerY", "Player.ScreenPlayerZ",
})
_SOUND = frozenset({
    "Player.PlayMusic", "Player.MusicVolume", "Player.SoundDeviceBG",
    "Player.PlaySound", "Player.SoundVolume", "Player.SoundDevice", "Player.Sound3D",
})
_GRAPHICS = frozenset({
    "Player.SyncMode", "Player.MaxFramerate", "Player.MaxPrerenderedFrames",
    "Player.VisualLatencyCorrection", "Player.GfxBackend", "Player.ShowFPS",
    "Player.AAFactor", "Player.MSAASamples", "Player.FXAA", "Player.Sharpen",
    "Player.SSRefl", "Player.PFReflection", "Player.DisableAO", "Player.DynamicAO",
    "Player.MaxTexDimension", "Player.AlphaRampAccuracy", "Player.CompressTextures",
    "Player.HDRDisableToneMapper", "Player.HDRGlobalExposure",
    "Player.ForceAnisotropicFiltering", "Player.ForceBloomOff",
    "Player.ForceMotionBlurOff", "Player.UseNVidiaAPI",
    "Player.SoftwareVertexProcessing", "Player.BallAntiStretch",
    "Player.DisableLightingForBalls", "Player.BallTrail", "Player.BallTrailStrength",
})
_WINDOW_SECTIONS = frozenset({"Backglass", "ScoreView", "Topper"})
POINT_OF_VIEW_KEYS = "TableOverride.View"
TABLE_OPTIONS = "table_options"
TABLE_OPTION_KEYS = "TableOption."

# Each view a table keeps a camera for, and how its keys spell it.
VIEWS = (("desktop", "DT"), ("fss", "FSS"), ("cabinet", "Cab"))


def view_mode(code: str) -> str:
    return f"{POINT_OF_VIEW_KEYS}{code}Mode"


VIEW_MODES = frozenset(view_mode(code) for _view, code in VIEWS)


def view_headings(offered: set[str]) -> tuple[Heading, ...]:
    """One heading per view, its view mode first, then its camera."""
    found = []
    for view, code in VIEWS:
        mode = view_mode(code)
        camera = sorted(key for key in offered
                        if key.startswith(f"{POINT_OF_VIEW_KEYS}{code}") and key != mode)
        keys = ((mode,) if mode in offered else ()) + tuple(camera)
        if keys:
            found.append(Heading(view, keys))
    return tuple(found)


def area_of(qualified: str) -> str:
    """The area a setting belongs to, whether or not it is one of its curated rows."""
    section = qualified.rsplit(".", 1)[0]
    if section in _WINDOW_SECTIONS or qualified in _DISPLAYS:
        return DISPLAYS
    if bindings.is_action(qualified):
        return INPUT
    if qualified in _SOUND:
        return SOUND
    if qualified in _GRAPHICS:
        return GRAPHICS
    if section.startswith("Plugin.") and plugin_of(qualified) not in NOT_PLUGINS:
        return PLUGINS
    if qualified.startswith(POINT_OF_VIEW_KEYS):
        return POINT_OF_VIEW
    return REST


def plugin_of(qualified: str) -> str:
    """`PinMAME` for `Plugin.PinMAME.Sound`; "" for a key that is not a plugin's."""
    parts = qualified.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "Plugin" else ""


def is_plugin_switch(qualified: str) -> bool:
    """`Plugin.PinMAME.Enable`: the switch the program gives every plugin."""
    plugin = plugin_of(qualified)
    return bool(plugin) and qualified == f"Plugin.{plugin}.Enable"


# A B2S heading's note once its DMD rows are gone, because the backglass file holds them.
DMD_IN_FILE = "dmd_in_file"


def plugin_headings(offered: set[str], installed: Mapping[str, Plugin] | None = None,
                    rev: int | None = None) -> tuple[Heading, ...]:
    """One heading per plugin the file has, its Enable first and the switch for the
    rest, in the program's words for it where the program has them."""
    found: list[Heading] = []
    seen: set[str] = set()
    for qualified in offered:
        plugin = plugin_of(qualified)
        if (not plugin or plugin in seen or area_of(qualified) != PLUGINS
                or (installed is not None and plugin not in installed)):
            continue
        seen.add(plugin)
        enable = f"Plugin.{plugin}.Enable"
        keys = tuple(key for key in (enable, *PLUGIN_ROWS.get(plugin, ()))
                     if key in offered)
        if keys:
            said = (installed or {}).get(plugin, Plugin(plugin))
            switched = keys[0] == enable
            found.append(Heading(
                plugin, keys, enabled_by=enable if switched else "",
                label=said.name if said.name != plugin else "",
                description=said.description if said.description != said.name else "",
                rivals=tuple(f"Plugin.{other}.Enable" for other in RIVALS.get(plugin, ()))
                if switched else (),
                pairs=tuple(pair for pair in PLUGIN_PAIRS.get(plugin, ())
                            if set(pair.keys) <= set(keys)),
                switched=kept_switched(PLUGIN_SWITCHED.get(plugin, ()), set(keys)),
                kinds=PLUGIN_KINDS.get(plugin, ()),
                versions=tuple(key for key in PLUGIN_VERSION_ROWS.get(plugin, ())
                               if key in offered),
                variant=DMD_IN_FILE if plugin == "B2S" and rev is not None
                and not build.declares("Plugin.B2S.BackglassDMDX", rev) else ""))
    return tuple(sorted(found, key=lambda one: plugin_order(one.key, installed)))


def kept_switched(switched: tuple[Switched, ...], offered: set[str]) -> tuple[Switched, ...]:
    """Each switch offered, with the rows it gates that are."""
    kept = (Switched(one.enabled_by, tuple(key for key in one.keys if key in offered), one.on)
            for one in switched if one.enabled_by in offered)
    return tuple(one for one in kept if one.keys)


def plugin_order(plugin: str, installed: Mapping[str, Plugin] | None) -> str:
    """Where a plugin sorts: by the program's name for it, else by its id."""
    said = (installed or {}).get(plugin)
    return ((said.name if said else "") or plugin).lower()


_CURATED_KEYS = frozenset({key for area in AREAS for heading in CURATED.get(area, ())
                           for key in heading.keys}
                          | {key for keys in PLUGIN_ROWS.values() for key in keys})


def is_curated(qualified: str) -> bool:
    return qualified in _CURATED_KEYS or (
        qualified.endswith(".Enable") and area_of(qualified) == PLUGINS)


# Besides the curated rows a table can hold, what the program's own menu saves for one
# table only.
PER_TABLE = frozenset({
    "TableOverride.Difficulty", "TableOverride.Exposure", "TableOverride.ToneMapper",
    "Player.OverrideTableEmissionScale", "Player.EmissionScale",
}) | VIEW_MODES
