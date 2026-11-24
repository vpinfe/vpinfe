"""What needs the person at the machine, put in front of them: a Confirm Code or a pairing
code, a browser asking to sign in, or a program asking to make a change.

`common.security.sign_in` holds the request and the digits and `common.security.approvals`
holds the asks. This sends what the screen should show to the windows as one `SignInRequest`
message, and core draws it on the controller window. `request` is null once there is nothing
to show. A Confirm or pairing code comes first, drawn at once with no Show; then a sign-in;
an ask waits behind both and stays in Needs You meanwhile.

Pushed with `send_event_all`, not the variant that also reaches theme iframes, so the code
goes to the page core runs in and not on into every frame a theme embeds.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from common.security import approvals, sign_in

if TYPE_CHECKING:
    from frontend.device_channel import DeviceChannel

MESSAGE = "SignInRequest"

# The screen is read across a room, so a request is shown whole or not approved from here.
READABLE_LINE = 160

_bridge: DeviceChannel | None = None
# The codes this screen was closed on. They keep working; the window just stops drawing them.
_closed: set[str] = set()
_closed_lock = threading.Lock()
_registered_on: tuple[sign_in.SignIn, approvals.Approvals] | None = None


def view(request: sign_in.Request | None) -> dict[str, Any] | None:
    """What the screen is given. `code` is empty until Show has been pressed."""
    if request is None:
        return None
    return {"id": request.id, "kind": "sign_in", "address": request.address,
            "agent": request.agent, "place": request.place or request.address,
            "grant": sign_in.SESSION_DAYS, "state": request.state,
            "code": request.code if request.state == sign_in.SHOWN else "",
            "expires_in": sign_in.instance().seconds_left(request)}


def code_view(code: sign_in.Confirm) -> dict[str, Any]:
    pairing = code.purpose == sign_in.PAIR
    return {"id": code.id, "kind": "pair" if pairing else "confirm",
            "address": code.address, "agent": code.agent,
            "place": code.place or code.address, "name": code.name,
            "grant": 0 if pairing else sign_in.CONFIRM_SECONDS // 60,
            "state": sign_in.SHOWN, "code": code.code,
            "expires_in": sign_in.instance().seconds_left(code)}


def _code() -> sign_in.Confirm | None:
    live = sign_in.instance().confirms()
    with _closed_lock:
        _closed.intersection_update(one.id for one in live)
        return next((one for one in live if one.id not in _closed), None)


def _too_long(ask: approvals.Ask) -> bool:
    return len(" ".join((ask.method, ask.path, ask.body))) > READABLE_LINE


def approval_view(ask: approvals.Ask | None) -> dict[str, Any] | None:
    if ask is None:
        return None
    return {"id": ask.id, "kind": "approval", "program": ask.token_name,
            "method": ask.method, "path": ask.path[:READABLE_LINE],
            "body": ask.body[:READABLE_LINE], "long": _too_long(ask),
            "expires_in": approvals.instance().seconds_left(ask)}


def _shown(request: sign_in.Request | None) -> dict[str, Any] | None:
    code = _code()
    if code is not None:
        return code_view(code)
    return view(request) or approval_view(approvals.instance().on_screen())


def pending() -> dict[str, Any] | None:
    return _shown(sign_in.instance().pending())


def show(request_id: str) -> dict[str, Any] | None:
    """Show: the digits, for the screen that pressed it. None when the request has gone."""
    return view(sign_in.instance().reveal(request_id))


def ignore(request_id: str) -> None:
    sign_in.instance().ignore(request_id)


def dismiss(request_id: str) -> None:
    """Close. A sign-in ends; a code or an ask leaves the screen and stays good, an ask in
    Needs You."""
    if any(one.id == request_id for one in sign_in.instance().confirms()):
        with _closed_lock:
            _closed.add(request_id)
        _push_now()
    elif approvals.instance().get(request_id) is not None:
        approvals.instance().leave_screen(request_id)
    else:
        sign_in.instance().dismiss(request_id)


def approve(ask_id: str) -> None:
    """Approve, from the machine's own screen. An ask too long to read here is not approved."""
    ask = approvals.instance().get(ask_id)
    if ask is not None and not _too_long(ask):
        approvals.instance().approve(ask_id)


def decline(ask_id: str) -> None:
    approvals.instance().decline(ask_id)


def _push_now() -> None:
    if _bridge is not None:
        _bridge.send_event_all({"type": MESSAGE, "request": pending()})


def _push(request: sign_in.Request | None) -> None:
    if _bridge is not None:
        _bridge.send_event_all({"type": MESSAGE, "request": _shown(request)})


def register(ws_bridge: DeviceChannel) -> None:
    """Attach the windows to the sign-in and the asks. Idempotent for one of each."""
    global _bridge, _registered_on
    _bridge = ws_bridge
    current = (sign_in.instance(), approvals.instance())
    if _registered_on is not None and _registered_on[0] is current[0] \
            and _registered_on[1] is current[1]:
        return
    if _registered_on is None or _registered_on[0] is not current[0]:
        current[0].on_request(_push)
    if _registered_on is None or _registered_on[1] is not current[1]:
        current[1].on_change(_push_now)
    _registered_on = current


def reset_for_tests() -> None:
    """Forget the bridge. The listeners stay on their stores, which a test replaces with
    `sign_in.use` and `approvals.use`, and do nothing without a bridge."""
    global _bridge, _registered_on
    _bridge = None
    _registered_on = None
    _closed.clear()
