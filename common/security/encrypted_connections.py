"""Which connections count as encrypted, and the certificate VPinFE serves HTTPS with.

Two lists the Owner keeps. **Trusted Proxies** are single addresses of a reverse proxy: only
a request from one has its forwarding headers believed, and one that says HTTPS counts as
encrypted. **Trusted Networks** are address ranges that carry traffic inside a tunnel,
judged by the connection's own address and never by a header.

Kept beside the secrets, not in `vpinfe.json`, so `PUT /config`, a restore or a hand edit of
the settings cannot add an address. Nothing here knows about HTTP: `httpapi/encrypted.py`
carries it.
"""

from __future__ import annotations

import ipaddress
import logging
import ssl
import threading
from pathlib import Path
from typing import Any

from common.config import paths
from common.security import machine_secrets

logger = logging.getLogger("vpinfe.common.security.encrypted_connections")

TAILSCALE = "100.64.0.0/10"

# The widest range a person may type. Tailscale's is wider and is only added by `add_tailscale`.
WIDEST_IPV4 = 24
WIDEST_IPV6 = 64

# Why an entry was refused, for a surface to word.
BAD_ADDRESS = "bad_address"
BAD_RANGE = "bad_range"
TOO_WIDE = "too_wide"
UNREADABLE = "unreadable"
NO_FILE = "no_file"

_FORWARDED_FOR = "x-forwarded-for"
_FORWARDED_PROTO = "x-forwarded-proto"


class EntryError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def canonical(address: str) -> str:
    """An address in one spelling, so `::ffff:10.0.0.5` and `10.0.0.5` compare equal."""
    text = address.split("%", 1)[0].strip().lower()
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    return str(parsed)


def _address(text: str) -> str:
    try:
        found = ipaddress.ip_address(canonical(text))
    except ValueError as error:
        raise EntryError(BAD_ADDRESS) from error
    if found.is_unspecified or found.is_multicast:
        raise EntryError(BAD_ADDRESS)
    return str(found)


def _network(text: str, *, any_width: bool = False) -> str:
    try:
        found = ipaddress.ip_network(text.split("%", 1)[0].strip(), strict=False)
    except ValueError as error:
        raise EntryError(BAD_RANGE) from error
    if isinstance(found, ipaddress.IPv6Network) and found.network_address.ipv4_mapped:
        found = ipaddress.ip_network(
            f"{found.network_address.ipv4_mapped}/{max(found.prefixlen - 96, 0)}")
    widest = 1 if any_width else (WIDEST_IPV4 if found.version == 4 else WIDEST_IPV6)
    if found.prefixlen < widest:
        raise EntryError(TOO_WIDE)
    return str(found)


def unreadable_reason(certfile: str, keyfile: str) -> str:
    """Why this certificate and key cannot be served, or "" where they can."""
    for name in (certfile, keyfile):
        if not name or not Path(name).is_file():
            return NO_FILE
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(
            certfile, keyfile, password=lambda: b"")
    except (OSError, ssl.SSLError):
        return UNREADABLE
    return ""


def certificate_names(certfile: str) -> list[str]:
    """The names and addresses the certificate vouches for, wildcards left out, or none
    where the file cannot be read. A browser checks the host it was given against these, so
    an address built from any other name opens with a warning.

    Reads through `ssl._ssl._test_decode_cert`, the one decoder in the standard library; it
    is private, so a Python without it answers none and the caller falls back to the host
    names the machine has.
    """
    try:
        decoded = ssl._ssl._test_decode_cert(certfile)  # type: ignore[attr-defined]
    except (AttributeError, OSError, ssl.SSLError, ValueError):
        return []
    found: list[str] = []
    for _, name in decoded.get("subjectAltName", ()):
        if name and not name.startswith("*") and name not in found:
            found.append(name)
    return found


