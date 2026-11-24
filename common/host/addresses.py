"""The addresses this machine can be reached on.

Written down once, answering "what would somebody type into a phone to reach this
install". A page that hands out the wrong address is a page that sends a person to a
machine that is not there, and a second copy is another chance to drift on which
addresses count.

Loopback is offered last and never first. It is the one address that is certainly
listening and the one that certainly does not work from anywhere else, so a list that
led with it would be a list whose first answer is wrong for the reader holding a phone.

This machine's name leads when it resolves to one of this machine's own addresses, and
the address follows it. A name that does not resolve at all is listed after the address; a
name that resolves to another machine is left out, along with that machine's address.

Nothing here waits on the network. A `.local` name that has not answered yet is absent from
the list, so the address leads, and it joins the list when it answers.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from urllib.parse import urlsplit

from common.security import encrypted_connections


def usable_ipv4(value: str) -> bool:
    """Whether this is an address something else on the network could dial.

    Loopback and link-local are excluded here rather than filtered by the caller: a
    169.254 address means the machine failed to get a lease, and offering it reads as
    an answer.
    """
    try:
        found = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return False
    return (found.version == 4 and not found.is_loopback
            and not found.is_link_local and not found.is_multicast
            and not found.is_unspecified)


def primary_ipv4() -> str:
    """The address this machine would use to reach the rest of the network.

    Asked by opening a socket at a public address and reading which interface the
    kernel chose. Nothing is sent - UDP connect only sets the peer - so this costs no
    traffic and works with the network down, which is when the answer is least obvious.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("192.0.2.1", 9))
            found = str(probe.getsockname()[0])
    except Exception:
        return ""
    return found if usable_ipv4(found) else ""


def hosts() -> list[str]:
    """Every name and address worth offering, best first, loopback last.

    The name leads only while it resolves to an address this machine holds: it is what a
    person recognizes and it keeps working when the lease changes, but a name that
    points somewhere else would send them to the wrong machine, so it is left out. The
    routed address follows, and is the lead when there is no such name.

    `<name>.local` takes the name's place while this machine resolves it to itself: a
    phone resolves that through mDNS where it cannot resolve the plain name.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        said = (host or "").strip()
        if said and said.lower() not in seen:
            seen.add(said.lower())
            found.append(said)

    routed = primary_ipv4()
    name = _hostname()
    resolved = _resolved(name) if name else []
    ours = bool(name) and _is_this_machine(resolved, routed)
    mdns = _mdns_name(name)
    mdns_ours = bool(mdns) and _is_this_machine(_resolved(mdns), routed)
    if mdns_ours:
        add(mdns)
    elif ours:
        add(name)
    add(routed)
    if ours:
        for address in resolved:
            add(address)
    elif name and routed and not resolved and not mdns_ours:
        add(name)
    add("localhost")
    return found


def served_names() -> list[str]:
    """The names the certificate vouches for while this run answers HTTPS, else none."""
    if not encrypted_connections.listening().https:
        return []
    return encrypted_connections.certificate_names(
        encrypted_connections.instance().certificate()[0])


def scheme() -> str:
    return "https" if encrypted_connections.listening().https else "http"


def urls(port: int, path: str = "/") -> list[str]:
    """The same list as somewhere to go. While HTTPS is on the names the certificate
    vouches for lead, so the address handed out is one a browser accepts."""
    named = served_names()
    ordered = [*named, *(host for host in hosts() if host not in named)]
    return [f"{scheme()}://{host}:{int(port)}{path}" for host in ordered]


def best(port: int, path: str = "/") -> str:
    """The one to put in front of somebody, or "" where there is nothing to offer.

    Never loopback while anything else is on the list: this is the address that goes
    on a screen for a person holding a different device.
    """
    found = urls(port, path)
    return next((one for one in found if "://localhost:" not in one),
                found[0] if found else "")


def _hostname() -> str:
    try:
        name = socket.gethostname().strip()
    except Exception:
        return ""
    return "" if name.lower() in {"localhost", "ip6-localhost"} else name


def _mdns_name(name: str) -> str:
    first = name.split(".")[0]
    return "" if not first or name.lower().endswith(".local") else f"{first}.local"


def _is_this_machine(resolved: list[str], routed: str) -> bool:
    """Whether any of these is an address on one of this machine's own interfaces."""
    return bool(set(resolved) & ({routed} | _interface_ipv4s()))


