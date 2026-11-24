"""What the release picker lists, how its filter reads a release, and how it words one."""

from __future__ import annotations

import unittest

from manager.library import release_match


def _release(**said: object) -> dict[str, object]:
    return {"vps_file_id": "r", "comment": "", "version": "", "format": "", "authors": [],
            "features": [], **said}


class Filter(unittest.TestCase):
    def test_nothing_typed_keeps_every_release(self) -> None:
        self.assertTrue(release_match._matches(_release(), ""))

    def test_every_word_has_to_be_somewhere_in_the_release(self) -> None:
        one = _release(comment="Bally Special", version="1.2", authors=["Jane Roe"])

        self.assertTrue(release_match._matches(one, "special roe"))
        self.assertFalse(release_match._matches(one, "special smith"))

    def test_the_version_the_format_and_the_features_are_read_too(self) -> None:
        one = _release(version="2.0", format="FP", features=["VR"])

        self.assertTrue(release_match._matches(one, "2.0"))
        self.assertTrue(release_match._matches(one, "fp"))
        self.assertTrue(release_match._matches(one, "vr"))


class People(unittest.TestCase):
    def test_a_credit_naming_two_people_is_two_names(self) -> None:
        self.assertEqual({"g5k", "3rdaxis", "vpw"},
                         release_match._people(["G5K and 3rdAxis", "VPW"]))
        self.assertEqual({"g5k", "3rdaxis"}, release_match._people(["G5K & 3rdAxis"]))

    def test_a_blank_credit_names_no_one(self) -> None:
        self.assertEqual(set(), release_match._people(["", " , "]))


class Authors(unittest.TestCase):
    def test_a_name_both_credit_is_marked(self) -> None:
        line = release_match._authors_markup(["Jane Roe", "Sam Poe"], {"jane roe"})

        self.assertEqual('<span class="manager-have">Jane Roe</span>, Sam Poe', line)

    def test_a_name_is_escaped_where_it_is_drawn_as_markup(self) -> None:
        self.assertEqual("&lt;b&gt;", release_match._authors_markup(["<b>"], set()))

    def test_past_three_the_rest_are_counted(self) -> None:
        self.assertEqual("a, b, c +2", release_match._authors_markup(
            ["a", "b", "c", "d", "e"], set()))


class Facts(unittest.TestCase):
    def test_a_release_carrying_nothing_says_nothing(self) -> None:
        self.assertEqual("", release_match._facts(_release()))

    def test_features_are_listed_without_the_mod_tag(self) -> None:
        facts = release_match._facts(_release(features=["MOD", "VR", "SSF"]))

        self.assertIn("VR", facts)
        self.assertIn("SSF", facts)
        self.assertNotIn("MOD", facts)


class Count(unittest.TestCase):
    WORDS = release_match._RELEASES

    def test_a_list_with_nothing_in_it_counts_nothing(self) -> None:
        self.assertEqual("", release_match._count(self.WORDS, 0, 0, False, ""))

    def test_a_filter_says_how_many_of_how_many(self) -> None:
        self.assertEqual("2 of 12", release_match._count(self.WORDS, 2, 12, True, ""))

    def test_a_single_format_app_names_its_format(self) -> None:
        self.assertEqual("12 VPS releases for VPX",
                         release_match._count(self.WORDS, 12, 12, False, "VPX"))

    def test_an_app_playing_several_formats_names_none(self) -> None:
        self.assertEqual("12 VPS releases", release_match._count(self.WORDS, 12, 12, False, ""))


if __name__ == "__main__":
    unittest.main()
