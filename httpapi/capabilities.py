"""Capability declarations for API discovery.

So `GET /api/v1` describes the running instance, not a fixed list in a doc.
Nothing is declared until the endpoints backing it land. See docs/http-api.md.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from common.devices import install_identity
from common.i18n import t

logger = logging.getLogger("vpinfe.httpapi.capabilities")

# Which feature switches a capability on. One value or none, not a list: residency was
# a list of places, which only meant something while `hub` and `device` were places.
# Infrastructure belongs to no feature - `events` and `jobs` are there wherever the API
# is - so this is nullable, and that is what ruled out simply rewriting residency in
# feature words.
FEATURES = frozenset(install_identity.FEATURES)


@dataclass(frozen=True)
class Capability:
    """One declared capability. `is_available` runs per request, not at import, so the
    answer stays honest after a config change; return (False, reason), not bare False."""

    name: str
    feature: str | None = None
    description: str = ""
    is_available: Callable[[], bool | tuple[bool, str]] | None = None

    def __post_init__(self) -> None:
        if self.feature is not None and self.feature not in FEATURES:
            raise ValueError(f"{self.name}: unknown feature {self.feature!r}, "
                             f"expected one of {sorted(FEATURES)} or None")

    def resolve(self) -> dict[str, Any]:
        available, reason = True, None
        if self.is_available is not None:
            try:
                result = self.is_available()
            except Exception:  # a broken probe must not break discovery
                logger.exception("Checking whether %s is available failed", self.name)
                available, reason = False, t("error.capabilities.check_failed")
            else:
                if isinstance(result, tuple):
                    available, reason = result[0], result[1]
                else:
                    available = bool(result)
        return {
            "name": self.name,
            "feature": self.feature,
            "description": self.description,
            "available": available,
            "reason": reason,
        }


_CAPABILITIES: dict[str, Capability] = {}


def declare(capability: Capability) -> Capability:
    """Register a capability for discovery. Re-declaring a name replaces it."""
    _CAPABILITIES[capability.name] = capability
    return capability


def _enabled_features() -> frozenset[str]:
    """What this install is meant to do. Read per request, not at import, so switching a
    feature off does not need a restart to be believed."""
    try:
        from common.config.paths import get_ini_config

        return frozenset(install_identity.features(get_ini_config()))
    except Exception:  # a config that cannot be read must not empty discovery
        return frozenset(install_identity.FEATURES)


def declared() -> list[dict[str, Any]]:
    """Every declared capability, sorted by name for a stable payload."""
    on = _enabled_features()
    # A capability whose feature is switched off is not served at all - not listed as
    # unavailable. "I do not do that" and "I do that and it is broken" are different
    # answers, and only the second is a problem somebody should act on.
    return [_CAPABILITIES[name].resolve() for name in sorted(_CAPABILITIES)
            if _CAPABILITIES[name].feature is None
            or _CAPABILITIES[name].feature in on]


def clear() -> None:
    """Drop all declarations. For tests."""
    _CAPABILITIES.clear()
