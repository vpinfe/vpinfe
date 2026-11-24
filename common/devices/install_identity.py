"""Stable local identity for one installation: an opaque id on the state folder's registry.

Follows `common/games/game_identity.py`: opaque id, explicit minting, reading never
writes. Nothing resolves through `name`, so renaming an install is safe.
"""

from __future__ import annotations

import logging
import socket

from common.config.config_access import cfg_get, cfg_has, cfg_list
from common.config.config_store import ConfigStore
from common.ids import new_id

logger = logging.getLogger("vpinfe.common.devices.install_identity")

ID_SECTION = "device"

# What an install is *meant* to do, chosen deliberately. Roles said this before and said
# it badly: the two it had were trying to name both what an install does and what
# kind of thing it is, and neither word survived the second job.
LIBRARY = "library"
FRONTEND = "frontend"
DEVICES = "devices"
# A rollup over the other three rather than something this install does. It is on by
# default and switched off per install.
OVERVIEW = "overview"
FEATURES = (LIBRARY, FRONTEND, DEVICES, OVERVIEW)

# What a person calls each. The id names the thing and the name says what you do with
# it, which is why `devices` reads as Device Management.
LABELS = {
    LIBRARY: "device.feature.library",
    FRONTEND: "device.feature.frontend",
    DEVICES: "device.feature.devices",
    OVERVIEW: "device.feature.overview",
}

# What an install that never chose its own features is for, and what an unreadable
# setting falls back to. A feature added to FEATURES that has to be asked for stays out of
# here, or a typo would switch it on.
DEFAULT_FEATURES = (LIBRARY, FRONTEND, DEVICES, OVERVIEW)

# The feature every install has, holding the settings that belong to the machine as a
# whole - identity, features, network, logging. Synthesized on every read and never
# written, so it cannot be edited out of a list, and not in FEATURES because that is the
# set a person chooses from.
CORE = "core"


def install_id() -> str:
    """This install's id, or "" if it has not been minted. Never writes."""
    from common.devices.device_registry import get_device_registry

    return get_device_registry().self_id()


def mint_id() -> str:
    """A fresh identity. One generator for every id in the registry, so one minted for
    a phone and one an install minted for itself are indistinguishable - which is what
    lets the registry key on a single field."""
    return new_id()


def ensure_id() -> str:
    """This install's id, minting and saving one if it has none. An id that is not on
    disk is not an identity: the next start would mint another and become someone else."""
    from common.devices.device_registry import get_device_registry

    return get_device_registry().ensure_self()


def display_name(config: ConfigStore) -> str:
    """What to call this install, falling back to the hostname. Reading never writes the
    default down, so a renamed machine follows instead of keeping its first name."""
    configured = cfg_get(config, ID_SECTION, "name").strip()
    if configured:
        return configured
    return _hostname()


def _hostname() -> str:
    try:
        name = socket.gethostname().split(".")[0].strip()
    except Exception:
        name = ""
    return name or "VPinFE"


def features(config: ConfigStore) -> list[str]:
    """What this install is for, in a stable order, `core` first because every one has it.

    Unrecognized falls back to the defaults rather than to none: a typo must not decide
    this machine stopped launching games.

    Written down and left empty is a different answer and is taken at its word - an
    install that is not currently for anything. That is a legal state because `core`
    keeps the settings that switch a feature back on.
    """
    configured = [name.strip().lower()
                  for name in cfg_list(config, ID_SECTION, "features")]
    known = [name for name in FEATURES if name in configured]
    unknown = sorted(set(configured) - set(FEATURES) - {CORE})
    if unknown:
        logger.warning("Ignoring unknown install features: %s", ", ".join(unknown))
    if not known:
        # A setting that is there and empty says "none of them". One that is not there
        # has not said, and neither has one holding nothing we recognize.
        emptied = not configured and cfg_has(config, ID_SECTION, "features")
        known = [] if emptied else list(DEFAULT_FEATURES)
    return [CORE, *known]


def has_feature(config: ConfigStore, feature: str) -> bool:
    return feature in features(config)
