"""Whether a length is written in centimeters or inches, and the arithmetic between them."""

from __future__ import annotations

from typing import Any

from common import i18n

CM = "cm"
INCH = "in"
BY_REGION = "region"

INCH_REGIONS = frozenset({"US", "LR", "MM"})
_CM_PER_INCH = 2.54
# A length is read to a tenth: finer than that is a ruler nobody holds.
DECIMALS = 1


def resolved(chosen: Any, region: str | None = None) -> str:
    """`cm` or `in`: what was chosen, else what the region measures in."""
    said = str(chosen or "").strip().lower()
    if said in (CM, INCH):
        return said
    where = (i18n.region() if region is None else region).strip().upper()
    return INCH if where in INCH_REGIONS else CM


def shown(cm: float, units: str) -> float:
    return round(cm / _CM_PER_INCH if units == INCH else cm, DECIMALS)


def to_cm(value: float, units: str) -> float:
    return value * _CM_PER_INCH if units == INCH else value
