"""Turning the one mobile device a 2.x install could hold into a listed one.

Runs inside the conversion of a 2.x ini and consumes the two keys it reads, so a converted
file never converts again and a device somebody removes does not come back.
"""

from __future__ import annotations

import logging
from typing import Any

from common.config import config_schema
from common.config.config_access import cfg_get
from common.devices.device_registry import (
    DEFAULT_MOBILE_PORT,
    MOBILE_DEVICES_KEY,
    address_problem,
    mint_device_id,
)

logger = logging.getLogger("vpinfe.common.devices.device_migration")

MOBILE_SECTION = "vpxmobile"
MOBILE_DISPLAY_NAME = "VPX Mobile"

# The schema no longer declares these, so a file keeps whichever spelling it was written in.
_ADDRESS_KEYS = ("device_ip", "deviceip")
_PORT_KEYS = ("device_port", "deviceport")


def seed(store: Any) -> None:
    """Give a converted install its mobile device, from the two keys 2.x kept it in."""
    config = store.config
    address = _held(config, _ADDRESS_KEYS)
    if address and address_problem(_bare(address)):
        logger.warning("Not importing the mobile device at %r: it is not an address", address)
        address = ""
    elif address:
        address = _bare(address)
        store.set_list(MOBILE_DEVICES_KEY, [
            *store.get_list(MOBILE_DEVICES_KEY),
            {"id": mint_device_id(), "name": MOBILE_DISPLAY_NAME,
             "address": address, "port": _port(_held(config, _PORT_KEYS))}])
        logger.info("Imported the mobile device at %s", address)
    if config.has_section(MOBILE_SECTION):
        for key in (*_ADDRESS_KEYS, *_PORT_KEYS):
            config.remove_option(MOBILE_SECTION, key)


def _held(config: Any, keys: tuple[str, ...]) -> str:
    return next((value for key in keys
                 if (value := str(cfg_get(config, MOBILE_SECTION, key, "") or "").strip())), "")


def _bare(address: str) -> str:
    """The host alone: 2.x took the field as free text, so a scheme, a path or a port
    could be in it, and `mobile_devices` takes only the host."""
    host = address.split("://", 1)[-1].split("/", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _port(raw: str) -> int:
    try:
        port = int(raw or DEFAULT_MOBILE_PORT)
    except ValueError:
        return DEFAULT_MOBILE_PORT
    return port if 0 < port < 65536 else DEFAULT_MOBILE_PORT


config_schema.register_seed("mobile_devices", seed)
