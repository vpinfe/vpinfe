"""Settings core used to hold, handed to the extension that owns them now.

A one-time move that core performs while it converts a 2.x config, not a door an
extension reaches through. Core names which of its own settings belong to which
extension; nothing here lets an extension ask for a setting, which is what keeps
`config:own` from being decorative.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from common.config import config_schema

from .store import ExtensionStore

logger = logging.getLogger("vpinfe.common.extensions.handover")

# {extension: {its setting: (our section, our key)}}. Core's list, in core, because
# deciding it anywhere else would be the door this exists not to be.
HANDOVER = {
    "vpinplay": {
        "endpoint": ("vpinplay", "api_endpoint"),
        "user_id": ("vpinplay", "user_id"),
        "machine_id": ("vpinplay", "machine_id"),
    },
}


def _default(section: str, key: str) -> str:
    """What the schema says this setting is when nobody has said."""
    found = config_schema.option(section, key)
    return str(getattr(found, "default", "") or "").strip() if found else ""


def seed(store: Any) -> None:
    """Move each extension's settings out of the 2.x file being converted.

    Only values somebody actually set, which is not the same as values that are there:
    a 2.x file is written with every default in it, so "not empty" would hand over the
    whole section. A default handed over pins the extension to core's default as it was
    on the day it was installed, and the two drift apart with nobody having chosen
    either. What is read is removed, so the file no longer answers for it.
    """
    config = store.config
    extensions = ExtensionStore(Path(store.json_path).parent)
    moved = 0
    read: list[tuple[str, str]] = []
    for extension, mapping in HANDOVER.items():
        held = extensions.settings(extension)
        for setting, (section, key) in mapping.items():
            if not config.has_option(section, key):
                continue
            value = config.get(section, key).strip()
            read.append((section, key))
            if setting in held or not value or value == _default(section, key):
                continue
            extensions.set_setting(extension, setting, value)
            moved += 1
    # After every write: a failed one leaves the 2.x value where the conversion restores it.
    for section, key in read:
        config.remove_option(section, key)
    if moved:
        logger.info("Handed %d setting(s) to the extensions that own them now", moved)


config_schema.register_seed("extension_handover", seed)
