"""The layouts a theme supports and the picture it shows for each."""

from __future__ import annotations

import unittest

from common.online import theme_layouts, theme_source_checks
from common.online.themes import ThemeRegistry

_BASE = {"name": "n", "version": "1", "author": "a", "description": "d",
         "windows": ["playfield"]}


def _validate(**fields: object) -> None:
    ThemeRegistry._validate_manifest(ThemeRegistry.__new__(ThemeRegistry), "t",
                                     {**_BASE, **fields})


class WhatTheMapSays(unittest.TestCase):
    def test_the_keys_are_the_layouts_a_theme_is_made_for(self) -> None:
        manifest = {"layouts": {"desktop": "d.png", "cab": "c.png"}}

        self.assertEqual(("cab", "desktop"), theme_layouts.supported(manifest))
        self.assertEqual({"cab": "c.png", "desktop": "d.png"}, theme_layouts.pictures(manifest))

    def test_the_first_one_written_leads(self) -> None:
        self.assertEqual("d.png", theme_layouts.lead(
            {"layouts": {"desktop": "d.png", "cab": "c.png"}}))

    def test_a_layout_with_no_picture_of_its_own_shows_the_lead(self) -> None:
        manifest = {"layouts": {"desktop": "d.png"}}

        self.assertEqual("d.png", theme_layouts.picture(manifest, "cab"))

    def test_an_unknown_layout_never_leads(self) -> None:
        self.assertEqual("c.png", theme_layouts.lead({"layouts": {"tv": "t.png", "cab": "c.png"}}))
        self.assertEqual("", theme_layouts.lead({"layouts": {"tv": "t.png"}}))

    def test_nothing_is_supported_when_nothing_is_said(self) -> None:
        self.assertEqual((), theme_layouts.supported({}))
        self.assertEqual({}, theme_layouts.pictures({}))
        self.assertEqual("", theme_layouts.lead({}))


class AnOlderManifest(unittest.TestCase):
    def test_type_names_the_layouts_and_preview_image_is_the_lead(self) -> None:
        both = {"type": "both", "preview_image": "p.png"}

        self.assertEqual(("cab", "desktop"), theme_layouts.supported(both))
        self.assertEqual("p.png", theme_layouts.lead(both))
        self.assertEqual("p.png", theme_layouts.picture(both, "desktop"))

    def test_one_picture_between_two_layouts_is_neither_ones_own(self) -> None:
        self.assertEqual({}, theme_layouts.pictures({"type": "both", "preview_image": "p.png"}))
        self.assertEqual({"cab": "p.png"},
                         theme_layouts.pictures({"type": "cab", "preview_image": "p.png"}))

    def test_an_unknown_type_supports_nothing(self) -> None:
        self.assertEqual((), theme_layouts.supported({"type": "tv"}))


class Validation(unittest.TestCase):
    def test_layouts_stand_in_for_type_and_preview_image(self) -> None:
        _validate(layouts={"cab": "c.png"})

    def test_a_manifest_with_neither_is_missing_them(self) -> None:
        with self.assertRaises(theme_source_checks.ManifestError):
            _validate()

    def test_a_layout_that_does_not_exist_is_refused(self) -> None:
        with self.assertRaises(theme_source_checks.ManifestError):
            _validate(layouts={"tv": "t.png"})

    def test_the_source_is_told_it_is_the_layouts_and_not_the_type(self) -> None:
        for layouts in ({}, {"tv": "t.png"}, {"cab": ""}):
            with self.subTest(layouts=layouts):
                try:
                    _validate(layouts=layouts)
                except theme_source_checks.ManifestError as exc:
                    state, reason = theme_source_checks.classify(exc)
                self.assertEqual(theme_source_checks.NOT_A_THEME, state)
                self.assertIn("layout", reason)
                self.assertNotIn("type", reason)

    def test_a_layout_with_no_picture_is_refused(self) -> None:
        for layouts in ({}, {"cab": ""}, {"cab": 3}, ["cab"]):
            with self.subTest(layouts=layouts), \
                    self.assertRaises(theme_source_checks.ManifestError):
                _validate(layouts=layouts)


if __name__ == "__main__":
    unittest.main()
