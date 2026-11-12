"""What this install collects: media kinds, asset kinds, artwork catalogs, quiet checks.

A view over the `library` section of the settings file. The Manager writes through
`PUT /config`; `set` here writes the file directly and announces nothing, which is for
tests and for code that is not itself a settings change. Each list holds what is
switched off, so empty means everything and a kind or a source added in a later
version arrives switched on.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config

SECTION = "library"

KEYS = ("hidden_media_kinds", "hidden_asset_kinds", "hidden_sources", "hidden_checks")

# The choices of how a new file of a kind is spelled. Not in KEYS: those are lists.
NAME_KEYS = ("flyer_name", "instruction_card_name")


def _as_list(raw: Any) -> list[str]:
    """However the value arrives - a list, or the comma string a parser holds."""
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(item).strip() for item in (raw or []) if str(item).strip()]


class LibraryPolicy:
    """The four lists, read from and written to the settings store."""

    def __init__(self, store: ConfigStore | None = None) -> None:
        self._store = store
        self._lock = threading.RLock()

    def _config(self) -> ConfigStore:
        return self._store if self._store is not None else get_ini_config()

    def values(self) -> dict[str, list[str]]:
        config = self._config()
        return {key: _as_list(config.value(SECTION, key)) for key in KEYS}

    def get(self, key: str) -> list[str]:
        if key not in KEYS:
            return []
        return _as_list(self._config().value(SECTION, key))

    def set(self, key: str, value: Any) -> list[str]:
        """Stores `value`, empty included: empty is how "collect it all" is said."""
        if key not in KEYS:
            raise ValueError(f"not a library policy: {key}")
        with self._lock:
            config = self._config()
            config.set_value(SECTION, key, _as_list(value))
            config.save()
            return self.get(key)


def media_token(kind: str, values: Mapping[str, Any] | None = None) -> str | None:
    """The token a new file of this kind is spelled with, for the kinds that have a choice
    and None for the rest. `values` is the policy as the API answers it, and this
    install's own where left out. An unknown or missing choice is the default."""
    from common.media.media_specs import NAMED_TOKENS

    tokens = NAMED_TOKENS.get(kind)
    if tokens is None:
        return None
    held = values if values is not None else _config_values()
    chosen = str(held.get(f"{kind}_name") or "").strip()
    return tokens.get(chosen) or next(iter(tokens.values()))


def _config_values() -> dict[str, Any]:
    config = get_library_policy()._config()
    return {key: config.value(SECTION, key) for key in NAME_KEYS}


def kept_kinds(values: Mapping[str, Any] | None = None) -> dict[str, set[str]]:
    """Which kinds of file a library collects: what to enumerate, never what exists.

    What this build knows less what is hidden, the direction that survives an upgrade: a
    kind added later is in nobody's hidden list, so it arrives switched on. `values` is
    the policy as the API answers it, and this install's own where left out. Assets are
    named both ways: the registry's kinds and the lens's `alt_color` and `alt_sound`.
    """
    # Imported here: the asset registry reads settings, which read this.
    from common.games.asset_registry import ASSET_SPECS, lens_kind
    from common.media.media_specs import media_label_map

    held = values if values is not None else get_library_policy().values()
    assets = {spec.kind for spec in ASSET_SPECS} - set(_as_list(held.get("hidden_asset_kinds")))
    return {
        "media": set(media_label_map()) - set(_as_list(held.get("hidden_media_kinds"))),
        "asset": assets | {lens_kind(kind) for kind in assets},
    }


_policy: LibraryPolicy | None = None


def get_library_policy() -> LibraryPolicy:
    global _policy
    if _policy is None:
        _policy = LibraryPolicy()
    return _policy


def reset_for_tests(store: ConfigStore | None = None) -> LibraryPolicy:
    global _policy
    _policy = LibraryPolicy(store)
    return _policy
