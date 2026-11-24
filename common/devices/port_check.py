"""Stops a start the port settings cannot satisfy, in words that name the setting.

A port something else holds, or two settings on one number, otherwise ends as a traceback
from whichever server reached the socket first, or as a Manager that never comes up.
"""

from __future__ import annotations

import errno
import logging
import socket
import sys
import time
from collections.abc import Callable, Iterable

from common.config.config_access import NetworkConfig
from common.failures import why
from common.i18n import t

logger = logging.getLogger("vpinfe.common.devices.port_check")

# The window channel reaches shutdown_system, so it is loopback whatever `http_bind` says.
WINDOW_CHANNEL_BIND = "127.0.0.1"

# A port the previous run is still closing is free within a moment, which is how long a
# restart takes to reach this check.
_RETRIES = 6
_PAUSE = 0.5


def _bind(address: str, port: int) -> OSError | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Windows lets a second socket take a port a first one holds under
        # SO_REUSEADDR, which would report it free.
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((address, port))
        return None
    except OverflowError:
        return OSError(errno.EINVAL, "port must be 0-65535")
    except OSError as exc:
        return exc
    finally:
        probe.close()


def _setting(key: str) -> str:
    return t(f"config.network.{key}.label")


def problems(network: NetworkConfig, keys: Iterable[str] = ("http_port", "ws_port"),
             *, bind: Callable[[str, int], OSError | None] = _bind,
             pause: Callable[[float], None] = time.sleep) -> list[str]:
    """One sentence per setting that cannot be served: a number two settings share, or a
    port this device does not let VPinFE listen on."""
    wanted = {"http_port": (network.http_port, network.http_bind),
              "ws_port": (network.ws_port, WINDOW_CHANNEL_BIND)}
    by_port: dict[int, list[str]] = {}
    for key in keys:
        by_port.setdefault(wanted[key][0], []).append(key)
    found: list[str] = []
    for port, sharing in by_port.items():
        if len(sharing) > 1:
            found.append(t("said.port_shared", first=_setting(sharing[0]),
                           second=_setting(sharing[1]), port=port))
            continue
        key = sharing[0]
        address = wanted[key][1]
        failure = bind(address, port)
        for _ in range(_RETRIES):
            if failure is None or failure.errno != errno.EADDRINUSE:
                break
            pause(_PAUSE)
            failure = bind(address, port)
        if failure is None:
            continue
        if failure.errno == errno.EADDRINUSE:
            found.append(t("said.port_taken", setting=_setting(key), port=port,
                           key=f"network.{key}"))
        else:
            found.append(t("said.port_refused", address=address, port=port,
                           reason=why(failure), setting=_setting(key),
                           key=f"network.{key}"))
    return found


def require(network: NetworkConfig, keys: Iterable[str] = ("http_port", "ws_port")) -> None:
    """Say what is wrong in the terminal and the log, and exit, when any port cannot be
    served. Returns when they all can."""
    found = problems(network, keys)
    if not found:
        return
    for line in found:
        logger.error(line)
        print(line, file=sys.stderr, flush=True)
    raise SystemExit(1)
