"""GNOME on Wayland: Mutter's `GetCurrentState`, asked in-process where `jeepney` is
installed and through `busctl` where it is not."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from common.capture import geometry

from ..model import OK, Reading, ScreenFact
from ..run import Run
from . import unavailable, version_in

SOURCE = "Mutter"

NAME = "org.gnome.Mutter.DisplayConfig"
PATH = "/org/gnome/Mutter/DisplayConfig"
FEATURE = "scale-monitors-framebuffer"
# The layout mode that sizes a logical monitor in scaled pixels, GNOME's default.
LOGICAL_LAYOUT = 1

Call = Callable[[Mapping[str, str]], object]


def unwrapped(value: Any) -> Any:
    """A reply as plain Python: a variant is its value, whichever transport carried it.
    `jeepney` hands a variant over as a `(signature, value)` tuple, only ever as the value
    of a dictionary, and `busctl` as `{"type": ..., "data": ...}`."""
    if isinstance(value, dict):
        if set(value) == {"type", "data"}:
            return unwrapped(value["data"])
        return {key: unwrapped(one[1] if isinstance(one, tuple) and len(one) == 2
                               and isinstance(one[0], str) else one)
                for key, one in value.items()}
    if isinstance(value, (list, tuple)):
        return [unwrapped(one) for one in value]
    return value


def _call_jeepney(env: Mapping[str, str]) -> object:
    """Raises ImportError where `jeepney` is not installed, and OSError where the session
    bus does not answer."""
    from jeepney import DBusAddress, new_method_call
    from jeepney.auth import AuthenticationError
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse, unwrap_msg

    runtime = str(env.get("XDG_RUNTIME_DIR") or "")
    address = str(env.get("DBUS_SESSION_BUS_ADDRESS") or "") \
        or (f"unix:path={runtime}/bus" if runtime else "")
    if not address:
        raise ConnectionRefusedError
    try:
        connection = open_dbus_connection(bus=address)
    except (ValueError, RuntimeError, AuthenticationError) as exc:
        raise OSError from exc
    with connection:
        try:
            body = unwrap_msg(connection.send_and_get_reply(
                new_method_call(DBusAddress(PATH, bus_name=NAME, interface=NAME),
                                "GetCurrentState"), timeout=3))
        except DBusErrorResponse as exc:
            raise OSError from exc
    return list(body)


def _unmarked(state: object) -> object:
    """The state with each monitor's vendor and serial replaced, for the record kept of an
    answer this parser could not read."""
    plain = unwrapped(state)
    try:
        monitors = [[[*one[0][:1], "-", *one[0][2:3], "-"], *one[1:]] for one in plain[1]]
    except (IndexError, TypeError):
        return "-"
    return [plain[0], monitors, *plain[2:]]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_state(state: object) -> list[ScreenFact] | None:
    """One screen for each logical monitor, and one switched-off screen for each monitor in
    none. A monitor's serial and vendor are never read. None where the state is not
    `GetCurrentState`'s."""
    plain = unwrapped(state)
    if not isinstance(plain, list) or len(plain) < 3 \
            or not isinstance(plain[1], list) or not isinstance(plain[2], list):
        return None
    properties = plain[3] if len(plain) > 3 and isinstance(plain[3], dict) else {}
    layout = properties.get("layout-mode", LOGICAL_LAYOUT)
    monitors: dict[str, tuple[str | None, tuple[int, int] | None, float | None]] = {}
    try:
        for one in plain[1]:
            ident, modes = one[0], one[1]
            current = next((mode for mode in modes
                            if isinstance(mode[6], dict) and mode[6].get("is-current")), None)
            monitors[str(ident[0])] = (
                _text(ident[2]),
                (int(current[1]), int(current[2])) if current else None,
                float(current[3]) if current else None)
        found: list[ScreenFact] = []
        placed: set[str] = set()
        for one in plain[2]:
            x, y, scale, transform, primary, members = one[:6]
            names = [str(member[0]) for member in members]
            placed.update(names)
            model, mode, refresh = monitors.get(names[0], (None, None, None)) \
                if names else (None, None, None)
            turn = geometry.wayland_transform(transform)
            size = turn.size(*mode) if mode else None
            if size and layout == LOGICAL_LAYOUT and scale:
                size = (round(size[0] / float(scale)), round(size[1] / float(scale)))
            found.append(ScreenFact(
                connector=names[0] if names else None, model=model, x=int(x), y=int(y),
                width=size[0] if size else None, height=size[1] if size else None,
                scale=float(scale), refresh_hz=refresh, turn=turn, said=str(transform),
                primary=bool(primary), enabled=True,
                mirrors=names[1] if len(names) > 1 else None, source=SOURCE))
        for name, (model, _, _) in monitors.items():
            if name not in placed:
                found.append(ScreenFact(connector=name, model=model, enabled=False,
                                        source=SOURCE))
    except (IndexError, TypeError, ValueError, KeyError):
        return None
    return found


def parse_experimental(text: str) -> bool:
    return FEATURE in text


def probe(env: Mapping[str, str], run: Run, call: Call = _call_jeepney) -> Reading:
    state: object
    try:
        state = call(env)
    except (ImportError, OSError):
        said = run(["busctl", "--user", "--json=short", "call", NAME, PATH, NAME,
                    "GetCurrentState"])
        if not said.ok:
            if said.state == "failed":
                return Reading.failed(SOURCE, "The desktop did not answer")
            return unavailable("busctl", said)
        try:
            state = json.loads(said.text).get("data")
        except (ValueError, AttributeError):
            return Reading.failed(SOURCE, "busctl printed something this report cannot read")
    screens = parse_state(state)
    if screens is None:
        return Reading.failed(SOURCE, "Mutter answered with something this report cannot "
                              "read", repr(_unmarked(state)))
    facts = []
    shell = run(["gnome-shell", "--version"])
    if shell.ok and version_in(shell.text):
        facts.append(("GNOME Shell", version_in(shell.text)))
    features = run(["gsettings", "get", "org.gnome.mutter", "experimental-features"])
    if features.ok:
        facts.append((f"Experimental {FEATURE}",
                      "On" if parse_experimental(features.text) else "Off"))
    return Reading(SOURCE, OK, screens=tuple(screens), facts=tuple(facts),
                   notes=("GNOME has no window rule, so window placement here is the "
                          "desktop's own",))

