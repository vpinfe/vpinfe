"""Encrypted connections: the two lists on Security, and the certificate VPinFE serves with.

Every edit is Protected, since each one decides who is spared a Confirm or what is believed
about where a request came from.  Reading is not: a list is not a secret.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from common.i18n import t
from common.security import encrypted_connections as store

from . import scopes
from .auth import caller_is_encrypted, requires
from .errors import InvalidRequestError
from .models import CertificateChoice, ConnectionEntry, EncryptedConnections

router = APIRouter(prefix="/auth/encrypted", tags=["auth"])

_REASONS = {
    store.BAD_ADDRESS: "error.encrypted.bad_address",
    store.BAD_RANGE: "error.encrypted.bad_range",
    store.TOO_WIDE: "error.encrypted.too_wide",
    store.NO_FILE: "error.encrypted.no_file",
    store.UNREADABLE: "error.encrypted.unreadable",
}


def _refused(error: store.EntryError) -> InvalidRequestError:
    return InvalidRequestError(t(_REASONS[error.reason]))


def _held(request: Request) -> EncryptedConnections:
    held = store.instance()
    certfile, keyfile = held.certificate()
    running = store.listening()
    return EncryptedConnections(
        proxies=held.proxies(), networks=held.networks(), certfile=certfile, keyfile=keyfile,
        https=running.https, problem=running.problem, encrypted=caller_is_encrypted(request))


@router.get("", summary="The encrypted connections set up", response_model=EncryptedConnections,
            dependencies=[requires(scopes.ACCESS_READ)])
def read(request: Request) -> EncryptedConnections:
    return _held(request)


@router.post("/proxies", summary="Trust a reverse proxy", response_model=EncryptedConnections,
             dependencies=[requires(scopes.PROTECTED)])
def add_proxy(body: ConnectionEntry, request: Request) -> EncryptedConnections:
    """Forwarding headers are believed from this address, and a request it says came over
    HTTPS counts as encrypted."""
    try:
        store.instance().add_proxy(body.value)
    except store.EntryError as error:
        raise _refused(error) from error
    return _held(request)


@router.delete("/proxies", summary="Stop trusting a reverse proxy",
               response_model=EncryptedConnections, dependencies=[requires(scopes.PROTECTED)])
def remove_proxy(address: str, request: Request) -> EncryptedConnections:
    store.instance().remove_proxy(address)
    return _held(request)


@router.post("/networks", summary="Add a trusted network", response_model=EncryptedConnections,
             dependencies=[requires(scopes.PROTECTED)])
def add_network(body: ConnectionEntry, request: Request) -> EncryptedConnections:
    """Connections from inside this range are encrypted, judged by the address they connect
    from and never by a header."""
    try:
        store.instance().add_network(body.value)
    except store.EntryError as error:
        raise _refused(error) from error
    return _held(request)


@router.post("/networks/tailscale", summary="Add Tailscale's range",
             response_model=EncryptedConnections, dependencies=[requires(scopes.PROTECTED)])
def add_tailscale(request: Request) -> EncryptedConnections:
    store.instance().add_tailscale()
    return _held(request)


@router.delete("/networks", summary="Remove a trusted network",
               response_model=EncryptedConnections, dependencies=[requires(scopes.PROTECTED)])
def remove_network(range: str, request: Request) -> EncryptedConnections:  # noqa: A002
    store.instance().remove_network(range)
    return _held(request)


@router.put("/certificate", summary="Choose the certificate to serve HTTPS with",
            response_model=EncryptedConnections, dependencies=[requires(scopes.PROTECTED)])
def choose_certificate(body: CertificateChoice, request: Request) -> EncryptedConnections:
    """Takes effect at the next start.  Refused unless the two files load together."""
    try:
        store.instance().set_certificate(body.certfile.strip(), body.keyfile.strip())
    except store.EntryError as error:
        raise _refused(error) from error
    return _held(request)


@router.delete("/certificate", summary="Serve plain HTTP again",
               response_model=EncryptedConnections, dependencies=[requires(scopes.PROTECTED)])
def clear_certificate(request: Request) -> EncryptedConnections:
    store.instance().clear_certificate()
    return _held(request)
