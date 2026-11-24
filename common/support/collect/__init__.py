"""Every section the summary is made of. A new section is one module and one line here."""

from __future__ import annotations

from common.support.sections import Section

from . import browser, device, displays, extensions, health, launchers, locations, settings

SECTIONS: tuple[Section, ...] = tuple(sorted(
    (*device.SECTIONS, displays.SECTION, browser.SECTION, launchers.SECTION, settings.SECTION,
     extensions.SECTION, locations.SECTION, health.SECTION),
    key=lambda section: section.order))
