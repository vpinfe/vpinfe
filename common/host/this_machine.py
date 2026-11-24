"""Whether a request is this machine's own, judged from the socket and the browser's headers.

Trust that needs no credential takes four things together: the peer is this machine, nothing
on the way in says it was forwarded, the request asked for a name this machine answers to,
and a browser that sent it did so from a page of VPinFE's own. A page on another site fails
the last, a DNS-rebound name fails the third, and a proxy fails the second.

Judged from the socket's address and the request's Host, Origin and Sec-Fetch-Site, never
from a forwarding header: those are written by the caller. A proxy the Owner has listed is
the one exception, and only for who the client was and whether it came in over HTTPS
(common/security/encrypted_connections.py); a request through it is still never this machine's own.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from common.devices import serving
from common.host import addresses
from common.security import encrypted_connections

LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})
LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "localhost"})

# What Starlette's TestClient reports in place of an address, and the Host it sends.
TEST_PEER = "testclient"
TEST_HOST = "testserver"

# Headers a proxy adds. A request that carries any came through something else, whatever
# address that something connected from.
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host",
                      "x-forwarded-proto", "x-forwarded-port", "x-real-ip")

# Where the browser says a request began, relative to the page it was sent for. The one
# value that is another site's page.
CROSS_SITE = "cross-site"

_REFRESH_SECONDS = 30.0
_INTERFACE_SECONDS = 5.0
_lock = threading.Lock()
_names: frozenset[str] = frozenset()
_taken = 0.0
_refreshing = False
_interfaces: frozenset[str] = frozenset()
_interfaces_taken = 0.0


def normalized(address: str) -> str:
    """An address in one spelling, so `::ffff:10.0.0.5` and `10.0.0.5` compare equal."""
    return encrypted_connections.canonical(address)


def _interface_addresses() -> set[str]:
    found: set[str] = set()
    try:
        import psutil
        for entries in psutil.net_if_addrs().values():
            for entry in entries:
                if entry.family in (socket.AF_INET, socket.AF_INET6):
                    found.add(normalized(entry.address))
    except Exception:  # noqa: BLE001 - psutil is optional and a bad interface must not stop a request
        pass
    return found


def _collect() -> frozenset[str]:
    names = set(LOOPBACK_NAMES) | {"127.0.0.1", "::1"}
    names.update(_interface_addresses())
    for host in addresses.hosts():
        names.add(normalized(host))
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            own = getter().strip().lower().rstrip(".")
        except Exception:  # noqa: BLE001 - a name that cannot be read is one fewer
            continue
        if own:
            names.update({own, own.split(".")[0], own.split(".")[0] + ".local"})
    return frozenset(names)


def _held_interfaces() -> frozenset[str]:
    """The addresses on this machine's own interfaces, never anything a name resolved to:
    a peer that a DNS record points at is not this machine."""
    global _interfaces, _interfaces_taken
    with _lock:
        if _interfaces and time.monotonic() - _interfaces_taken < _INTERFACE_SECONDS:
            return _interfaces
    found = frozenset(_interface_addresses())
    with _lock:
        _interfaces, _interfaces_taken = found, time.monotonic()
        return _interfaces


def _refresh_in_background() -> None:
    global _names, _taken, _refreshing
    try:
        found: frozenset[str] | None = _collect()
    except Exception:  # noqa: BLE001 - the names already held stay until the next try
        found = None
    with _lock:
        if found:
            _names, _taken = found, time.monotonic()
        _refreshing = False


def own_names() -> frozenset[str]:
    """Every name and address this machine answers to.

    Resolving a hostname can take seconds, and this is asked on the event loop, so only the
    first ask waits for it. Later asks answer from what is held while a thread takes it again.
    """
    global _names, _taken, _refreshing
    with _lock:
        if _names:
            if time.monotonic() - _taken > _REFRESH_SECONDS and not _refreshing:
                _refreshing = True
                threading.Thread(target=_refresh_in_background, daemon=True,
                                 name="this-machine-names").start()
            return _names
    found = _collect()
    with _lock:
        _names, _taken = found, time.monotonic()
        return _names


def warm() -> None:
    """Take the names now, from a thread, so the first request does not wait on DNS."""
    threading.Thread(target=own_names, daemon=True, name="this-machine-warm").start()


def forget() -> None:
    """Take the names again at the next ask. For a test."""
    global _names, _taken, _interfaces, _interfaces_taken
    with _lock:
        _names, _taken = frozenset(), 0.0
        _interfaces, _interfaces_taken = frozenset(), 0.0


def split_authority(value: str) -> tuple[str, int | None]:
    """The host and port of a `Host` header or the authority of an Origin."""
    text = (value or "").strip().lower()
    if not text:
        return "", None
    parts = urlsplit("//" + text)
    try:
        port = parts.port
    except ValueError:
        port = None
    return (parts.hostname or "").rstrip("."), port


def _is_loopback_address(host: str) -> bool:
    try:
        return ipaddress.ip_address(normalized(host)).is_loopback
    except ValueError:
        return False


def names_this_machine(host: str, *, test_peer: bool = False) -> bool:
    """Whether a host (a name or an address) is one this machine answers to."""
    name = (host or "").strip().lower().rstrip(".")
    if not name:
        return False
    if test_peer and name == TEST_HOST:
        return True
    # Loopback is answered before own_names(), which resolves the hostname and can take seconds.
    return (_is_loopback_address(name) or name.endswith(".localhost")
            or normalized(name) in own_names() or name in own_names())


def peer_is_this_machine(peer: str | None) -> bool:
    """Whether the socket's own address is this machine: loopback, or one of its interfaces."""
    if not peer:
        return True
    if peer in LOOPBACK_PEERS or peer == TEST_PEER or _is_loopback_address(peer):
        return True
    return normalized(peer) in _held_interfaces()


