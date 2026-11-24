"""What a Remote can press on a running Visual Pinball table, and the key behind each."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from common.apps.contract import (
    CONTROLS_COINS,
    CONTROLS_DEBUG,
    CONTROLS_GAME,
    CONTROLS_MENU,
    CONTROLS_NUDGE,
    CONTROLS_PLAY,
    CONTROLS_SERVICE,
    CONTROLS_SOUND,
    NOT_A_KEY,
    PAUSE,
    SIDE_LEFT,
    SIDE_RIGHT,
    UNMAPPED,
    Control,
)

from . import bindings, keys
from .config import settings_file
from .pause import VPXPause

logger = logging.getLogger("vpinfe.apps.vpx.controls")

_HOLD_MS = 100

_OFFERED = (
    Control("left_flipper", CONTROLS_PLAY, _HOLD_MS, when_mapped=True, holdable=True,
            side=SIDE_LEFT),
    Control("right_flipper", CONTROLS_PLAY, _HOLD_MS, when_mapped=True, holdable=True,
            side=SIDE_RIGHT),
    Control("launch_ball", CONTROLS_PLAY, _HOLD_MS, when_mapped=True, holdable=True),
    Control("left_magna", CONTROLS_PLAY, _HOLD_MS, when_mapped=True, holdable=True,
            side=SIDE_LEFT),
    Control("right_magna", CONTROLS_PLAY, _HOLD_MS, when_mapped=True, holdable=True,
            side=SIDE_RIGHT),
    Control("start", CONTROLS_GAME, _HOLD_MS),
    Control("show_rules", CONTROLS_GAME),
    Control("reset", CONTROLS_GAME),
    Control("extra_ball", CONTROLS_GAME),
    Control("fire", CONTROLS_GAME),
    Control(PAUSE, CONTROLS_GAME),
    Control("credit_1", CONTROLS_COINS),
    Control("credit_2", CONTROLS_COINS),
    Control("credit_3", CONTROLS_COINS),
    Control("credit_4", CONTROLS_COINS),
    Control("volume_down", CONTROLS_SOUND),
    Control("volume_up", CONTROLS_SOUND),
    Control("nudge_left", CONTROLS_NUDGE, _HOLD_MS, when_mapped=True),
    Control("nudge_center", CONTROLS_NUDGE, _HOLD_MS, when_mapped=True),
    Control("nudge_right", CONTROLS_NUDGE, _HOLD_MS, when_mapped=True),
    Control("coin_door", CONTROLS_MENU),
    Control("menu_up", CONTROLS_MENU, _HOLD_MS),
    Control("menu_down", CONTROLS_MENU, _HOLD_MS),
    Control("menu_enter", CONTROLS_MENU, _HOLD_MS),
    Control("menu_cancel", CONTROLS_MENU, _HOLD_MS),
    *(Control(f"service_{number}", CONTROLS_SERVICE) for number in range(1, 9)),
    Control("toggle_stereo", CONTROLS_DEBUG),
    Control("perf_overlay", CONTROLS_DEBUG),
    Control("debugger", CONTROLS_DEBUG),
    Control("debug_balls", CONTROLS_DEBUG),
)

# The `Mapping.*` name VPX files each under. A control it maps nothing to sends VPX's own key.
_MAPPED = {
    "left_flipper": "LeftFlipper",
    "right_flipper": "RightFlipper",
    "launch_ball": "LaunchBall",
    "left_magna": "LeftMagna",
    "right_magna": "RightMagna",
    "nudge_left": "LeftNudge",
    "nudge_center": "CenterNudge",
    "nudge_right": "RightNudge",
    "start": "Start",
    "show_rules": "ShowRules",
    "reset": "Reset",
    "extra_ball": "ExtraBall",
    "fire": "Lockbar",
    "credit_1": "Credit1",
    "credit_2": "Credit2",
    "credit_3": "Credit3",
    "credit_4": "Credit4",
    "volume_down": "VolumeDown",
    "volume_up": "VolumeUp",
    "coin_door": "CoinDoor",
    **{f"service_{number}": f"Service{number}" for number in range(1, 9)},
    "toggle_stereo": "ToggleStereo",
    "perf_overlay": "PerfOverlay",
    "debugger": "Debugger",
    "debug_balls": "DebugBalls",
}

# PinMAME's service menu listens on these whatever the settings file says.
_FIXED = {
    "menu_up": "Digit9",
    "menu_down": "Digit8",
    "menu_enter": "Digit0",
    "menu_cancel": "Digit7",
}


class VPXControls:
    def offered(self) -> tuple[Control, ...]:
        return _OFFERED

    def key(self, control_id: str, settings: Mapping[str, Any]) -> str:
        """`settings` is the launcher's. A file that cannot be read maps nothing."""
        if control_id in _FIXED:
            return _FIXED[control_id]
        if control_id == PAUSE:
            return VPXPause().key(settings)
        return self._found(control_id, settings)[0]

    def why_not(self, control_id: str, settings: Mapping[str, Any]) -> str:
        if control_id in _FIXED or control_id == PAUSE:
            return ""
        return self._found(control_id, settings)[1]

    @staticmethod
    def _found(control_id: str, settings: Mapping[str, Any]) -> tuple[str, str]:
        """The key, or "" and why there is none."""
        name = _MAPPED.get(control_id)
        if name is None:
            return "", UNMAPPED
        written = ""
        file = settings_file(settings)
        if file is not None:
            try:
                written = keys.entries(str(file)).get(name, "")
            except (OSError, UnicodeError) as error:
                logger.warning("Could not read %s (%s), so %s is the key VPX sends on its "
                               "own", file, error, name)
        qualified = f"{bindings.SECTION}.{bindings.PREFIX}{name}"
        held = bindings.parse(written, bindings.default_of(qualified))
        for chord in held.keys:
            if len(chord) == 1:
                return chord[0], ""
        return "", NOT_A_KEY if held.keys or held.kept else UNMAPPED
