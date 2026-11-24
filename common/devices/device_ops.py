"""The devices this install knows about, and what it can ask them.

A device is another machine: a second VPinFE install, or a phone running VPX Mobile. The
registry is a copy of what each one reported, which goes stale by design - so reachability
is asked rather than stored, and what a phone is carrying is asked of the phone.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from common import service_errors
from common.devices import device_client, device_registry, discovery, install_identity
from common.devices.device_registry import Device, get_device_registry
from common.i18n import t

logger = logging.getLogger("vpinfe.common.devices.device_ops")


def _resource(device: Device) -> dict:
    return device.as_dict() | {"links": {"self": f"/api/v1/devices/{device.device_id}"}}


def device_or_refuse(device_id: str) -> Device:
    found = get_device_registry().get(device_id)
    if found is None:
        raise service_errors.NotFoundError(
            t("error.devices.not_found", device_id=(device_id)))
    return found


def mobile_or_refuse(device_id: str) -> Device:
    """The device a phone operation is about, refusing anything that is not a phone.

    An install carries its own library and its own API; asking one what folders it holds
    over a protocol only VPX Mobile speaks would be a question it cannot answer.
    """
    found = device_or_refuse(device_id)
    if found.kind != device_registry.KIND_VPX_MOBILE:
        raise service_errors.RefusedError(
            t("error.devices.wrong_kind",
              device=(found.display_name or device_id)))
    if not found.address or not found.port:
        raise service_errors.RefusedError(
            t("error.devices.no_address", device=(found.display_name or device_id)))
    return found


def listing() -> dict[str, Any]:
    devices = get_device_registry().devices()
    return {"count": len(devices), "devices": [_resource(one) for one in devices]}


def resource(device_id: str) -> dict[str, Any]:
    return _resource(device_or_refuse(device_id))


def discovered() -> dict[str, Any]:
    """What mDNS has heard, as it stands.

    Not the registry: these are announcements, so nothing here has been recorded or
    decided about. It is what a picker offers and a person confirms.
    """
    found = discovery.peers()
    return {"count": len(found),
            "installs": [{"install_id": peer.install_id,
                          "display_name": peer.display_name,
                          "features": list(peer.features),
                          "address": peer.address, "port": peer.port,
                          "https": peer.https, "url": peer.url} for peer in found]}


def announce(device_id: str, kind: str, display_name: str, features: Iterable[str],
             port: int, *, heard_from: str = "") -> dict[str, Any]:
    """Idempotent by `device_id`: announcing twice is one device, heard from twice.

    The address kept is `heard_from` - the socket's address - never one the caller named: a
    device behind a router does not know how it is reached, and a caller that could name its
    own address could name someone else's.

    A VPX Mobile device is not announced. It cannot call, so a person lists it in
    `mobile_devices`, and the address there is the only one there is.
    """
    device_id = (device_id or "").strip()
    if kind == device_registry.KIND_VPX_MOBILE:
        raise service_errors.RefusedError(t("error.devices.mobile_in_config"))
    if not device_id:
        raise service_errors.RefusedError(t("error.devices.id_needed"))

    try:
        device = get_device_registry().record(
            device_id,
            kind=kind,
            display_name=(display_name or "").strip(),
            features=tuple(features),
            address=heard_from or "",
            # Declared, unlike the address: the socket says where a request came from, never
            # what that machine listens on. A device that does not say stays at 0 - what it
            # was told is read, and nobody is dialled.
            port=port,
        )
    except ValueError as exc:
        raise service_errors.RefusedError(t("error.devices.mobile_in_config")) from exc
    if device is None:
        raise service_errors.RefusedError(t("error.devices.id_needed"))
    return _resource(device)


def probe_one(device: Device) -> dict[str, Any]:
    """Dial one device, and record it as reachable if it answered.

    One at a time and off the caller's loop: a machine that is off costs its own short
    timeout and nobody else's answer.
    """
    registry = get_device_registry()
    local_id = install_identity.install_id()
    client = device_client.for_device(device.as_dict(), local_id)
    found = device_client.probe(client)
    if found.get("state") == device_client.ANSWERING:
        held = registry.record_reachable(device.device_id,
                                         browser=_browser_of(client, device, found))
        if held is not None and held.browser:
            found = {**found, "browser": held.browser}
    return {"device_id": device.device_id, **found}


def _browser_of(client: Any, device: Device, found: dict[str, Any]) -> dict[str, Any] | None:
    """What an answering device's frontend browser plays, `{}` where it runs no frontend,
    None where it did not say.

    This install is never asked here: its frontend writes its own entry as it reports.
    """
    ask = getattr(client, "frontend_browser", None)
    if ask is None:
        return None
    if install_identity.FRONTEND not in (found.get("features") or []):
        return {}
    try:
        return device_registry.browser_said(ask()) or None
    except device_client.NotPairedError:
        return None
    except Exception:  # noqa: BLE001 - one unanswered question keeps the last answer
        logger.debug("Could not ask %s what its browser plays", device.device_id,
                     exc_info=True)
        return None


def all_devices() -> list:
    return get_device_registry().devices()


def forget(device_id: str) -> None:
    """Forgetting one that is still running only means it announces itself again."""
    registry = get_device_registry()
    wanted = (device_id or "").strip()
    if wanted and wanted == registry.self_id():
        raise service_errors.RefusedError(t("error.devices.forget_self"))
    held = registry.get(wanted)
    if held is not None and held.kind == device_registry.KIND_VPX_MOBILE:
        raise service_errors.RefusedError(t("error.devices.forget_mobile"))
    if not registry.forget(device_id):
        raise service_errors.NotFoundError(
            t("error.devices.not_found", device_id=(device_id)))
