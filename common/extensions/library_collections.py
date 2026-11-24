"""Collections, as an extension is allowed to reach them: `ctx.collections`.

Offered by name, under the scopes the API's own routes use. Criteria, exclusions and images
are not offered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .offered import Offered, Registry

COLLECTIONS: Registry = {}


def offer_collection(name: str, scope: str, run: Callable[..., Any]) -> None:
    """Core: let extensions call this on `ctx.collections`, for anything declaring `scope`."""
    COLLECTIONS[name] = (scope, run)


class ExtensionCollections(Offered):
    def __init__(self, name: str, scopes: Any) -> None:
        super().__init__(name, scopes, COLLECTIONS)
