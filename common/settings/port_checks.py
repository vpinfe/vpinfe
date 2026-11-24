"""Whether a port setting can be bound, asked when it is saved.

A port is read once, by the server that binds it, so a bad one is found at the next start
- by an install that does not come back. Answered on the machine the setting belongs to:
what is listening is a fact about that machine, so a caller names a setting and the install
holding it answers.
"""

from __future__ import annotations

import socket
from typing import Any

from common.config import config_schema
from common.config.paths import get_ini_config
from common.devices import serving
from common.i18n import t

LOWEST = 1
HIGHEST = 65535

_PROBE_SECONDS = 0.3


def ports() -> tuple[config_schema.ConfigOption, ...]:
    return tuple(option for option in config_schema.settable()
                 if option.unit == config_schema.UNIT_PORT)


def listening(port: int) -> bool:
    """Whether something on this machine answers on `port`."""
    try:
        with socket.create_connection(("localhost", port), timeout=_PROBE_SECONDS):
            return True
    except OSError:
        return False


def refused(wanted: dict[str, dict[str, Any]] | None) -> dict[str, str]:
    """Why each port in `wanted` cannot be bound, keyed `section.key`; nothing for one that
    can, or that is not changing.

    The ports this process already serves are not "in use": setting the one it is on, or
    moving to one another of its own ports is vacating, is not a clash with a stranger. A
    clash is two of the three settings holding one number once the patch is applied.
    """
    store = get_ini_config()
    changing: dict[str, Any] = {}
    for section, entries in (wanted or {}).items():
        for key, value in (entries or {}).items():
            option = config_schema.option(*config_schema.locate(section, key))
            if option is not None and option.unit == config_schema.UNIT_PORT:
                changing[f"{option.section}.{option.key}"] = value

    after = {f"{option.section}.{option.key}":
             changing.get(f"{option.section}.{option.key}",
                          store.value(option.section, option.key))
             for option in ports()}
    ours = serving.bound_ports()
    refusals: dict[str, str] = {}
    for key, raw in changing.items():
        if str(raw).strip() == str(store.value(*key.split(".", 1))):
            continue
        try:
            number = int(str(raw).strip())
        except ValueError:
            number = 0
        if not LOWEST <= number <= HIGHEST:
            refusals[key] = t("error.config.port_range", low=LOWEST, high=HIGHEST)
            continue
        other = next((one for one in ports() if f"{one.section}.{one.key}" != key
                      and str(after[f"{one.section}.{one.key}"]).strip() == str(number)),
                     None)
        if other is not None:
            refusals[key] = t("error.config.port_clash", port=number,
                              setting=other.label or other.key)
        elif number not in ours and listening(number):
            refusals[key] = t("error.config.port_taken", port=number)
    return refusals
