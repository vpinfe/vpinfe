"""What a settings group does beyond the schema's own rows.

A group that has rows depending on other rows implements these four hooks and is handed
to `section_rows`; the rows it does not name pass through untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class Linked(Protocol):
    def under_heading(self, group: str) -> list[tuple[Any, Any]]:
        """Rows drawn straight under the group's heading."""
        ...

    def saves(self, key: str, save: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """`save`, kept in step with the rows that depend on this one."""
        ...

    def draws(self, key: str, control: Callable[[], None]) -> Callable[[], None]:
        """The control, drawn so that it can be disabled in place."""
        ...

    def under(self, key: str) -> list[tuple[Any, Any]]:
        """Rows drawn under a setting's own."""
        ...
