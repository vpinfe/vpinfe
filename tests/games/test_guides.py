"""Guides: what the catalog offers, what a 2.x file carries in, what a theme still reads."""

from __future__ import annotations

import unittest

from common.games.game_metadata import contract_1_tutorial, vps_details_differ
from common.games.info_codec import encode
from common.games.info_file import GUIDES_KEY, PINBALL_PRIMER_PREFIX, guides_from_vps, record_of
from common.games.info_migration import migrate


def _entry(*tutorials: dict) -> dict:
    return {"id": "abc", "name": "Example", "tutorialFiles": list(tutorials)}


class FromTheCatalog(unittest.TestCase):
    def test_a_nested_urls_list_is_one_guide_each(self) -> None:
        found = guides_from_vps(_entry(
            {"title": "Two", "urls": [{"url": "https://a/"}, {"url": "https://b/"}]}))
        self.assertEqual(["https://a/", "https://b/"], [one["url"] for one in found])

    def test_a_record_with_only_a_video_is_kept(self) -> None:
        found = guides_from_vps(_entry({"title": "V", "url": None, "youtubeId": "xyz"}))
        self.assertEqual([("", "xyz")],
                         [(one["url"], one["youtube_id"]) for one in found])

    def test_a_record_naming_nothing_is_dropped(self) -> None:
        self.assertEqual([], guides_from_vps(_entry({"title": "Nothing"})))

    def test_authors_survive(self) -> None:
        found = guides_from_vps(_entry(
            {"title": "T", "url": "https://a/", "authors": ["Kongedam", "Odradek"]}))
        self.assertEqual(["Kongedam", "Odradek"], found[0]["authors"])

    def test_nothing_at_all_is_not_an_error(self) -> None:
        self.assertEqual([], guides_from_vps(None))
        self.assertEqual([], guides_from_vps({}))


class FromA2xFile(unittest.TestCase):
    def test_the_old_key_becomes_a_guide(self) -> None:
        out = migrate({"Info": {"PinballPrimerTut": "https://pinballprimer.github.io/x"},
                       "VPinFE": {"altvpsid": "1"}})
        self.assertNotIn("PinballPrimerTut", out["Info"])
        self.assertEqual([("tutorial", "https://pinballprimer.github.io/x")],
                         [(one["kind"], one["url"]) for one in out[GUIDES_KEY]])

    def test_running_it_twice_adds_nothing(self) -> None:
        once = migrate({"Info": {"PinballPrimerTut": "https://pinballprimer.github.io/x"},
                        "VPinFE": {"altvpsid": "1"}})
        self.assertEqual(once[GUIDES_KEY], record_of(encode(once))[GUIDES_KEY])

    def test_a_file_with_no_tutorial_grows_no_block(self) -> None:
        self.assertNotIn(GUIDES_KEY, migrate({"Info": {}, "VPinFE": {"altvpsid": "1"}}))


class WhatAThemeReads(unittest.TestCase):
    """`entry.tutorial` is proxied by the frontend, and that proxy allows one host."""

    def test_the_primer_guide_is_the_contract_1_value(self) -> None:
        config = {GUIDES_KEY: [
            {"kind": "tutorial", "url": "https://www.youtube.com/watch?v=a"},
            {"kind": "tutorial", "url": PINBALL_PRIMER_PREFIX + "x.html"}]}
        self.assertEqual(PINBALL_PRIMER_PREFIX + "x.html", contract_1_tutorial(config))

    def test_a_guide_the_proxy_would_refuse_is_not_offered(self) -> None:
        config = {GUIDES_KEY: [
            {"kind": "tutorial", "url": "https://vpuniverse.com/files/file/1"},
            {"kind": "tutorial", "url": "", "youtube_id": "xyz"}]}
        self.assertEqual("", contract_1_tutorial(config))

    def test_no_guides_is_an_empty_string_not_a_failure(self) -> None:
        self.assertEqual("", contract_1_tutorial({}))
        self.assertEqual("", contract_1_tutorial({GUIDES_KEY: []}))


