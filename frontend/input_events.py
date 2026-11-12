"""Carrying a press from the bus to the windows.

`common/host/input_actions.py` decides what a press is; this puts it where the browser can
answer it. Registered once per process against the shared bridge, for the same reason
`play_events` is: every API instance sends through the same one, so registering per
window would send each press three times.

Broadcast to every window rather than aimed at one. Python does not know which window is
the controller and should not learn - that is browser-side knowledge, and core's dispatch
already branches on it. Aiming from here would make one press three presses on a
three-screen cabinet, or make this file track window roles.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from common import events
from common.host import input_actions

if TYPE_CHECKING:
    from frontend.device_channel import DeviceChannel

logger = logging.getLogger("vpinfe.frontend.input_events")

# The message core routes into the same dispatch as a keystroke. Named for what it is
# rather than for where it came from: a theme never sees it, and the window that answers
# it does not care whether a thumb or a button board produced it.
MESSAGE_TYPE = "InputAction"

_registered = False
_bridge = None


def on_input_action(*, action: str, phase: str, source: str = "",
                    **_payload: Any) -> None:
    """Put one press or release in front of every window.

    `source` stays here and in the log. A theme that behaved differently depending on
    where a press came from would be a bug surface, and a contract 1 theme cannot see it
    in any case - so what reaches the theme is exactly what a keystroke reaches it with.

    Not a press the page itself reported: it has answered that one already.
    """
    if _bridge is None or source == input_actions.PAGE:
        return
    _bridge.send_event_all_with_iframe({"type": MESSAGE_TYPE,
                                        "action": action, "phase": phase})


def register(ws_bridge: DeviceChannel) -> None:
    """Attach the bridge to the input bus. Idempotent."""
    global _registered, _bridge
    _bridge = ws_bridge
    if _registered:
        return
    events.subscribe(events.INPUT_ACTION, on_input_action)
    _registered = True


def shutdown() -> None:
    """Let go of anything still held, so a process going away does not leave a wheel
    spinning in a window that outlives it."""
    input_actions.release_all()


def reset_for_tests() -> None:
    global _registered, _bridge
    _registered = False
    _bridge = None
