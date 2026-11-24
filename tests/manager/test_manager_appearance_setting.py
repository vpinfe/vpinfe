"""The Manager's appearance is a setting, and four things have to agree about it.

The palette names the modes, the schema offers them, the catalog has a word for each and
the picker draws one tile per mode. Three of those four are silent when they drift: a
mode missing from `choices` is simply never offered, a mode the schema offers and the
palette does not know falls back to the default without a word, and a tile with no
catalog entry renders its own key. So the agreement is pinned rather than trusted.

`system` gets its own tests because it is the one mode with a different delivery. It has
no palette of its own - it ships both neutral ones behind media queries, and hands Quasar
its brand in CSS rather than through `ui.colors()`, which takes a single value.
"""

from __future__ import annotations

import configparser
import re
import unittest
from unittest import mock

from common.config import config_schema
from manager.kit import appearance, appearance_picker


class VocabularyTests(unittest.TestCase):
    def test_the_schema_offers_exactly_the_modes_that_exist(self) -> None:
        option = config_schema.option(appearance.MODE_SECTION, appearance.MODE_KEY)
        self.assertIsNotNone(option, "the setting is gone")
        assert option is not None
        self.assertEqual(sorted(option.choices), sorted(appearance.MODES))

    def test_the_default_is_the_palette_default(self) -> None:
        option = config_schema.option(appearance.MODE_SECTION, appearance.MODE_KEY)
        assert option is not None
        self.assertEqual(option.default, appearance.DEFAULT_MODE)
        self.assertIn(option.default, appearance.MODES)

    def test_every_mode_says_whether_quasar_goes_dark(self) -> None:
        """A mode missing here raises on the page rather than drawing wrong, but it
        raises inside the page function, which nicegui logs and moves past."""
        self.assertEqual(sorted(appearance.QUASAR_DARK), sorted(appearance.MODES))

    def test_every_mode_has_a_tile_and_a_word(self) -> None:
        self.assertEqual(sorted(appearance_picker.NAMES), sorted(appearance.MODES))
        for mode, key in appearance_picker.NAMES.items():
            with self.subTest(mode=mode):
                from common.i18n import t
                self.assertNotEqual(t(key), key, "no word for this mode")

    def test_the_tiles_are_offered_in_the_order_of_the_modes(self) -> None:
        """System first: it is where a new install starts."""
        offered = appearance_picker.choices()
        self.assertEqual([one[0] for one in offered], list(appearance.MODES))
        self.assertEqual(appearance.MODES[0], appearance.SYSTEM)

    def test_a_tile_carries_its_word_its_caption_and_its_picture(self) -> None:
        from common.i18n import t
        for mode, label, help_text, picture in appearance_picker.choices():
            with self.subTest(mode=mode):
                self.assertEqual(label, t(appearance_picker.NAMES[mode]))
                self.assertEqual(picture, appearance_picker.PICTURE_PREFIX + mode)
                if mode in appearance_picker.HELPS:
                    self.assertNotEqual(help_text, appearance_picker.HELPS[mode], "no caption word")
                else:
                    self.assertEqual(help_text, "")

    def test_the_two_ways_to_start_are_the_two_with_a_caption(self) -> None:
        self.assertEqual(sorted(appearance_picker.HELPS),
                         sorted([appearance.SYSTEM, appearance.DEFAULT_MODE]))

    def test_the_editor_the_setting_asks_for_is_one_that_exists(self) -> None:
        option = config_schema.option(appearance.MODE_SECTION, appearance.MODE_KEY)
        assert option is not None
        self.assertIn(option.editor, config_schema.EDITORS)


class StoredValueTests(unittest.TestCase):
    """What the config file says, and what is done with it when it says nonsense."""

    def _configured(self, stored: str) -> str:
        """Through a real parser rather than a stub answer. What is being checked is
        that the section and key this reads are the ones the setting is stored under,
        and a stub that answers whatever it is asked cannot tell."""
        parser = configparser.ConfigParser()
        parser.add_section(appearance.MODE_SECTION)
        parser.set(appearance.MODE_SECTION, appearance.MODE_KEY, stored)
        with mock.patch("common.config.paths.get_ini_config", return_value=parser):
            return appearance.configured_mode()

    def test_a_stored_mode_is_used(self) -> None:
        for mode in appearance.MODES:
            with self.subTest(mode=mode):
                self.assertEqual(self._configured(mode), mode)

    def test_anything_else_falls_back_rather_than_raising(self) -> None:
        """This reads a file somebody can edit. A mode nobody has is not a reason for
        the Manager not to draw."""
        for said in ("", "  ", "midnight", "None"):
            with self.subTest(said=said):
                self.assertEqual(self._configured(said), appearance.DEFAULT_MODE)

    def test_case_and_space_do_not_stop_a_match(self) -> None:
        self.assertEqual(self._configured("  Light "), "light")