class RefreshedLikeEveryOtherCatalogFact(unittest.TestCase):
    def test_a_changed_catalog_is_reported_as_a_difference(self) -> None:
        config = {"Info": {"Title": "Example"},
                  GUIDES_KEY: [{"kind": "tutorial", "title": "Old", "authors": [],
                                "url": "https://a/", "youtube_id": ""}]}
        found = vps_details_differ(config, _entry({"title": "New", "url": "https://b/"}))
        self.assertIn("guides", found)
        self.assertEqual(["https://b/"], [one["url"] for one in found["guides"][1]])

    def test_an_unchanged_catalog_reports_nothing(self) -> None:
        entry = _entry({"title": "T", "url": "https://a/"})
        config = {"Info": {"Title": "Example"}, GUIDES_KEY: guides_from_vps(entry)}
        self.assertNotIn("guides", vps_details_differ(config, entry))

    def test_a_record_that_has_none_yet_is_a_gap_being_filled(self) -> None:
        found = vps_details_differ({"Info": {"Title": "Example"}},
                                   _entry({"title": "T", "url": "https://a/"}))
        self.assertTrue(found["guides"][2])


def _vps(url: str, title: str = "T", **more) -> dict:
    return {"kind": "tutorial", "origin": "vps", "title": title, "authors": [],
            "url": url, "youtube_id": "", **more}


def _mine(url: str, **more) -> dict:
    return {"kind": "rule_sheet", "origin": "user", "title": "Mine", "authors": [],
            "url": url, "youtube_id": "", **more}


class WhoPutItThere(unittest.TestCase):
    def test_the_catalog_says_so(self) -> None:
        found = guides_from_vps(_entry({"title": "T", "url": "https://a/"}))
        self.assertEqual("vps", found[0]["origin"])

    def test_a_2x_link_came_from_the_catalog(self) -> None:
        out = migrate({"Info": {"PinballPrimerTut": "https://pinballprimer.github.io/x"},
                       "VPinFE": {"altvpsid": "1"}})
        self.assertEqual("vps", out[GUIDES_KEY][0]["origin"])


class TakingTheCatalogsListAgain(unittest.TestCase):
    def _merge(self, held: list, offered: list) -> list:
        from common.games.game_metadata import merge_guides

        return merge_guides(held, offered)

    def test_a_persons_own_keep_their_place(self) -> None:
        out = self._merge([_vps("https://a/"), _mine("https://m/"), _vps("https://b/")],
                          [_vps("https://a/"), _vps("https://b/")])
        self.assertEqual(["https://a/", "https://m/", "https://b/"],
                         [one["url"] for one in out])

    def test_a_hidden_one_stays_hidden_and_is_refreshed(self) -> None:
        out = self._merge([_vps("https://a/", "Old", hidden=True)],
                          [_vps("https://a/", "New")])
        self.assertEqual([("New", True)], [(one["title"], one.get("hidden")) for one in out])

    def test_one_the_catalog_dropped_goes(self) -> None:
        out = self._merge([_vps("https://a/"), _vps("https://gone/")],
                          [_vps("https://a/")])
        self.assertEqual(["https://a/"], [one["url"] for one in out])

    def test_a_new_one_joins_at_the_end(self) -> None:
        out = self._merge([_mine("https://m/"), _vps("https://a/")],
                          [_vps("https://new/"), _vps("https://a/")])
        self.assertEqual(["https://m/", "https://a/", "https://new/"],
                         [one["url"] for one in out])

    def test_a_persons_guide_at_an_offered_address_stands(self) -> None:
        out = self._merge([_mine("https://a/")], [_vps("https://a/")])
        self.assertEqual([("user", "Mine")], [(one["origin"], one["title"]) for one in out])

    def test_a_record_saying_nothing_is_the_persons(self) -> None:
        silent = {"kind": "tutorial", "title": "Hand", "url": "https://h/"}
        self.assertEqual([silent, _vps("https://a/")],
                         self._merge([silent], [_vps("https://a/")]))


