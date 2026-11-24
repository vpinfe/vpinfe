"""What the Manager remembers about how you left it, per browser.

Chrome only: which rail groups are open, which section you were on. Not a selection.
Never raises; absent a store, `get` answers the default.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from nicegui import app

PREFIX = "manager."


def get(key: str, default: Any = None) -> Any:
    with suppress(Exception):
        return app.storage.user.get(PREFIX + key, default)
    return default


def put(key: str, value: Any) -> None:
    with suppress(Exception):
        app.storage.user[PREFIX + key] = value
