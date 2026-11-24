"""The helper an operating system ships for what needs more than VPinFE's own user.

VPinFE never runs as root. Where the system offers a helper, this module is the whole of
what VPinFE says to it: a closed table of methods, each taking a closed set of arguments,
over a Unix socket the helper listens on.

The wire is one request and one answer, each a single line of JSON, then the connection
closes:

    {"method": "updates.apply", "args": {}, "by": "os_update", "origin": "this device"}
    {"ok": true, "result": {...}}
    {"ok": false, "error": "busy", "message": "An update is already running"}

`by` and `origin` are for the helper's journal and are never a basis for permission: the
helper decides who may ask from the socket's peer credentials.
"""

from __future__ import annotations

import json
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.i18n import t

SOCKET = "/run/vpinos/helper.sock"

# The longest line either side sends. An answer longer than this is not one.
MAX_LINE = 64 * 1024

# What a refusal's text may carry into a sentence VPinFE shows, from a process VPinFE does
# not control.
MAX_REASON = 200


class HelperAbsentError(OSError):
    """No helper answered on this device."""


class HelperBrokenError(OSError):
    """A helper stands but gave no answer in time, or one that cannot be read."""


class HelperRefusedError(RuntimeError):
    """The helper answered no. The message is the sentence a person reads."""


@dataclass(frozen=True)
class Method:
    name: str
    # The capability an extension declares to use it.
    area: str
    # A change needs the request to have reached Protected; a read checks nothing.
    changes: bool
    # Longest the helper may take, in seconds.
    timeout: float
    # The arguments it takes, none of which is ever a command, a path or an address.
    args: tuple[str, ...] = ()

    @property
    def sentence(self) -> str:
        """What core asks before the method runs, and what its log line says."""
        return t(f"os.method.{self.name}.says")


METHODS: dict[str, Method] = {one.name: one for one in (
    Method("updates.check", "os:updates", changes=False, timeout=120),
    Method("updates.apply", "os:updates", changes=True, timeout=3600),
)}

AREAS = frozenset(one.area for one in METHODS.values())


def available() -> bool:
    """Whether a socket stands where the helper listens. Nothing is sent: a helper started
    by its first connection would be started by a question about whether it is there."""
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        return False
    try:
        return stat.S_ISSOCK(Path(SOCKET).stat().st_mode)
    except OSError:
        return False


def call(method: Method, args: dict[str, Any], *, by: str, origin: str) -> Any:
    """Ask the helper for `method` and return what it answers.

    Raises `HelperAbsentError` where nobody listens, `HelperBrokenError` where the helper
    gave no answer in time or one that cannot be read, `HelperRefusedError` where it
    answered no, and `ValueError` for an argument the method does not take or JSON cannot
    carry.
    """
    unknown = sorted(set(args) - set(method.args))
    if unknown:
        raise ValueError(t("error.os.bad_args", method=method.name, names=", ".join(unknown)))
    try:
        wanted = json.dumps({"method": method.name, "args": args, "by": by, "origin": origin},
                            separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ValueError(t("error.os.bad_args", method=method.name, names=", ".join(args))) from exc
    answer = _exchange(wanted, method.timeout)
    try:
        said = json.loads(answer, parse_constant=_refuse_constant)
    except (ValueError, RecursionError) as exc:
        raise HelperBrokenError(t("error.os.bad_reply")) from exc
    if not isinstance(said, dict) or not isinstance(said.get("ok"), bool):
        raise HelperBrokenError(t("error.os.bad_reply"))
    if not said["ok"]:
        raise HelperRefusedError(t("error.os.refused", method=method.name,
                                   reason=_reason(said)))
    return said.get("result")


def _refuse_constant(name: str) -> Any:
    raise ValueError(name)


def _exchange(wanted: bytes, timeout: float) -> bytes:
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        raise HelperAbsentError(t("error.os.no_helper"))
    try:
        with socket.socket(af_unix, socket.SOCK_STREAM) as one:
            deadline = time.monotonic() + timeout
            one.settimeout(timeout)
            one.connect(SOCKET)
            one.sendall(wanted)
            return _line(one, deadline)
    except HelperBrokenError:
        raise
    except TimeoutError as exc:
        raise HelperBrokenError(t("error.os.timed_out")) from exc
    except OSError as exc:
        raise HelperAbsentError(t("error.os.no_helper")) from exc


def _line(one: socket.socket, deadline: float) -> bytes:
    held = b""
    while b"\n" not in held:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        one.settimeout(remaining)
        more = one.recv(4096)
        if not more:
            break
        held += more
        if len(held) > MAX_LINE:
            raise HelperBrokenError(t("error.os.bad_reply"))
    return held.split(b"\n", 1)[0]


def _reason(said: dict[str, Any]) -> str:
    text = str(said.get("message") or said.get("error") or "")
    clean = "".join(ch for ch in text if ch.isprintable())
    return clean[:MAX_REASON].strip() or t("error.os.no_reason")