def is_forwarded(headers: Mapping[str, str]) -> bool:
    keys = {str(key).lower() for key in headers}
    return any(name in keys for name in FORWARDING_HEADERS)


def origin_is_ours(origin: str, host: str) -> bool:
    """Whether a browser's Origin is VPinFE's own page: the address that was asked for."""
    said = (origin or "").strip()
    if not said or said.lower() == "null":
        return False
    parts = urlsplit(said)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    origin_host, origin_port = split_authority(parts.netloc)
    asked_host, asked_port = split_authority(host)
    return origin_host == asked_host and origin_port == asked_port


def came_through_listed_proxy(peer: str | None, port: int | None) -> bool:
    """Whether the request came from a proxy the Owner listed, and not on the port this
    machine's own windows use. That port exists only while HTTPS is on; with none, a proxy
    on this device cannot be told from a window, and the list changes nothing here."""
    own = serving.bound_port("loopback_port")
    return (own is not None and port != own
            and encrypted_connections.instance().is_proxy(peer))


def request_is_this_machine(peer: str | None, headers: Mapping[str, str],
                            port: int | None = None) -> bool:
    """The machine's own trust. A request with no Origin is not a page's script, so Origin
    is checked only where it is sent; `Sec-Fetch-Site` catches the cross-site request a
    browser sends without one, such as an image or a navigation. `port` is the one the
    request came in on."""
    if not peer_is_this_machine(peer):
        return False
    if came_through_listed_proxy(peer, port):
        return False
    lowered = {str(key).lower(): value for key, value in headers.items()}
    if is_forwarded(lowered):
        return False
    if str(lowered.get("sec-fetch-site", "")).strip().lower() == CROSS_SITE:
        return False
    test_peer = peer == TEST_PEER
    host = lowered.get("host", "")
    if host and not names_this_machine(split_authority(host)[0], test_peer=test_peer):
        return False
    origin = lowered.get("origin")
    return origin is None or origin_is_ours(origin, host)