class SystemModeTests(unittest.TestCase):
    """The one mode that is two, and the one that ships no values of its own."""

    def setUp(self) -> None:
        self.css = appearance.palette_css(appearance.SYSTEM)

    def test_it_ships_both_neutral_palettes_and_neither_is_bare(self) -> None:
        for prefers in ("light", "dark"):
            with self.subTest(prefers=prefers):
                self.assertIn(f"@media (prefers-color-scheme: {prefers})", self.css)
        for mode in appearance.SYSTEM_PALETTES.values():
            with self.subTest(mode=mode):
                # A value only that palette carries, so a block cannot pass by being
                # present and empty.
                self.assertIn(appearance._token("--surface-0", mode), self.css)

    def test_synthwave_is_not_something_an_operating_system_can_ask_for(self) -> None:
        self.assertNotIn(appearance.DEFAULT_MODE, appearance.SYSTEM_PALETTES.values())
        self.assertNotIn(appearance._token("--flair", appearance.DEFAULT_MODE), self.css)

    def test_the_sizes_are_stated_once_and_outside_the_queries(self) -> None:
        """No mode may change these, so a copy per branch would be two statements of a
        value that cannot differ - and the first one to drift would only show up in one
        colour scheme."""
        head = self.css.split("@media", 1)[0]
        self.assertIn("--fs-body", head)
        self.assertEqual(self.css.count("--fs-body:"), 1)

    def test_quasar_gets_its_brand_in_a_form_that_beats_an_inline_style(self) -> None:
        """Quasar writes its own brand onto the body at boot, from nicegui's defaults,
        before any page of ours runs. A `:root` rule loses to that however specific it
        is; a stylesheet `!important` is what outranks it."""
        for mode in appearance.SYSTEM_PALETTES.values():
            with self.subTest(mode=mode):
                want = appearance._token("--flair", mode)
                self.assertIn(f"--q-primary: {want} !important;", self.css)
        self.assertNotIn(":root {\n  --q-primary", self.css)

    def test_ui_colors_is_not_called_for_it(self) -> None:
        """It would pick one of the two and pin the framework to that one whatever the
        browser reports."""
        with mock.patch.object(appearance.ui, "colors") as colors:
            appearance.apply_colors(appearance.SYSTEM)
            colors.assert_not_called()
            appearance.apply_colors("light")
            colors.assert_called_once()


class SwatchTests(unittest.TestCase):
    """A swatch is drawn on a page already painted in one of these modes."""

    # What the picker reads off each palette to draw its miniature.
    SHOWN = ("--surface-0", "--nav-bg", "--surface-work", "--flair", "--ink", "--line")

    def test_nothing_a_swatch_paints_with_is_left_as_a_reference(self) -> None:
        """`var()` resolves against the palette in use, so a reference left in a swatch
        would make all four show the colours of the mode already on screen. Dark's rail
        is `var(--surface-1)` and Synthwave's is a gradient between three tokens, so
        this is the normal case rather than the odd one."""
        for mode in appearance.PALETTES:
            for name in self.SHOWN:
                with self.subTest(mode=mode, token=name):
                    self.assertNotIn("var(", appearance.token(name, mode))

    def test_a_reference_with_nothing_behind_it_falls_back(self) -> None:
        self.assertEqual(appearance._REFERENCE.sub(
            lambda m: "fell back", "var(--nothing, #fff)"), "fell back")

    def test_a_cycle_stops_rather_than_hanging(self) -> None:
        with mock.patch.dict(appearance.PALETTES,
                             {"loop": "  --a: var(--b);\n  --b: var(--a);\n"}):
            self.assertIsInstance(appearance.token("--a", "loop"), str)

    def test_the_swatch_tokens_are_ones_every_palette_declares(self) -> None:
        for mode in appearance.PALETTES:
            for name in self.SHOWN:
                with self.subTest(mode=mode, token=name):
                    appearance._token(name, mode)


class StructureTests(unittest.TestCase):
    def test_no_mode_moves_a_size(self) -> None:
        """Colour and effects are a mode's business. Density is this surface's, and a
        mode that changed the type scale is how a designed density becomes an
        accident."""
        sizes = re.compile(r"^\s*(--fs-[a-z0-9-]+|--panel-gutter|--target-min)\s*:",
                           re.MULTILINE)
        for mode, block in appearance.PALETTES.items():
            with self.subTest(mode=mode):
                self.assertEqual(sizes.findall(block), [])


if __name__ == "__main__":
    unittest.main()
