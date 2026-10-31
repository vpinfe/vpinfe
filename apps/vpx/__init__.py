"""Visual Pinball - the app we understand, and the reference for the rest.

10.8 is the supported floor. What a particular install can do is probed rather than
derived from its version; see `capability`.
"""

from __future__ import annotations

from common.apps.contract import App, Claim, Field, Kinds

from .buttons import VPXButtons
from .capability import VPXCapability
from .capture import VPXCapture
from .config import VPXConfig
from .controls import VPXControls
from .launch import VPXLaunch
from .pause import VPXPause

# What sits beside a table and belongs to it: its settings, a script, its
# backglass, a point of view, a saved camera. Here rather than in a generic module
# because it is this program's list and the next app's will differ.
COMPANION_SUFFIXES: tuple[str, ...] = (".ini", ".vbs", ".directb2s", ".pov", ".scv", ".nix")

# What running Visual Pinball takes, in the order a person meets it: what to run, then
# what to run it with. The names have shed the `vpx_` prefix - a field on a Visual
# Pinball launcher does not need to say which app it belongs to.
FIELDS: tuple[Field, ...] = (
    Field("bin_path", path="exe"),
    Field("ini_path", path="file", suffixes=(".ini",)),
    Field("launch_env", type="text", lines=3),
    Field("log_delete_on_start", type="bool", default="false"),
)

# `format` is unset: reading a table's OLE container still lives in core, and moving it
# is its own piece of work. A group nothing implements answers None, which every
# consumer already handles.
VPX = App(
    id="vpx",
    name="Visual Pinball X",
    claim=Claim(suffixes=(".vpx",),
                # Stem-matched files that belong to one of its tables.
                companions=COMPANION_SUFFIXES),
    vps_formats=("VPX",),
    fields=FIELDS,
    kinds=Kinds(),
    launch=VPXLaunch(),
    config=VPXConfig(),
    capability=VPXCapability(),
    capture=VPXCapture(),
    pause=VPXPause(),
    buttons=VPXButtons(),
    controls=VPXControls(),
)
