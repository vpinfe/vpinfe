"""A picker's options keep the line that says what each one is for."""

from __future__ import annotations

import unittest

from nicegui import ui

from manager.kit import panel

SAID = {"Alpha": "the first one", "Beta": "the second"}


def _picker() -> panel.DescribedSelect:
    with ui.card():
        return panel.DescribedSelect({"a": "Alpha", "b": "Beta"}, value="a",
                                     label="View", describes=SAID)


def _help(picker: panel.DescribedSelect) -> dict[str, str]:
    return {str(one["label"]): str(one.get("help") or "")
            for one in picker._props["options"]}


class OptionsKeepTheirDescriptions(unittest.TestCase):

    def test_they_are_there_when_it_is_built(self):
        self.assertEqual(_help(_picker()), SAID)

    def test_they_survive_set_options(self):
        picker = _picker()
        picker.set_options({"a": "Alpha", "b": "Beta"}, value="b")
        self.assertEqual(_help(picker), SAID)

    def test_they_survive_an_update_from_anywhere_else(self):
        """The one that fails without the override."""
        picker = _picker()
        picker.update()
        picker.update()
        self.assertEqual(_help(picker), SAID)

    def test_an_option_nothing_describes_carries_an_empty_line(self):
        """Empty, not missing: the slot draws a tooltip on a truthy help."""
        picker = _picker()
        picker.describe_options({"Alpha": "only this one"})
        self.assertEqual(_help(picker), {"Alpha": "only this one", "Beta": ""})

    def test_the_option_slot_reads_the_field_the_payload_carries(self):
        """Renaming either half alone fails silently, so pin them together."""
        self.assertIn("props.opt.help", panel.DescribedSelect.SLOT)
        self.assertIn("manager-menu-tip", panel.DescribedSelect.SLOT)


if __name__ == "__main__":
    unittest.main()
