"""What to call a device that reached this one: the name it answers to on the network, or
its address when it has none."""

from __future__ import annotations

import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

LOOKUP_SECONDS = 1.0

_lookups = ThreadPoolExecutor(max_workers=2, thread_name_prefix="peer-name")
_found: dict[str, str] = {}
_lock = threading.Lock()


def place_of(address: str) -> str:
    """The network name for `address`, found by reverse lookup, else `address` itself.

    A lookup that gets no answer in `LOOKUP_SECONDS` is given up on, so an ask never waits
    on a slow resolver. Only a name found is kept; a miss is tried again at the next ask."""
    try:
        ipaddress.ip_address(address)
    except ValueError:
        return address
    with _lock:
        if address in _found:
            return _found[address]
    try:
        name = _lookups.submit(socket.gethostbyaddr, address).result(LOOKUP_SECONDS)[0].strip()
    except Exception:  # noqa: BLE001 - any failure to name it leaves the address
        return address
    if not name:
        return address
    with _lock:
        _found[address] = name
    return name
