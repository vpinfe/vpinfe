"""What VPS calls the program a table is for, in words a person would use."""

from __future__ import annotations

from collections.abc import Collection

from common import i18n


def name(token: str) -> str:
    """`FP` as Future Pinball. A token the catalog has no words for comes back as it came,
    because VPS may file a format this build has not heard of."""
    wanted = str(token or "").strip().upper()
    if not wanted:
        return ""
    return i18n.literal_or("", f"vps.format.{wanted}", fallback=wanted)[0]


def fits(filed: object, formats: Collection[str]) -> bool:
    """A release filed under no format fits any app, and an app declaring none fits all."""
    wanted = str(filed or "").strip().upper()
    return not formats or not wanted or wanted in {str(one).upper() for one in formats}