class WhatCountsAsADifference(unittest.TestCase):
    def test_a_persons_own_is_not_one(self) -> None:
        entry = _entry({"title": "T", "url": "https://a/"})
        config = {"Info": {"Title": "Example"},
                  GUIDES_KEY: [*guides_from_vps(entry), _mine("https://m/")]}
        self.assertNotIn("guides", vps_details_differ(config, entry))

    def test_the_persons_order_is_not_one(self) -> None:
        entry = _entry({"title": "A", "url": "https://a/"}, {"title": "B", "url": "https://b/"})
        config = {"Info": {"Title": "Example"},
                  GUIDES_KEY: list(reversed(guides_from_vps(entry)))}
        self.assertNotIn("guides", vps_details_differ(config, entry))

    def test_a_title_is_not_one(self) -> None:
        entry = _entry({"title": "Catalog title", "url": "https://a/"})
        config = {"Info": {"Title": "Example"},
                  GUIDES_KEY: [_vps("https://a/", title="")]}
        self.assertNotIn("guides", vps_details_differ(config, entry))

    def test_another_link_is_one(self) -> None:
        entry = _entry({"title": "T", "url": "https://b/"})
        config = {"Info": {"Title": "Example"}, GUIDES_KEY: [_vps("https://a/")]}
        self.assertIn("guides", vps_details_differ(config, entry))

    def test_hiding_one_is_not_one(self) -> None:
        entry = _entry({"title": "T", "url": "https://a/"})
        config = {"Info": {"Title": "Example"},
                  GUIDES_KEY: [{**guides_from_vps(entry)[0], "hidden": True}]}
        self.assertNotIn("guides", vps_details_differ(config, entry))


class WhatEachSurfaceIsSent(unittest.TestCase):
    def test_an_entry_leaves_the_hidden_out(self) -> None:
        from common.games.game_metadata import guides_on_wire

        config = {GUIDES_KEY: [_vps("https://a/", hidden=True), _vps("https://b/")]}
        self.assertEqual(["https://b/"], [one["url"] for one in guides_on_wire(config)])

    def test_the_games_own_list_has_them_all_and_says_whose(self) -> None:
        from common.games.game_metadata import guides_on_wire

        config = {GUIDES_KEY: [_vps("https://a/", hidden=True),
                               {"kind": "tutorial", "url": "https://h/"}]}
        self.assertEqual([("vps", True), ("user", False)],
                         [(one["origin"], one["hidden"])
                          for one in guides_on_wire(config, hidden=True)])

    def test_a_hidden_primer_guide_is_not_the_contract_1_value(self) -> None:
        config = {GUIDES_KEY: [_vps(PINBALL_PRIMER_PREFIX + "x.html", hidden=True)]}
        self.assertEqual("", contract_1_tutorial(config))


class SettingTheList(unittest.TestCase):
    def _curate(self, held: list, wanted: list) -> list:
        from common.games.game_ops import curate_guides

        return curate_guides(held, wanted)

    def test_the_order_and_what_is_hidden_are_the_persons(self) -> None:
        out = self._curate([_vps("https://a/"), _vps("https://b/")],
                           [{"url": "https://b/"}, {"url": "https://a/", "hidden": True}])
        self.assertEqual([("https://b/", None), ("https://a/", True)],
                         [(one["url"], one.get("hidden")) for one in out])

    def test_a_new_address_is_the_persons_own(self) -> None:
        out = self._curate([], [{"url": "https://rules.example/", "kind": "rule_sheet",
                                 "title": "Rules"}])
        self.assertEqual([("user", "rule_sheet", "Rules")],
                         [(one["origin"], one["kind"], one["title"]) for one in out])

    def test_a_video_is_named_by_the_address_it_was_sent_with(self) -> None:
        held = [_vps("", youtube_id="abc")]
        out = self._curate(held, [{"url": "https://www.youtube.com/watch?v=abc",
                                   "hidden": True}])
        self.assertEqual([("", "abc", True)],
                         [(one["url"], one["youtube_id"], one["hidden"]) for one in out])

    def test_a_persons_guide_left_out_is_removed(self) -> None:
        self.assertEqual([], self._curate([_mine("https://m/")], []))

    def test_what_is_refused(self) -> None:
        from common.service_errors import RefusedError

        for held, wanted in (
                ([_vps("https://a/")], []),
                ([], [{"url": ""}]),
                ([], [{"url": "https://x/"}, {"url": "https://x/"}]),
                ([], [{"url": "ftp://x/"}]),
                ([], [{"url": "https://x/", "kind": "flyer"}])):
            with self.subTest(wanted=wanted), self.assertRaises(RefusedError):
                self._curate(held, wanted)


