"""The certificate another install showed when this one was paired with it.

Two installs on a home network usually have no certificate authority between them: the
other one serves HTTPS from a certificate it signed itself, which a browser-style check
refuses. So pairing takes the certificate the other install shows at that moment, keeps its
SHA-256 fingerprint beside the secrets, and every later call to that address and port must
meet the same certificate. One that differs raises `CertificateChangedError` and nothing is
sent; pairing again is the only way to trust the new one.

A certificate a certificate authority vouches for for the name dialled is not pinned at all,
so a renewal never reads as a change. Taking the fingerprint the first time is trust on
first use: it protects every call after pairing and gives nothing against a machine in the
middle of the pairing itself.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import ipaddress
import logging
import socket
import ssl
import warnings
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning

from common.i18n import t
from common.security import machine_secrets
from common.service_errors import CertificateChangedError

logger = logging.getLogger("vpinfe.common.security.pinned_certificates")

PINNED = "pinned_certificates"

# Passed as `trust`. UNVERIFIED is a call that carries no secret to an address nothing is
# pinned for. VERIFIED is the ordinary check, even where a pin is kept, for an address whose
# certificate an authority now vouches for.
UNVERIFIED = "unverified"
VERIFIED = "verified"

_MISMATCH = "Fingerprints did not match"
_CAPTURE_SECONDS = 5


def _key(host: str, port: int) -> str:
    return f"{host.lower()}:{port}"


def _held() -> dict[str, str]:
    held = machine_secrets.read().get(PINNED)
    return {str(key): str(value) for key, value in held.items()} if isinstance(held, dict) else {}


def pinned(host: str, port: int) -> str:
    """The fingerprint kept for this address and port, or ""."""
    return _held().get(_key(host, port), "")


def keep(host: str, port: int, fingerprint: str) -> None:
    machine_secrets.change(
        PINNED, lambda held: {**(held if isinstance(held, dict) else {}),
                              _key(host, port): fingerprint.lower()})


def forget(host: str, port: int) -> None:
    if not pinned(host, port):
        return
    machine_secrets.change(
        PINNED, lambda held: {key: value for key, value in (held or {}).items()
                              if key != _key(host, port)})


def _fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _name_to_send(host: str) -> str | None:
    """The name a client puts in the handshake, which a server may pick its certificate by:
    a host name, never an address."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def shown_by(host: str, port: int) -> str:
    """The fingerprint of the certificate that address and port show right now, taken
    without judging it. Raises OSError where nothing answers TLS there."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=_CAPTURE_SECONDS) as raw:
        with context.wrap_socket(raw, server_hostname=_name_to_send(host)) as secured:
            der = secured.getpeercert(binary_form=True)
    if not der:
        raise OSError(t("said.why.no_certificate_at", host=host))
    return _fingerprint(der)


def vouched_for(host: str, port: int) -> bool:
    """Whether a certificate authority vouches for what that address shows, for that
    name. Such a certificate renews on its own schedule, so it is checked the ordinary
    way on every call and never pinned."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=_CAPTURE_SECONDS) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ssl.SSLError):
        return False


def split(url: str) -> tuple[str, int] | None:
    """The host and port of an https URL, or None for anything else."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts.hostname, parts.port or 443


def trust_now(url: str) -> str:
    """What to trust of the install at `url` as it stands: "" for http, `VERIFIED` where an
    authority vouches for its certificate, else the fingerprint it shows. Raises OSError
    where nothing answers TLS there, or where the machine is offline."""
    found = split(url)
    if found is None:
        return ""
    # http_client imports this module, so the gate can only be reached from inside a call.
    from common.security import http_client
    http_client.require_reachable(url)
    return VERIFIED if vouched_for(*found) else shown_by(*found)


def adopt(url: str, trust: str) -> None:
    """Keep what `trust_now` found for `url`, or drop the pin where it found none needed."""
    found = split(url)
    if found is None or not trust:
        return
    if trust == VERIFIED:
        forget(*found)
    else:
        keep(*found, trust)


class _Pinned(HTTPAdapter):
    """Meets one certificate and no other, whatever name it carries."""

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        if self._fingerprint != UNVERIFIED:
            kwargs["assert_fingerprint"] = self._fingerprint
        super().init_poolmanager(*args, cert_reqs="CERT_NONE", **kwargs)


def _session(fingerprint: str) -> requests.Session:
    session = requests.Session()
    session.verify = False
    # An environment proxy or CA bundle would be applied beside the adapter's
    # settings, and a proxied connection gets a pool manager without the pin.
    session.trust_env = False
    session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
    session.mount("https://", _Pinned(fingerprint))
    return session


def sending(verb: Callable[..., requests.Response], url: str,
            trust: str | None = None) -> Callable[..., requests.Response]:
    """`verb` as it should be sent to `url`: itself for http and for an address with
    nothing pinned, else the same verb on a session that meets only the pinned
    certificate. `trust` is a fingerprint to meet instead of the one kept, or `UNVERIFIED`."""
    found = split(url)
    if found is None or trust == VERIFIED:
        return verb
    fingerprint = trust or pinned(*found)
    if not fingerprint:
        return verb

    def send(*args: Any, **kwargs: Any) -> requests.Response:
        # One session per request, as requests.post makes: a pooled connection the peer
        # has since closed would be reused for a POST, which is not retried.
        with _session(fingerprint) as session:
            pinned_verb = getattr(session, verb.__name__)
            try:
                with warnings.catch_warnings():
                    if fingerprint == UNVERIFIED:
                        warnings.simplefilter("ignore", InsecureRequestWarning)
                    return pinned_verb(*args, **kwargs)
            except requests.exceptions.SSLError as exc:
                if _MISMATCH in str(exc):
                    raise CertificateChangedError(found[0]) from exc
                raise

    return send
