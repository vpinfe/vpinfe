"""Putting this device's controller buttons and keys in front of a Manager.

A browser reads a gamepad and a keyboard only on its own device, so a Manager opened
elsewhere cannot hear this one's. While a page wants them, the window that is the controller
reports each button and key it sees and `common.events` carries it back, so the Manager
page hears it as if it had been pressed there.

Registered once per process against the shared bridge, like `input_events`. The message
goes to every window and the controller one answers: which window that is, is browser-side
knowledge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common import events

if TYPE_CHECKING:
    from frontend.device_channel import DeviceChannel

MESSAGE_TYPE = "CaptureArm"

_registered = False
_bridge = None


def on_armed(*, armed: bool, ttl_ms: int = 0, browser: str = "", device: str = "",
             **_payload: object) -> None:
    if _bridge is None:
        return
    _bridge.send_event_all_with_iframe({"type": MESSAGE_TYPE, "armed": bool(armed),
                                        "ttl_ms": int(ttl_ms), "browser": str(browser),
                                        "device": str(device)})


def register(ws_bridge: DeviceChannel) -> None:
    """Attach the bridge to the bus. Idempotent."""
    global _registered, _bridge
    _bridge = ws_bridge
    if _registered:
        return
    events.subscribe(events.INPUT_CAPTURE_ARMED, on_armed)
    _registered = True


def reset_for_tests() -> None:
    global _registered, _bridge
    _registered = False
    _bridge = None
