"""How this install presents itself to everything else.

Its id, the name it goes by, the ports its other servers answer on, its registry row and
its mDNS announcement. The registry row and the announcement carry the same two facts, so
they are written together.
"""

from __future__ import annotations

import logging

from common.config.paths import get_ini_config
from common.devices import device_registry, discovery, install_identity
from common.devices.device_registry import get_device_registry
from common.devices.discovery import Peer

logger = logging.getLogger("vpinfe.common.devices.install_presence")


def record_self() -> None:
    """Put what this install currently calls itself into its own registry entry.

    Called again whenever the name changes, not only at startup. A registry entry is a
    copy of what an install reported, which for a remote device goes stale by design -
    but this install can say so the moment it is renamed, and every screen listing
    devices reads the registry rather than asking each one.
    """
    try:
        config = get_ini_config()
        get_device_registry().record(
            install_identity.install_id(),
            kind=device_registry.KIND_VPINFE,
            display_name=install_identity.display_name(config),
            features=install_identity.features(config),
        )
    except Exception as exc:
        # A registry that cannot be written must not stop the API starting: the entry is
        # a label, and the install is identified with or without it.
        logger.warning("Could not record this install in its own registry: %s", exc)
        return
    # The announcement carries the same two facts, so it is refreshed with them rather
    # than left saying what this install used to be called until the next restart.
    discovery.refresh(config)


def announce_on_the_network() -> None:
    """Say what this install is, and note the ones that say back."""
    try:
        config = get_ini_config()
    except Exception as exc:
        logger.warning("Could not read this install to announce it: %s", exc)
        return
    discovery.start(config, on_peer=_heard_from)


def announce_again() -> None:
    """Say it again with what is now true. HTTPS comes up after the first announcement."""
    try:
        discovery.refresh(get_ini_config())
    except Exception as exc:
        logger.warning("Could not read this install to announce it again: %s", exc)


def _heard_from(peer: Peer) -> None:
    """File an install this one heard from, when managing devices is its job.

    The feature is read per announcement rather than once at startup, so switching it on
    from the Manager does not need every other machine to restart before it is noticed.
    """
    try:
        config = get_ini_config()
        if not install_identity.has_feature(config, install_identity.DEVICES):
            return
        get_device_registry().record(
            peer.install_id,
            kind=device_registry.KIND_VPINFE,
            display_name=peer.display_name,
            features=peer.features,
            address=peer.address,
            port=peer.port,
            https=peer.https,
        )
    except Exception:
        logger.debug("Could not record the install that announced itself", exc_info=True)


def identity() -> dict:
    """Who is answering. A broken config must not take discovery down with it, so this
    degrades to the unidentified answer 2.x gave rather than raising."""
    try:
        config = get_ini_config()
        return {
            "install_id": install_identity.install_id(),
            "display_name": install_identity.display_name(config),
            "features": install_identity.features(config),
        }
    except Exception as exc:
        logger.warning("Could not read this install's identity: %s", exc)
        return {"install_id": "", "display_name": ""}
