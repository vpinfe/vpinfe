"""What heads a group of settings, and where an ungrouped one is drawn."""

from __future__ import annotations

import unittest

from common.i18n import t
from manager.kit import panel
from manager.settings import settings as settings_page


def _option(key: str, group: str = "") -> dict:
    return {"key": key, "type": "bool", "default": "false", "group": group.lower(),
            "group_label": group, "label": key.title()}


def _headings(entries: list) -> list[str]:
    return [value for label, *rest in entries
            if label is panel.HEADING for value in rest[:1]]


def _rows(entries: list) -> list[str]:
    return [label for label, *_ in entries if isinstance(label, str)]


class OrderTests(unittest.TestCase):
    def test_the_ungrouped_come_last(self) -> None:
        ordered = settings_page.by_group(
            [_option("loose"), _option("named", "Named"), _option("also_loose")])

        self.assertEqual([one["key"] for one in ordered],
                         ["named", "loose", "also_loose"])

    def test_a_group_keeps_its_declaration_order(self) -> None:
        ordered = settings_page.by_group(
            [_option("first", "A"), _option("other", "B"), _option("second", "A")])

        self.assertEqual([one["key"] for one in ordered],
                         ["first", "second", "other"])


class HeadingTests(unittest.TestCase):
    def _entries(self, options: list[dict]) -> list:
        return settings_page.section_rows(
            object(), "media", options, {}, True, lambda: None)

    def test_the_remainder_is_headed_other(self) -> None:
        entries = self._entries([_option("named", "Named"), _option("loose")])

        self.assertEqual(_headings(entries),
                         ["Named", t("manager.settings.group_other")])

    def test_other_comes_after_the_named_group(self) -> None:
        entries = self._entries([_option("loose"), _option("named", "Named")])

        self.assertEqual(_rows(entries), ["Named", "Loose"])

    def test_a_section_naming_no_group_is_headed_by_the_page(self) -> None:
        """`page_head` draws the name above the panel, in the heading treatment rather
        than the cyan one a group inside a panel takes. Repeating it here would rank the
        page and its groups the same."""
        entries = self._entries([_option("one"), _option("two")])

        self.assertEqual(_headings(entries), [])


class PageNameTests(unittest.TestCase):
    """A page's name is written once, and its key spells the page it names.

    `vpinfe.json` stores `[<section>]`, the rail addresses `?page=<group>.<section>`, and
    the catalog answers `manager.settings.page_<section>`. A key typed by hand is free to
    name something else, and then the rail draws a word no file here contains.
    """

    def _pages(self) -> list[tuple[str, str]]:
        found = [(key, label) for _group, pages in settings_page.DEVICE_INDEX
                 for key, label, *_ in pages]
        found.append(settings_page.IDENTITY_PAGE[:2])
        return found

    def test_a_page_label_key_names_the_page(self) -> None:
        for key, label in self._pages():
            with self.subTest(page=key):
                self.assertEqual(label,
                                 f"manager.settings.page_{key.split('.', 1)[1]}")

    def test_every_page_name_is_in_the_catalog(self) -> None:
        for key, label in self._pages():
            with self.subTest(page=key):
                self.assertNotEqual(t(label), label)

    def test_a_declared_section_heading_names_its_section(self) -> None:
        """The same rule for the headings on a page that draws more than one section."""
        for section, label in settings_page.SECTION_LABELS.items():
            with self.subTest(section=section):
                self.assertEqual(
                    label, f"manager.settings.section_{section.rsplit('.', 1)[-1]}")
                self.assertNotEqual(t(label), label)


if __name__ == "__main__":
    unittest.main()