class WhatTheDifferSays(unittest.TestCase):
    def test_guides_are_named_by_the_site_they_live_on(self) -> None:
        from common.games.game_ops import _both_said

        said = _both_said("guides", [_vps("https://pinballprimer.github.io/a.html")],
                          [_vps("https://pinballprimer.github.io/a.html"),
                           _vps("https://tiltforums.com/t/1")])
        self.assertEqual({"ours": "Pinball Primer",
                          "theirs": "Pinball Primer, Tilt Forums"}, said)

    def test_two_sides_reading_the_same_are_told_apart_by_address(self) -> None:
        from common.games.game_ops import _both_said

        said = _both_said("guides", [_vps("https://pinballprimer.github.io/a.html")],
                          [_vps("https://pinballprimer.github.io/b.html")])
        self.assertEqual({"ours": "https://pinballprimer.github.io/a.html",
                          "theirs": "https://pinballprimer.github.io/b.html"}, said)

    def test_two_sides_differing_only_in_kind_are_told_apart(self) -> None:
        from common.games.game_ops import _both_said

        said = _both_said("guides", [_vps("https://a/")],
                          [{**_vps("https://a/"), "kind": "manual"}])
        self.assertNotEqual(said["ours"], said["theirs"])

    def test_no_guides_reads_as_nothing(self) -> None:
        from common.games.game_ops import _said

        self.assertEqual("", _said([], "guides"))


class OnTheWire(unittest.TestCase):
    def test_each_guide_says_where_it_lives(self) -> None:
        from common.games.game_metadata import guides_on_wire

        config = {GUIDES_KEY: [
            {"kind": "tutorial", "url": PINBALL_PRIMER_PREFIX + "x.html"},
            {"kind": "tutorial", "url": "", "youtube_id": "abc"},
            {"kind": "tutorial", "url": "https://example.org/rules"}]}
        self.assertEqual(["Pinball Primer", "YouTube", "example.org"],
                         [one["source"] for one in guides_on_wire(config)])

    def test_no_guides_is_an_empty_list(self) -> None:
        from common.games.game_metadata import guides_on_wire

        self.assertEqual([], guides_on_wire({}))

    def test_a_video_with_no_url_opens_on_youtube_and_keeps_its_id(self) -> None:
        from common.games.game_metadata import guides_on_wire

        sent = guides_on_wire({GUIDES_KEY: [{"kind": "tutorial", "url": "",
                                             "youtube_id": "abc"}]})
        self.assertEqual(("https://www.youtube.com/watch?v=abc", "abc"),
                         (sent[0]["url"], sent[0]["youtube_id"]))

    def test_a_url_is_sent_as_it_is_stored(self) -> None:
        from common.games.game_metadata import guides_on_wire

        sent = guides_on_wire({GUIDES_KEY: [{"kind": "tutorial", "url": "https://a/b",
                                             "youtube_id": "abc"}]})
        self.assertEqual("https://a/b", sent[0]["url"])


if __name__ == "__main__":
    unittest.main()
