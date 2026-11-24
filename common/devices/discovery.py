"""Saying what this install is, on the network, and hearing what else is out there.

Announced rather than reported. An install says who it is on the LAN and every other
install decides for itself what to do with that, so nothing registers itself anywhere -
which is what lets a machine that only launches games carry no address for a machine
that manages it.

The announcement carries identity and nothing else: who this is, what it is called, what
it is for, and which build. Capabilities do not fit a TXT record and stay an HTTP fetch
for a peer somebody has decided to care about.

A home LAN is the assumption, and it is the one that makes this fine. Anything on the
network can claim to be a VPinFE install, so what comes back from here is a list of what
said it was there - untrusted input, not a record of anything deliberate.

Multicast is not everywhere. Some networks filter it, it does not cross a Docker bridge,
and Windows asks the first time a process binds. So this is best effort throughout: a
network that refuses costs discovery, never a startup, and manual entry stays.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

from common.config.config_store import ConfigStore
from common.devices import install_identity, serving
from common.security import encrypted_connections
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.devices.discovery")

SERVICE_TYPE = "_vpinfe._tcp.local."

# Set to keep an install off the network entirely. For a test that starts a real VPinFE:
# those get their own config dir and their own ports so they cannot touch the developer's
# install, and announcing themselves would put them in its device list anyway.
OFF = "VPINFE_NO_DISCOVERY"

# What the TXT record is keyed by. Short names because a TXT record is not free, and
# stable because a peer two releases behind reads them.
ID = "id"
NAME = "name"
FEATURES = "features"
VERSION = "version"
SCHEME = "scheme"


@dataclass(frozen=True)
class Peer:
    """One install heard announcing itself.

    `address` and `port` are how to ask it anything, which is the half that cannot come
    from the record's own contents: an install declares the port it answers on, and
    mDNS resolves the address.
    """

    install_id: str
    display_name: str
    features: tuple[str, ...]
    address: str
    port: int
    # What it is reached over. An install two releases behind says nothing and is plain HTTP.
    https: bool = False

    @property
    def url(self) -> str:
        return f"{'https' if self.https else 'http'}://{self.address}:{self.port}"


class _Discovery:
    """The one Zeroconf this process has, and what it has heard.

    A single instance because each one binds the multicast sockets, and a second would
    be a second machine on the network as far as anyone listening is concerned.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._info: ServiceInfo | None = None
        self._peers: dict[str, Peer] = {}
        self._mine = ""
        self._on_peer: Callable[[Peer], None] | None = None

    def start(self, config: ConfigStore,
              on_peer: Callable[[Peer], None] | None = None) -> None:
        with self._lock:
            if self._zeroconf is not None:
                return
            self._mine = install_identity.install_id()
            self._on_peer = on_peer
            self._zeroconf = Zeroconf()
            announced = self._announce(config)
            self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE,
                                           handlers=[self._changed])
            if announced:
                logger.info("Announcing this install on %s", SERVICE_TYPE)

    def _announce(self, config: ConfigStore, update: bool = False) -> bool:
        """True when the announcement went out, False when there is no address to give."""
        # Both callers hold the lock and have already established this: `start` has just
        # constructed it, and `refresh` returns early without one.
        assert self._zeroconf is not None
        port = serving.network(config).http_port
        address = _routable_address()
        if not address:
            logger.info("No network route, so this install is not announcing itself")
            return False
        # Keyed on the install id, which is the one name guaranteed not to collide with
        # another machine's. What a person reads is in the record, not in the key.
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"{self._mine}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(address)],
            port=port,
            properties={
                ID: self._mine,
                NAME: install_identity.display_name(config),
                FEATURES: ",".join(install_identity.features(config)),
                VERSION: get_version(),
                SCHEME: "https" if encrypted_connections.serves_https() else "http",
            },
            server=f"vpinfe-{self._mine}.local.",
        )
        if update:
            self._zeroconf.update_service(self._info)
        else:
            self._zeroconf.register_service(self._info)
        return True

    def _changed(self, zeroconf: Zeroconf, service_type: str, name: str,
                 state_change: ServiceStateChange) -> None:
        """What the browser heard. Resolving happens on a thread of its own, because
        this runs on Zeroconf's and asking it a question from inside its own callback is
        how that loop stops answering."""
        if state_change is ServiceStateChange.Removed:
            self._forget(name)
            return
        threading.Thread(target=self._resolve, args=(zeroconf, service_type, name),
                         daemon=True, name="vpinfe-resolve").start()

    def _resolve(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
        except Exception:
            logger.debug("Could not resolve %s", name, exc_info=True)
            return
        peer = _as_peer(info)
        # Our own announcement comes back like everyone else's. Dropped here rather than
        # filtered by every caller, because "the installs on this network" never means
        # this one.
        if peer is None or peer.install_id == self._mine:
            return
        with self._lock:
            self._peers[name] = peer
            told = self._on_peer
        if told is not None:
            try:
                told(peer)
            except Exception:
                logger.debug("A discovery handler raised", exc_info=True)

    def _forget(self, name: str) -> None:
        with self._lock:
            gone = self._peers.pop(name, None)
        if gone is not None:
            logger.info("%s has gone quiet", gone.display_name or gone.install_id)

    def refresh(self, config: ConfigStore) -> None:
        """Say the same thing again with what this install now calls itself. Silent when
        nothing is announcing: refreshing is what a rename does, and a rename on a machine
        with no network is not an error."""
        with self._lock:
            if self._zeroconf is None:
                return
            self._announce(config, update=True)

    def peers(self) -> list[Peer]:
        with self._lock:
            return sorted(self._peers.values(),
                          key=lambda peer: peer.display_name.lower())

    def stop(self) -> None:
        """Say goodbye and let go of the sockets.

        The goodbye is the point: it is what turns a machine going away into an event
        rather than something the other end works out from a timeout.
        """
        with self._lock:
            zeroconf, info = self._zeroconf, self._info
            self._zeroconf = self._browser = self._info = None
            self._peers = {}
        if zeroconf is None:
            return
        try:
            if info is not None:
                zeroconf.unregister_service(info)
            zeroconf.close()
        except Exception:
            logger.debug("Could not close discovery cleanly", exc_info=True)


_discovery = _Discovery()


def start(config: ConfigStore, on_peer: Callable[[Peer], None] | None = None) -> None:
    """Announce this install and start listening for the others.

    `on_peer` is called once per install heard from, on a thread of its own. What to do
    with one is the caller's business, which is the whole point of announcing.
    """
    if os.environ.get(OFF, "").strip():
        logger.info("%s is set; this install is not announcing itself", OFF)
        return
    try:
        _discovery.start(config, on_peer)
    except Exception as exc:  # noqa: BLE001 - discovery is not worth a failed startup
        logger.warning("Could not announce this install on the network: %r", exc)


def peers() -> list[Peer]:
    """Every install heard announcing itself, by name. Never this one."""
    return _discovery.peers()


def refresh(config: ConfigStore) -> None:
    """Announce again, because what this install says about itself has changed."""
    try:
        _discovery.refresh(config)
    except Exception as exc:  # noqa: BLE001 - a rename is not worth a failed write
        logger.warning("Could not refresh this install's announcement: %s", exc)


def stop() -> None:
    _discovery.stop()


def _as_peer(info: Any) -> Peer | None:
    """One resolved announcement, or None if it did not carry an identity.

    A record without an install id is not a VPinFE install as far as we are concerned,
    whatever it claims: the id is what every other surface keys on.
    """
    if info is None:
        return None
    said = {_text(key): _text(value) for key, value in (info.properties or {}).items()}
    install_id = said.get(ID, "").strip()
    addresses = [address for address in info.parsed_addresses() or []
                 if not _is_loopback(address)]
    if not install_id or not addresses:
        return None
    return Peer(
        install_id=install_id,
        display_name=said.get(NAME, "").strip(),
        features=tuple(name.strip() for name in said.get(FEATURES, "").split(",")
                       if name.strip()),
        address=addresses[0],
        port=int(info.port or 0),
        https=said.get(SCHEME, "").strip().lower() == "https",
    )


def _text(raw: object) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _routable_address() -> str:
    """The address this machine would be reached on, without sending anything, or "" when
    there is no route to anywhere else.

    Connecting a UDP socket only picks a route, so this asks the routing table which
    interface leaves the machine and reads the address off it. The alternative - looking
    the hostname up - fails outright on a Mac with no search domain.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Reserved for documentation, so it is guaranteed to be nobody.
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()
    return "" if _is_loopback(address) else address