class Connections:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._proxies: list[str] = []
        self._networks: list[str] = []
        self._certfile = ""
        self._keyfile = ""
        self._loaded = False
        self._parsed: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    def _file(self) -> Path:
        return self._path or paths.CONNECTIONS_PATH

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        held = machine_secrets.read_private(self._file())
        for key, check, into in (("proxies", _address, self._proxies),
                                 ("networks", lambda text: _network(text, any_width=True),
                                  self._networks)):
            for entry in held.get(key) or []:
                try:
                    into.append(check(str(entry)))
                except EntryError:
                    continue
        self._certfile = str(held.get("certfile") or "")
        self._keyfile = str(held.get("keyfile") or "")
        self._reparse()

    def _reparse(self) -> None:
        self._parsed = [ipaddress.ip_network(one) for one in self._networks]

    def _save(self) -> None:
        held: dict[str, Any] = {"proxies": self._proxies, "networks": self._networks,
                                "certfile": self._certfile, "keyfile": self._keyfile}
        paths.ensure_config_dir()
        machine_secrets.write_private(self._file(), held)

    def proxies(self) -> list[str]:
        with self._lock:
            self._load()
            return list(self._proxies)

    def networks(self) -> list[str]:
        with self._lock:
            self._load()
            return list(self._networks)

    def certificate(self) -> tuple[str, str]:
        """The certificate and key files chosen, or two empty strings."""
        with self._lock:
            self._load()
            return self._certfile, self._keyfile

    def _add(self, held: list[str], entry: str) -> str:
        if entry not in held:
            held.append(entry)
            self._reparse()
            self._save()
        return entry

    def add_proxy(self, text: str) -> str:
        entry = _address(text)
        with self._lock:
            self._load()
            return self._add(self._proxies, entry)

    def add_network(self, text: str) -> str:
        return self._add_network(_network(text))

    def add_tailscale(self) -> str:
        return self._add_network(_network(TAILSCALE, any_width=True))

    def _add_network(self, entry: str) -> str:
        with self._lock:
            self._load()
            return self._add(self._networks, entry)

    def _remove(self, held: list[str], entry: str) -> bool:
        if entry not in held:
            return False
        held.remove(entry)
        self._reparse()
        self._save()
        return True

    def remove_proxy(self, text: str) -> bool:
        with self._lock:
            self._load()
            return self._remove(self._proxies, canonical(text))

    def remove_network(self, text: str) -> bool:
        with self._lock:
            self._load()
            try:
                return self._remove(self._networks, _network(text, any_width=True))
            except EntryError:
                return False

    def set_certificate(self, certfile: str, keyfile: str) -> None:
        """Choose the files. Refused unless they load together, so a typo never turns into
        a start that cannot serve."""
        reason = unreadable_reason(certfile, keyfile)
        if reason:
            raise EntryError(reason)
        with self._lock:
            self._load()
            self._certfile, self._keyfile = certfile, keyfile
            self._save()

    def clear_certificate(self) -> None:
        with self._lock:
            self._load()
            self._certfile = self._keyfile = ""
            self._save()

    def is_proxy(self, peer: str | None) -> bool:
        if not peer:
            return False
        with self._lock:
            self._load()
            return canonical(peer) in self._proxies

    def in_encrypted_network(self, peer: str | None) -> bool:
        if not peer:
            return False
        with self._lock:
            self._load()
            try:
                found = ipaddress.ip_address(canonical(peer))
            except ValueError:
                return False
            return any(found.version == one.version and found in one for one in self._parsed)


_instance = Connections()


def instance() -> Connections:
    return _instance


def use(connections: Connections) -> None:
    """Swap the process's lists. For tests."""
    global _instance
    _instance = connections


class Listening:
    """Whether this run answers HTTPS, and why not where a certificate was chosen and could
    not be used."""

    def __init__(self) -> None:
        self.https = False
        self.problem = ""
        self.decided = False


_listening = Listening()


def listening() -> Listening:
    return _listening


def https_started() -> None:
    _listening.https, _listening.problem, _listening.decided = True, "", True


def https_failed(reason: str) -> None:
    _listening.https, _listening.problem, _listening.decided = False, reason, True


def forget_listening() -> None:
    """Nothing has been started. For a test."""
    _listening.https, _listening.problem, _listening.decided = False, "", False


def serves_https() -> bool:
    """Whether this run answers HTTPS, or before the run has decided, whether it will.
    The first announcement goes out before the port starts, and an install that announced
    plain HTTP and then HTTPS would make every peer drop its pairing."""
    if _listening.decided:
        return _listening.https
    certfile, keyfile = instance().certificate()
    return bool(certfile) and not unreadable_reason(certfile, keyfile)


def _header(headers: Any, name: str) -> str:
    """Every line of a header joined as one chain, so a proxy that adds a line of its own
    instead of appending to the client's is read from the right like one that appends."""
    many = getattr(headers, "getlist", None)
    return ", ".join(many(name)) if many else str(headers.get(name, ""))


def forwarded_for(peer: str | None, headers: Any) -> str:
    """The client a listed proxy says it forwarded for, or the peer itself where it is not
    one or says nothing.

    The chain is read from the right: each hop is believed only while the address before it
    is a listed proxy, so a client that wrote its own entry at the left is never reached."""
    address = canonical(peer or "")
    if not address or not _instance.is_proxy(address):
        return address
    chain = [canonical(part) for part in _header(headers, _FORWARDED_FOR).split(",")
             if part.strip()]
    while chain:
        hop = chain.pop()
        if not _instance.is_proxy(hop):
            return hop
    return address


def said_https(peer: str | None, headers: Any) -> bool:
    """Whether a listed proxy says the request reached it over HTTPS."""
    if not _instance.is_proxy(peer):
        return False
    return _header(headers, _FORWARDED_PROTO).split(",")[-1].strip().lower() == "https"


def is_encrypted(peer: str | None, headers: Any, scheme: str = "http") -> bool:
    """Whether the request arrived inside an encrypted connection: this server's own TLS, a
    listed proxy that says it terminated one, or a peer inside a Trusted Network."""
    if scheme == "https" or said_https(peer, headers):
        return True
    return not _instance.is_proxy(peer) and _instance.in_encrypted_network(peer)
