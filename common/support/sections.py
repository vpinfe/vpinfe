"""The sections a support summary is made of, and the one place that runs them.

A collector reads through readers that already exist, never raises, and never redacts:
the builder cleans every byte it collects, so a section added later cannot forget. A
failure is a `not_reported` with its reason, because a gap sends the reader looking for
the gap.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("vpinfe.common.support.sections")

OK = "ok"
NOT_REPORTED = "not_reported"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class SectionText:
    """What one collector found: Markdown lines, or why there are none."""

    status: str = OK
    lines: tuple[str, ...] = ()
    reason: str = ""

    @classmethod
    def ok(cls, lines: list[str] | tuple[str, ...]) -> SectionText:
        return cls(OK, tuple(lines))

    @classmethod
    def not_reported(cls, reason: str) -> SectionText:
        return cls(NOT_REPORTED, (), reason)

    @classmethod
    def not_applicable(cls, reason: str) -> SectionText:
        return cls(NOT_APPLICABLE, (), reason)


@dataclass(frozen=True)
class Section:
    """`order` places it in the summary. `platforms` are `sys.platform` prefixes, and an
    empty tuple means every platform."""

    id: str
    title: str
    order: int
    collect: Callable[[], SectionText]
    platforms: tuple[str, ...] = ()

    def applies(self, platform: str = sys.platform) -> bool:
        return not self.platforms or platform.startswith(self.platforms)


def collected(section: Section) -> SectionText:
    """The section's text, whatever the collector does."""
    if not section.applies():
        return SectionText.not_applicable("")
    try:
        return section.collect()
    except Exception as failure:  # noqa: BLE001 - a collector that fails is a gap, not an error
        logger.warning("Section %s could not be collected: %s", section.id, type(failure).__name__)
        logger.debug("Section %s failed", section.id, exc_info=True)
        return SectionText.not_reported(type(failure).__name__)


def rendered(section: Section, text: SectionText) -> str:
    """One section as it reads in the summary."""
    if text.status == OK:
        body = "\n".join(text.lines)
    elif text.status == NOT_APPLICABLE:
        body = f"Not applicable on this device: {text.reason}" if text.reason else "Not applicable."
    else:
        body = f"Not reported: {text.reason}" if text.reason else "Not reported."
    return f"## {section.title}\n\n{body}\n"
