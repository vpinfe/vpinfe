"""Where Things Live: the folders and files this install reads, as About has them."""

from __future__ import annotations

from common.host import about
from common.support.sections import Section, SectionText

LOCATIONS_GROUP = 3


def collect() -> SectionText:
    group = about.details()[LOCATIONS_GROUP]
    return SectionText.ok([f"- **{label}:** {value}" for label, value in group["facts"]])


SECTION = Section("locations", "Where Things Live", 80, collect)