def _interface_ipv4s() -> set[str]:
    try:
        import psutil
        return {entry.address for entries in psutil.net_if_addrs().values()
                for entry in entries if entry.family == socket.AF_INET}
    except Exception:  # noqa: BLE001 - psutil raises on odd interfaces; an empty set only costs the name its lead
        return set()


def _resolved(name: str) -> list[str]:
    """The IPv4 addresses a hostname resolves to, or none.

    Never raises: a machine whose own name does not resolve is common enough - it is
    what a fresh container does - and it must not take the rest of the list with it.
    """
    if name.lower().endswith(".local"):
        return _resolved_by_mdns(name)
    return _lookup(name)


# mDNS leaves a machine that does not answer for itself waiting five seconds, and the
# addresses are asked while a page is built. A `.local` name is therefore never waited
# for: the answer held so far is returned at once, none until the first one comes, and a
# held answer older than this is replaced by a lookup that runs beside the page.
_MDNS_HOLD_SECONDS = 30.0
_NETWORK_WATCH_SECONDS = 10.0
_mdns_lock = threading.Lock()
_mdns_held: dict[str, tuple[float, list[str]]] = {}
_mdns_asking: set[str] = set()
_mdns_generation = 0
_watching = False


def _resolved_by_mdns(name: str) -> list[str]:
    """The addresses held for a `.local` name, whatever their age, without waiting.

    An answer past its hold, or none yet, starts one lookup in the background; the page
    that asked is built with what is held. A lookup that was running when `forget()` was
    called stores nothing: it was asked on the network that has gone.
    """
    key = name.lower()
    with _mdns_lock:
        held = _mdns_held.get(key)
        stale = not held or time.monotonic() - held[0] >= _MDNS_HOLD_SECONDS
        if not stale or key in _mdns_asking:
            return held[1] if held else []
        _mdns_asking.add(key)
        generation = _mdns_generation

    def ask() -> None:
        found = _lookup(name)
        with _mdns_lock:
            if generation == _mdns_generation:
                _mdns_held[key] = (time.monotonic(), found)
                _mdns_asking.discard(key)

    threading.Thread(target=ask, daemon=True, name="mdns-lookup").start()
    return held[1] if held else []


def _mdns_target() -> str:
    """The `.local` name that stands for this machine, or "" where it has none."""
    name = _hostname()
    return name if name.lower().endswith(".local") else _mdns_name(name)


def resolving() -> bool:
    """Whether this machine's `.local` name is still waiting for its first answer.

    True means the list is showing the address only because the name has not answered
    yet, so a screen drawn from it can ask again shortly and show the name when it comes.
    A screen reads this before it builds the list, so an answer that lands between the
    two is caught by the next ask.
    """
    target = _mdns_target()
    if not target:
        return False
    with _mdns_lock:
        return target.lower() not in _mdns_held


def warm() -> None:
    """Start asking for this machine's `.local` name, and keep asking when the network
    changes. Runs once; a second call does nothing."""
    global _watching
    with _mdns_lock:
        if _watching:
            return
        _watching = True
    target = _mdns_target()
    if target:
        _resolved_by_mdns(target)
    threading.Thread(target=_watch_network, daemon=True, name="mdns-watch").start()


def _watch_network() -> None:
    """Forget the held answers when the routed address changes: they were taken on the
    old network, and a name that points at the old address is not this machine's now."""
    routed = primary_ipv4()
    while True:
        time.sleep(_NETWORK_WATCH_SECONDS)
        now = primary_ipv4()
        if now == routed:
            continue
        routed = now
        forget()
        target = _mdns_target()
        if target:
            _resolved_by_mdns(target)


def forget() -> None:
    """Ask the `.local` names again at the next lookup."""
    global _mdns_generation
    with _mdns_lock:
        _mdns_generation += 1
        _mdns_held.clear()
        _mdns_asking.clear()


def to_scan(urls: list[str]) -> str:
    """The address to put in a QR code: a device's IP address, because a phone resolves
    that without help while a name may or may not resolve.

    An HTTPS address is used only while the certificate covers it, so a scan never opens
    on a warning. Otherwise the first address that is not loopback.
    """
    vouched = served_names()
    for url in urls:
        host = urlsplit(url).hostname or ""
        if usable_ipv4(host) and (not vouched or host in vouched):
            return url
    return next((url for url in urls if "://localhost:" not in url.lower()),
                urls[0] if urls else "")


def _lookup(name: str) -> list[str]:
    try:
        found = socket.getaddrinfo(name, None, socket.AF_INET)
    except Exception:
        return []
    return [str(where[0]) for family, _, _, _, where in found
            if family == socket.AF_INET and usable_ipv4(str(where[0]))]
