"""The rows of Needs You: which draw, in what order, counting what, and opening what.

The rows are decided by `needs.collect`, apart from the page, so this runs without one.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from common.extensions import host
from common.i18n import t
from common.runtime.jobs import DONE, FAILED
from common.settings import feature_checks
from manager.kit import need_keys
from manager.kit.data import Library
from manager.library import games
from manager.overview import needs


class FakeLibrary:
    def __init__(self, games_=(), tables=(), watched=None, hidden=(), unreadable=(),
                 collected=("playfield", "backglass"), metadata=None):
        self.games = list(games_)
        self.media = {g["id"]: g.get("media", {}) for g in self.games}
        self._tables = list(tables)
        self._watched = watched or {}
        self._hidden = set(hidden)
        self._unreadable = list(unreadable)
        self._collected = set(collected)
        self._metadata = dict(metadata or {})

    untied_tables = Library.untied_tables

    def table_rows(self):
        return self._tables

    def fillable_media(self):
        return getattr(self, "offered", {})

    def hidden_checks(self):
        return self._hidden

    def stopped(self):
        return self._hidden

    def kept_kinds(self):
        return {"media": self._collected}

    def watched(self):
        return self._watched

    def discovery(self):
        return {"install_id": "me"}

    def metadata_state(self):
        return {"unreadable": self._unreadable, **self._metadata}


def game(game_id, **rest):
    return {"id": game_id, "name": game_id, "year": "1992", "vps_id": "v", **rest}


def present(*kinds):
    return {"media": {kind: {"present": True} for kind in kinds}}


def table(game_id, **rest):
    return {"id": f"t-{game_id}", "game_id": game_id, "rom": "afm", **rest}


def keys(rows):
    return [one.key for one in rows]


class DrawsOnlyWhatHasSomethingToSay(unittest.TestCase):
    def test_a_clean_library_has_no_rows(self):
        lib = FakeLibrary([game("g1", **present("playfield", "backglass"))])
        self.assertEqual([], needs.collect(lib, {}))

    def test_a_check_the_library_turned_off_does_not_draw(self):
        lib = FakeLibrary([game("g1", year="", **present("playfield", "backglass"))],
                          hidden=["no_year"])
        self.assertEqual([], needs.collect(lib, {}))

    def test_a_kind_nobody_collects_is_not_a_row(self):
        lib = FakeLibrary([game("g1", **present("playfield"))], collected=("playfield",))
        self.assertEqual([], needs.collect(lib, {}))


class WhatASourceCanFillIsDiscoverys(unittest.TestCase):
    def test_a_game_a_source_can_fill_is_not_a_row_of_its_own(self):
        lib = FakeLibrary([game("g1"), game("g2")])
        lib.offered = {"playfield": {"g1", "g2"}, "backglass": {"g1"}}
        rows = {one.key: one for one in needs.collect(lib, {})}

        self.assertNotIn("no_playfield", rows)
        self.assertEqual(("g2",), rows["no_backglass"].ids)
        self.assertNotIn("no_media", rows)

    def test_the_games_nothing_can_fill_keep_their_rows(self):
        lib = FakeLibrary([game("g1")])
        rows = {one.key: one for one in needs.collect(lib, {})}

        self.assertEqual(("g1",), rows["no_playfield"].ids)
        self.assertEqual(("g1",), rows["no_media"].ids)


class CountsKeepTheirOwnUnit(unittest.TestCase):
    def test_a_missing_rom_counts_tables_and_a_game_without_art_counts_games(self):
        lib = FakeLibrary(
            [game("g1"), game("g2", **present("playfield", "backglass"))],
            [table("g1", rom_installed=False), table("g1", rom_installed=False),
             table("g2", rom_installed=True)])
        rows = {one.key: one for one in needs.collect(lib, {})}

        self.assertEqual(2, rows["rom_missing"].count)
        self.assertEqual("2 tables", rows["rom_missing"].counted())
        self.assertEqual("1 game", rows["no_playfield"].counted())

    def test_the_same_game_is_counted_by_each_row_that_names_it(self):
        lib = FakeLibrary([game("g1", year="")])
        rows = {one.key: one for one in needs.collect(lib, {})}
        self.assertEqual(("g1",), rows["no_year"].ids)
        self.assertEqual(("g1",), rows["no_media"].ids)

    def test_an_unknown_rom_is_not_missing(self):
        lib = FakeLibrary([game("g1", **present("playfield", "backglass"))],
                          [table("g1", rom_installed=None)])
        self.assertEqual([], needs.collect(lib, {}))


class OrderedByCost(unittest.TestCase):
    def test_cannot_play_then_missing_then_ready_then_tidy(self):
        update = {"update_available": True, "latest_version": "9.9.9", "update_supported": True}
        lib = FakeLibrary(
            [game("g1", year="", vps_id="")],
            [table("g1", rom_installed=False, update_available=True)],
            watched={"update": update, "recordings": ["r1"]},
            unreadable=[{"folder": "x"}])
        trouble = [SimpleNamespace(where=feature_checks.WHERE_LAUNCHERS)]

        order = keys(needs.collect(lib, {"trouble": trouble}))

        self.assertEqual(["rom_missing", "launchers", "unreadable", "unmatched",
                          "no_playfield", "no_backglass", "no_media", "update",
                          "recordings", "no_year"], order)

    def test_the_tones_follow_the_cost(self):
        lib = FakeLibrary([game("g1", year="", vps_id="")], [table("g1", rom_installed=False)])
        tones = {one.key: one.tone for one in needs.collect(lib, {})}
        self.assertEqual(needs.DANGER, tones["rom_missing"])
        self.assertEqual(needs.WARM, tones["unmatched"])
        self.assertEqual(needs.MUTED, tones["no_year"])


class WhereAClickLands(unittest.TestCase):
    def test_each_grid_row_opens_its_grid_on_what_it_counted(self):
        update = {"update_available": False}
        lib = FakeLibrary([game("g1", year="", vps_id="")],
                          [table("g1", rom_installed=False, update_available=True)],
                          watched={"update": update})
        rows = {one.key: one.opens for one in needs.collect(lib, {})}

        self.assertEqual(("tables", games.ROM_MISSING),
                         (rows["rom_missing"].view, rows["rom_missing"].arriving))
        self.assertEqual(("games", games.UNMATCHED),
                         (rows["unmatched"].view, rows["unmatched"].arriving))
        self.assertEqual({games.UNFILLED_KEY: ("g1",)}, dict(rows["no_playfield"].keys))

    def test_a_media_row_opens_exactly_the_games_it_counts(self):
        lib = FakeLibrary([game("g1"), game("g2"), game("g3")])
        lib.offered = {"playfield": {"g1"}}
        rows = {one.key: one for one in needs.collect(lib, {})}
        for key in ("no_playfield", "no_backglass", "no_media"):
            opened = rows[key].opens.keys[games.UNFILLED_KEY]
            self.assertEqual(rows[key].count, len(opened), key)
            self.assertEqual(rows[key].ids, tuple(opened), key)
        self.assertNotIn("g1", rows["no_playfield"].opens.keys[games.UNFILLED_KEY])

    def test_the_rows_that_are_not_a_grid_say_what_they_do_instead(self):
        lib = FakeLibrary(watched={"recordings": ["r1"]}, unreadable=[{"folder": "x"}])
        rows = {one.key: one.opens for one in needs.collect(lib, {})}
        self.assertEqual((needs.FOLDERS, {"folders": "unreadable"}),
                         (rows["unreadable"].then, dict(rows["unreadable"].keys)))
        self.assertEqual("", rows["unreadable"].view)
        self.assertEqual(needs.REVIEW, rows["recordings"].then)


class WhatVPinFECanDoItself(unittest.TestCase):
    def test_art_that_is_missing_can_be_fetched(self):
        lib = FakeLibrary([game("g1", **present("backglass"))])
        row = next(one for one in needs.collect(lib, {}) if one.key == "no_playfield")
        self.assertEqual((needs.GET_MEDIA, "playfield"), (row.fix.key, row.fix.kind))

    def test_an_update_is_offered_only_where_this_build_can_take_it(self):
        def row(supported):
            update = {"update_available": True, "latest_version": "3.0.2",
                      "update_supported": supported}
            lib = FakeLibrary(watched={"update": update})
            return needs.collect(lib, {})[0]

        self.assertEqual("VPinFE 3.0.2 Is Available", row(True).label)
        self.assertIsNotNone(row(True).fix)
        self.assertIsNone(row(False).fix)

    def test_a_failed_check_for_an_update_is_not_an_update(self):
        lib = FakeLibrary(watched={"update": {"update_available": True, "error": "offline"}})
        self.assertEqual([], needs.collect(lib, {}))


class AReadThatFailedIsSaid(unittest.TestCase):
    def test_a_page_that_could_not_look_never_says_nothing_needs_you(self):
        lib = FakeLibrary(watched={"unread": ["recordings", "fill"]})
        rows = needs.collect(lib, {})
        self.assertEqual(["unread"], keys(rows))
        self.assertEqual(needs.RETRY, rows[0].opens.then)
        self.assertIn("the recordings waiting", rows[0].tip)
        self.assertIn("the last media fill", rows[0].tip)
        self.assertNotIn("update", rows[0].tip)

    def test_each_read_that_fails_is_named_and_the_others_still_count(self):
        def refuse():
            raise OSError("down")

        client = SimpleNamespace(update_check=lambda: {"error": "offline"},
                                 capture_proposals=lambda: {"proposals": [{"id": "r1"}]},
                                 jobs=lambda limit: refuse(),
                                 extension_needs=lambda: {"needs": []},
                                 unaccepted_themes=lambda: {"themes": []},
                                 extensions=lambda: [],
                                 launchers=lambda: {"launchers": []},
                                 all_media=lambda: [], devices=lambda: [],
                                 library_policy=lambda: {})
        library = Library.__new__(Library)
        library._client = client

        read = library._read_watched()

        self.assertEqual(["update", "fill"], read["unread"])
        self.assertEqual(["r1"], read["recordings"])

    def test_it_comes_last_and_only_when_a_read_failed(self):
        lib = FakeLibrary([game("g1", year="")], watched={"unread": ["update"]})
        self.assertEqual("unread", keys(needs.collect(lib, {}))[-1])
        self.assertNotIn("unread", keys(needs.collect(FakeLibrary([game("g1", year="")]), {})))


class MediaFill(unittest.TestCase):
    def _fill(self, state, problems=0, stopped=False):
        return {"kind": "library.media_fill", "state": state, "problems": problems,
                "stopped": stopped, "title": "Get media"}

    def test_a_fill_that_ended_with_errors_is_a_row_that_opens_that_run(self):
        lib = FakeLibrary(watched={"fill": self._fill(DONE, problems=2)})
        row = needs.collect(lib, {})[0]
        self.assertEqual("fill_errors", row.key)
        self.assertEqual("jobs", row.opens.view)
        self.assertEqual("Get media", row.opens.keys["jobs_find"])

    def test_a_failed_fill_is_one_too(self):
        lib = FakeLibrary(watched={"fill": self._fill(FAILED)})
        self.assertEqual(["fill_errors"], keys(needs.collect(lib, {})))

    def test_a_clean_fill_is_not(self):
        lib = FakeLibrary(watched={"fill": self._fill(DONE)})
        self.assertEqual([], needs.collect(lib, {}))


def clip(game_id, **rest):
    return {"game_id": game_id, "path": f"{game_id}.mp4", "kind": "playfield_video",
            "via": "table", "present": True, "video_codec": "h264", **rest}


REFUSES_H264 = {"device_id": "me", "browser": {"formats": {"h264": False}}}


class FilesConvertIsFor(unittest.TestCase):
    def rows(self, files, devices=(REFUSES_H264,), **rest):
        lib = FakeLibrary(watched={"media_files": list(files), "devices": list(devices)},
                          **rest)
        return {one.key: one for one in needs.collect(lib, {})}

    def test_a_file_in_a_format_nothing_uses_is_a_row_opening_the_unsupported_view(self):
        found = self.rows([clip("a", via="unsupported", video_codec=None, path="a.avi")])

        row = found["unsupported"]
        self.assertEqual(("a:a.avi",), row.ids)
        self.assertEqual("media", row.opens.view)
        self.assertEqual({games.ARRIVE_VIEW: "manager.view.unsupported"}, row.opens.keys)
        self.assertEqual("1 file", row.counted())

    def test_a_video_a_device_refuses_is_a_row_opening_the_wont_play_view(self):
        found = self.rows([clip("a"), clip("b")])

        row = found["wont_play"]
        self.assertEqual(("a:a.mp4", "b:b.mp4"), row.ids)
        self.assertEqual({games.ARRIVE_VIEW: "manager.view.wont_play"}, row.opens.keys)
        self.assertEqual(needs.DANGER, row.tone)

    def test_neither_draws_while_nothing_is_refused(self):
        self.assertEqual({}, self.rows([clip("a")], devices=[]))
        self.assertEqual({}, self.rows([]))

    def test_a_device_that_plays_another_library_does_not_make_a_row(self):
        elsewhere = {"device_id": "else", "browser": {"formats": {"h264": False}}}

        self.assertEqual({}, self.rows([clip("a")], devices=[elsewhere]))

    def test_a_file_that_is_not_there_is_missing_not_refused(self):
        self.assertEqual({}, self.rows([clip("a", present=False)]))

    def test_the_count_is_the_media_views_own(self):
        from manager.library import file_states, media

        files = [clip("a"), clip("b", video_codec="vp9"), clip("c", via="unsupported"),
                 clip("d", via="orphan")]
        grid = media.rows(files, [REFUSES_H264], "me")
        found = self.rows(files)

        self.assertEqual(
            {f"{one['game_id']}:{one['path']}" for one in grid
             if one["reason"] == file_states.WONT_PLAY}, set(found["wont_play"].ids))
        self.assertEqual(
            {f"{one['game_id']}:{one['path']}" for one in grid
             if one["reason"] == file_states.UNSUPPORTED}, set(found["unsupported"].ids))

    def test_the_devices_that_refused_are_named_once_each(self):
        here = {**REFUSES_H264, "display_name": "Laptop"}
        also = {"device_id": "cab", "display_name": "Cabinet",
                "browser": {"formats": {"h264": False}, "library": "me"}}
        found = needs.unplayable({"media_files": [clip("a"), clip("b")],
                                  "devices": [here, also]}, "me")

        self.assertEqual(("Laptop", "Cabinet"), found.devices)

    def test_this_install_refusing_is_said_and_another_device_refusing_is_not(self):
        cab = {"device_id": "cab", "display_name": "Cabinet",
               "browser": {"formats": {"h264": False}, "library": "me"}}
        files = {"media_files": [clip("a")]}

        self.assertTrue(needs.unplayable({**files, "devices": [REFUSES_H264]}, "me").here)
        self.assertFalse(needs.unplayable({**files, "devices": [cab]}, "me").here)

    def test_a_kind_set_aside_with_nothing_new_or_stopped_is_left_out_of_the_count(self):
        found = {"media_files": [clip("a"), clip("b", via="unsupported", video_codec=None)],
                 "devices": [REFUSES_H264]}

        both = needs.unplayable(found, "me")
        aside = needs.unplayable(found, "me", {"wont_play": ["a:a.mp4"],
                                               "unsupported": ["b:b.mp4"]})
        new = needs.unplayable(found, "me", {"wont_play": ["z:z.mp4"]})
        stopped = needs.unplayable(found, "me", None, {"wont_play"})

        self.assertEqual((1, 1), (len(both.wont_play), len(both.unsupported)))
        self.assertEqual((0, 0), (len(aside.wont_play), len(aside.unsupported)))
        self.assertEqual(1, len(new.wont_play))
        self.assertEqual((0, 1), (len(stopped.wont_play), len(stopped.unsupported)))

    def test_both_can_be_set_aside_and_say_when_they_return(self):
        for key in ("wont_play", "unsupported"):
            self.assertTrue(needs.can_set_aside(key))
            self.assertTrue(needs.name_of(key))
            self.assertTrue(needs.comes_back(key))

    def test_a_read_that_failed_makes_both_blind_and_is_said(self):
        lib = FakeLibrary(watched={"unread": ["media"]})

        self.assertLessEqual({"wont_play", "unsupported"}, needs.blind(lib))
        self.assertIn(t("manager.needs.unread_media"), needs.collect(lib, {})[0].tip)


class TheMediaReadKeepsOnlyWhatCouldBeRefused(unittest.TestCase):
    def library(self, rows):
        client = SimpleNamespace(all_media=lambda: rows, devices=lambda: [REFUSES_H264],
                                 library_policy=lambda: {})
        library = Library.__new__(Library)
        library._client = client
        return library

    def test_a_picture_that_plays_is_not_kept(self):
        found, unread = self.library([
            clip("a", kind="playfield", video_codec=None),
            clip("b"), clip("c", via="unsupported", video_codec=None)])._watch_media()

        self.assertEqual(["b", "c"], [one["game_id"] for one in found["media_files"]])
        self.assertEqual([], unread)

    def test_a_read_that_fails_is_unread_not_empty(self):
        def refused():
            raise OSError("not allowed")

        library = self.library([])
        library._client.all_media = refused

        self.assertEqual(({}, ["media"]), library._watch_media())


def bare(key, ids=(), token=()):
    return needs.Need(key, key, "", needs.WARM, "x", needs.Opens(), tuple(ids),
                      "manager.games.games", token=tuple(token))


class SettingAsideOverWhatItCounted(unittest.TestCase):
    def test_a_row_stays_away_while_every_id_it_counts_is_in_the_record(self):
        arranged = needs.arrange([bare("unmatched", ["a", "b"])], {"unmatched": ["a", "b"]},
                                 set())
        self.assertEqual([], arranged.rows)
        self.assertEqual(["unmatched"], [one.key for one in arranged.aside])
        self.assertEqual({"unmatched": ["a", "b"]}, arranged.record)

    def test_a_row_with_nothing_recorded_draws_whole(self):
        arranged = needs.arrange([bare("unmatched", ["a"])], {}, set())
        self.assertEqual(["unmatched"], keys(arranged.rows))
        self.assertEqual((), arranged.rows[0].new)

    def test_a_joiner_brings_it_back_with_the_true_total_and_the_new_marked(self):
        arranged = needs.arrange([bare("unmatched", ["a", "b", "c"])],
                                 {"unmatched": ["a", "b"]}, set())
        self.assertEqual(3, arranged.rows[0].count)
        self.assertEqual(("c",), arranged.rows[0].new)
        self.assertEqual([], arranged.aside)

    def test_what_the_row_no_longer_counts_leaves_the_record(self):
        arranged = needs.arrange([bare("unmatched", ["a", "c"])], {"unmatched": ["a", "b"]},
                                 set())
        self.assertEqual({"unmatched": ["a"]}, arranged.record)

    def test_a_row_whose_old_ids_are_all_gone_is_a_different_finding_and_unmarked(self):
        arranged = needs.arrange([bare("unmatched", ["c"])], {"unmatched": ["a"]}, set())
        self.assertEqual((), arranged.rows[0].new)
        self.assertEqual({}, arranged.record)

    def test_a_row_a_source_fills_keeps_what_it_was_dismissed_over_while_the_source_fills_it(self):
        # A source had a file for "b", so the row stopped counting it; the source is
        # away again and the row counts it, dismissed as it was.
        record = {"no_backglass": ["a", "b"]}
        gone_away = needs.arrange([bare("no_backglass", ["a", "b"])], record, set(), (), (),
                                  {"no_backglass": set()})
        filled_by_it = needs.arrange([bare("no_backglass", ["a"])], record, set(), (), (),
                                     {"no_backglass": {"b"}})

        self.assertEqual([], gone_away.rows)
        self.assertEqual({"no_backglass": ["a", "b"]}, filled_by_it.record)
        self.assertEqual({"no_backglass": ["a", "b"]},
                         needs.arrange([bare("no_backglass", ["a", "b"])],
                                       filled_by_it.record, set(), (), (),
                                       {"no_backglass": set()}).record)

    def test_a_source_filled_row_is_kept_whole_when_a_source_fills_every_game_in_it(self):
        arranged = needs.arrange([], {"no_backglass": ["a", "b"]}, set(), (), (),
                                 {"no_backglass": {"a", "b"}})

        self.assertEqual({"no_backglass": ["a", "b"]}, arranged.record)

    def test_what_a_source_fills_no_longer_leaves_the_record(self):
        arranged = needs.arrange([bare("no_media", ["a"])], {"no_media": ["a", "b"]}, set(),
                                 (), (), {"no_media": set()})

        self.assertEqual({"no_media": ["a"]}, arranged.record)
        self.assertEqual({}, needs.arrange([], {"no_media": ["b"]}, set(), (), (),
                                           {"no_media": set()}).record)

    def test_a_game_that_has_the_file_itself_is_forgotten_and_counts_as_new_when_it_goes(self):
        # Dismissed for "a" and "b"; "a" gets a playfield, no source involved, so the row
        # stops counting it and its id leaves. Its playfield is deleted later: the row
        # counts "a" again, and it is a new one.
        record = {"no_playfield": ["a", "b"]}
        got_it = needs.arrange([bare("no_playfield", ["b"])], record, set(), (), (),
                               {"no_playfield": set()})
        lost_it = needs.arrange([bare("no_playfield", ["a", "b"])], got_it.record, set(), (), (),
                                {"no_playfield": set()})

        self.assertEqual({"no_playfield": ["b"]}, got_it.record)
        self.assertEqual(("a",), lost_it.rows[0].new)

    def test_a_row_no_source_fills_still_forgets_an_id_it_no_longer_counts(self):
        arranged = needs.arrange([bare("unmatched", ["a"])], {"unmatched": ["a", "b"]}, set(),
                                 (), (), {"no_backglass": {"a", "b"}})

        self.assertEqual({"unmatched": ["a"]}, arranged.record)

    def test_a_row_not_checked_keeps_its_record_but_an_absent_one_keeps_nothing(self):
        record = {"unmatched": ["a"], "no_year": ["b"]}
        arranged = needs.arrange([], record, {"no_year"})
        self.assertEqual({"no_year": ["b"]}, arranged.record)

    def test_a_read_that_failed_keeps_its_record(self):
        arranged = needs.arrange([], {"update": ["3.1"], "unmatched": ["a"]}, set(),
                                 ["update"])
        self.assertEqual({"update": ["3.1"]}, arranged.record)

    def test_a_blind_row_keeps_its_record(self):
        arranged = needs.arrange([], {"rom_missing": ["t-a"], "unmatched": ["a"]}, set(), (),
                                 {"rom_missing"})
        self.assertEqual({"rom_missing": ["t-a"]}, arranged.record)

    def test_a_row_with_no_ids_is_dismissed_over_its_token(self):
        row = bare("update", token=["3.1"])
        self.assertEqual([], needs.arrange([row], {"update": ["3.1"]}, set()).rows)
        self.assertEqual(["update"], keys(needs.arrange([row], {"update": ["3.0"]}, set()).rows))

    def test_the_unread_row_is_never_set_aside(self):
        row = needs.Need("unread", "x", "", needs.MUTED, "x", needs.Opens(then=needs.RETRY))
        self.assertEqual(["unread"], keys(needs.arrange([row], {"unread": ["x"]}, set()).rows))

    def test_a_stopped_check_is_set_aside_as_not_checked(self):
        arranged = needs.arrange([], {}, {"no_year", "recordings"})
        self.assertEqual(["recordings", "no_year"], [one.key for one in arranged.aside])
        self.assertTrue(all(one.stopped for one in arranged.aside))

    def test_an_update_dismissal_says_skipped(self):
        arranged = needs.arrange([bare("update", token=["3.1"])], {"update": ["3.1"]}, set())
        self.assertEqual("Skipped", arranged.aside[0].chip)


class WhereAStopIsKept(unittest.TestCase):
    def test_a_device_row_is_this_installs_and_a_library_row_the_librarys(self):
        self.assertTrue(all(needs.is_device(key) for key in
                            ("launchers", "fill_errors", "leftovers", "update", "recordings")))
        self.assertFalse(any(needs.is_device(key) for key in
                             ("rom_missing", "unreadable", "unmatched", "no_year")))

    def test_the_unread_row_cannot_be_set_aside(self):
        self.assertFalse(needs.can_set_aside("unread"))
        self.assertTrue(all(needs.can_set_aside(key) for key in needs.ORDER))

    def test_no_vpx_leaves_the_rom_row_blind(self):
        library = FakeLibrary(tables=[table("a", rom_installed=None, update_available=None)])
        self.assertEqual({"rom_missing"}, needs.blind(library))

    def test_a_row_the_library_can_count_is_not_blind(self):
        library = FakeLibrary(tables=[table("a", rom_installed=True, update_available=False)])
        self.assertEqual(set(), needs.blind(library))

    def test_a_metadata_state_that_could_not_be_read_leaves_its_rows_blind(self):
        library = FakeLibrary(tables=[table("a", rom_installed=True, update_available=False)])
        library.metadata_state = lambda: {}
        self.assertEqual({"unreadable", "older_format"}, needs.blind(library))

    def test_a_kind_the_library_does_not_collect_leaves_its_row_blind(self):
        library = FakeLibrary(tables=[table("a", rom_installed=True, update_available=False)],
                              collected=("backglass",))
        self.assertEqual({"no_playfield"}, needs.blind(library))

    def test_every_row_that_can_be_set_aside_has_a_name_and_a_line_on_when_it_returns(self):
        for key in needs.ORDER:
            self.assertTrue(needs.name_of(key), key)
            self.assertTrue(needs.comes_back(key), key)


class StoppingOneRowKeepsTheOthers(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Mock()
        self.client.library_policy.return_value = {"hidden_checks": ["no_year"]}
        self.client.manager_policy.return_value = {"hidden_overview": ["recordings"]}
        self.client.put_library_policy.return_value = {}
        self.library = Library(self.client)

    def test_a_library_row_is_added_to_what_the_server_holds_not_to_what_the_page_held(self):
        self.library.set_checking("unmatched", False, device=False)
        self.client.put_library_policy.assert_called_once_with(
            {"hidden_checks": ["no_year", "unmatched"]})

    def test_a_device_row_is_added_to_what_the_server_holds(self):
        self.library.set_checking("update", False, device=True)
        self.client.put_manager_policy.assert_called_once_with(
            {"hidden_overview": ["recordings", "update"]})

    def test_checking_again_takes_only_that_row_out(self):
        self.library.set_checking("recordings", True, device=True)
        self.client.put_manager_policy.assert_called_once_with({"hidden_overview": []})

    def test_a_policy_that_cannot_be_read_writes_nothing(self):
        self.client.library_policy.side_effect = OSError("refused")
        with self.assertRaises(OSError):
            self.library.set_checking("unmatched", False, device=False)
        self.client.put_library_policy.assert_not_called()


def raised(extension, kind, *, tone=needs.WARM, name=""):
    return {"extension": extension, "key": f"{needs.EXTENSION}{extension}:{kind}",
            "label": kind, "to": "x", "opens": "view", "tone": tone,
            "display_name": name or extension, "count": 1}


class RowsAreGroupedByWhoRaisedThem(unittest.TestCase):
    def labels(self, rows):
        return [(one.label, [r.key for r in one.rows]) for one in needs.grouped(rows)]

    def test_the_library_this_device_and_each_extension_are_a_group(self):
        update = {"update_available": True, "latest_version": "9", "update_supported": True}
        lib = FakeLibrary([game("g1", year="")], watched={
            "update": update, "extension_needs": [raised("acme", "weekly", name="Acme")]})
        groups = self.labels(needs.collect(lib, {}))
        self.assertEqual([("Library", ["no_playfield", "no_backglass", "no_media", "no_year"]),
                          ("Acme", ["ext:acme:weekly"]),
                          ("This Device", ["update"])], groups)

    def test_a_group_goes_by_its_costliest_row(self):
        trouble = [SimpleNamespace(where=feature_checks.WHERE_LAUNCHERS)]
        lib = FakeLibrary([game("g1", vps_id="")], watched={
            "extension_needs": [raised("acme", "weekly")]})
        order = [one.label for one in needs.grouped(needs.collect(lib, {"trouble": trouble}))]
        self.assertEqual(["This Device", "Library", "acme"], order)

    def test_groups_that_tie_keep_library_then_device_then_extensions_by_name(self):
        rows = [needs.Need("recordings", "r", "", needs.WARM, "x", needs.Opens()),
                *needs.extension_rows([raised("zed", "a"), raised("abe", "a")]),
                needs.Need("no_year", "y", "", needs.WARM, "x", needs.Opens())]
        self.assertEqual(["Library", "This Device", "abe", "zed"],
                         [one.label for one in needs.grouped(rows)])

    def test_a_group_puts_its_costliest_row_first(self):
        rows = [needs.Need("ext:acme:tidy", "t", "", needs.MUTED, "x", needs.Opens()),
                needs.Need("ext:acme:broken", "b", "", needs.DANGER, "x", needs.Opens())]
        [group] = needs.grouped(rows)
        self.assertEqual(["ext:acme:broken", "ext:acme:tidy"], keys(group.rows))

    def test_rows_that_tie_keep_the_order_they_were_given(self):
        rows = [needs.Need("no_year", "y", "", needs.WARM, "x", needs.Opens()),
                needs.Need("unmatched", "u", "", needs.WARM, "x", needs.Opens())]
        [group] = needs.grouped(rows)
        self.assertEqual(["no_year", "unmatched"], keys(group.rows))

    def test_the_row_saying_the_page_could_not_look_has_no_heading_and_is_last(self):
        lib = FakeLibrary(watched={"unread": ["fill"], "extension_needs": [raised("acme", "a")]})
        groups = needs.grouped(needs.collect(lib, {}))
        self.assertEqual(["acme", ""], [one.label for one in groups])
        self.assertEqual(["unread"], keys(groups[-1].rows))

    def test_nothing_to_say_is_no_group(self):
        self.assertEqual([], needs.grouped([]))


METADATA = {"unreadable": [{"folder": "Broken B", "error": "empty"},
                            {"folder": "Broken A", "error": "empty"}],
            "unwritten": [{"folder": "Locked B", "error": "read only"},
                          {"folder": "Locked A", "error": "read only"}],
            "pending_games": ["Old B", "Old A"],
            "newer_than_us": 2}


class WhatTheLibrarysMetadataRaises(unittest.TestCase):
    def rows(self, **given):
        lib = FakeLibrary(**given)
        return {one.key: one for one in needs.collect(lib, {})}

    def test_each_row_counts_the_folders_the_state_lists_and_opens_them(self):
        rows = self.rows(metadata=METADATA)

        self.assertEqual(("Broken A", "Broken B"), rows["unreadable"].ids)
        self.assertEqual(("Locked A", "Locked B"), rows["unwritten"].ids)
        self.assertEqual(("Old A", "Old B"), rows["older_format"].ids)
        for key, which in (("unreadable", "unreadable"), ("unwritten", "unwritten"),
                           ("older_format", "older_format")):
            self.assertEqual((needs.FOLDERS, {"folders": which}),
                             (rows[key].opens.then, dict(rows[key].opens.keys)), key)

    def test_the_labels_tones_and_units(self):
        rows = self.rows(metadata=METADATA)

        self.assertEqual(("Could Not Save", needs.WARM, "2 games"),
                         (rows["unwritten"].label, rows["unwritten"].tone,
                          rows["unwritten"].counted()))
        self.assertEqual(("Older Format", needs.MUTED, "2 games"),
                         (rows["older_format"].label, rows["older_format"].tone,
                          rows["older_format"].counted()))
        self.assertEqual(("Written by a Newer VPinFE", needs.MUTED),
                         (rows["newer_build"].label, rows["newer_build"].tone))

    def test_only_the_older_format_has_a_fix_and_it_is_the_upgrade(self):
        rows = self.rows(metadata=METADATA)

        self.assertEqual(needs.UPGRADE, rows["older_format"].fix.key)
        self.assertEqual("Upgrade", rows["older_format"].fix.label)
        self.assertIsNone(rows["unwritten"].fix)
        self.assertIsNone(rows["newer_build"].fix)

    def test_a_newer_build_counts_games_it_has_no_ids_for_and_opens_about(self):
        row = self.rows(metadata=METADATA)["newer_build"]

        self.assertEqual((), row.ids)
        self.assertEqual(2, row.count)
        self.assertEqual("2 games", row.counted())
        self.assertEqual("about", row.opens.view)

    def test_an_extension_s_row_has_a_count_and_no_unit_so_it_draws_none(self):
        raised_row = needs.extension_rows([{**RAISED, "covered": []}])[0]

        self.assertEqual(3, raised_row.count)
        self.assertEqual("", raised_row.counted())

    def test_a_row_with_a_unit_and_no_count_draws_none(self):
        row = needs.Need("x", "x", "", needs.MUTED, "x", needs.Opens(),
                         unit="manager.games.games")

        self.assertEqual("", row.counted())

    def test_a_state_with_nothing_in_it_raises_none_of_them(self):
        rows = self.rows(metadata={"unwritten": [], "pending_games": [], "newer_than_us": 0})

        self.assertEqual(set(), {"unreadable", "unwritten", "older_format", "newer_build"}
                         & set(rows))

    def test_older_format_is_hidden_with_the_checks_the_library_turned_off(self):
        shown = self.rows(metadata=METADATA, hidden=["older_format"])

        self.assertNotIn("older_format", shown)
        self.assertIn("unwritten", shown)
        self.assertIn("newer_build", shown)

    def test_unwritten_and_newer_build_are_not_checks_and_cannot_be_set_aside(self):
        self.assertFalse(needs.can_set_aside("unwritten"))
        self.assertFalse(needs.can_set_aside("newer_build"))
        self.assertNotIn("unwritten", needs.ORDER)
        self.assertNotIn("newer_build", needs.ORDER)
        self.assertTrue(needs.can_set_aside("older_format"))

    def test_older_format_is_named_and_says_when_it_returns(self):
        self.assertEqual("Older Format", needs.name_of("older_format"))
        self.assertEqual("Returns when another game is on an older format",
                         needs.comes_back("older_format"))
        self.assertEqual(needs.ORDER.index("no_year"), needs.ORDER.index("older_format") + 1)

    def test_the_library_group_reads_in_this_order_when_every_row_is_there(self):
        lib = FakeLibrary(
            [game("g1", year="", vps_id="", table_count=0)],
            [table("g1", rom_installed=False, update_available=True)],
            metadata=METADATA)

        [group] = [one for one in needs.grouped(needs.collect(lib, {}))
                   if one.owner == needs.LIBRARY]

        self.assertEqual(
            ["Missing ROM", "Unreadable Folder", "Could Not Save", "Unmatched", "No Tables",
             "No Playfield", "No Backglass", "No Media",
             "Older Format", "Written by a Newer VPinFE", "No Year"],
            [one.label for one in group.rows])


class TablesWithNoReleaseMatched(unittest.TestCase):
    def _rows(self, *tables):
        lib = FakeLibrary([game("g1"), game("g2")], list(tables))
        return {one.key: one for one in needs.collect(lib, {})}

    def test_a_table_of_a_game_on_vps_with_no_tie_is_counted_in_tables(self):
        rows = self._rows(table("g1", game_vps_id="v"), table("g2", game_vps_id="v"))

        self.assertEqual(("t-g1", "t-g2"), rows["untied"].ids)
        self.assertEqual("2 tables", rows["untied"].counted())

    def test_a_tied_table_is_not(self):
        rows = self._rows(table("g1", game_vps_id="v",
                                source={"vps_file_id": "r", "confirmed_by": "exact"}))

        self.assertNotIn("untied", rows)

    def test_a_table_of_a_game_not_on_vps_has_no_release_to_pick(self):
        self.assertNotIn("untied", self._rows(table("g1", game_vps_id="")))

    def test_a_source_that_holds_no_tie_reads_as_untied(self):
        rows = self._rows(table("g1", game_vps_id="v", source={"hash": "h"}))

        self.assertEqual(("t-g1",), rows["untied"].ids)

    def test_a_table_a_patch_made_has_no_release_to_pick(self):
        rows = self._rows(table("g1", game_vps_id="v", source={
            "base": {"file": "a.vpx"}, "patch": {"format": "jojodiff"}}))

        self.assertNotIn("untied", rows)

    def test_a_hidden_table_kept_as_a_patch_base_has_none_either(self):
        rows = self._rows(
            table("g1", game_vps_id="v", hidden=True),
            {**table("g2", game_vps_id="v"), "source": {"base": {"table_id": "t-g1"}}})

        self.assertNotIn("untied", rows)

    def test_a_hidden_table_that_is_no_patch_base_still_counts(self):
        rows = self._rows(table("g1", game_vps_id="v", hidden=True))

        self.assertEqual(("t-g1",), rows["untied"].ids)

    def test_a_table_a_person_took_the_release_from_is_untied(self):
        rows = self._rows(table("g1", game_vps_id="v",
                                source={"vps_file_id": "", "confirmed_by": "user"}))

        self.assertEqual(("t-g1",), rows["untied"].ids)

    def test_the_row_opens_the_tables_grid_on_them_and_is_tidy_up(self):
        row = self._rows(table("g1", game_vps_id="v"))["untied"]

        self.assertEqual(("tables", games.UNTIED), (row.opens.view, row.opens.arriving))
        self.assertEqual(needs.MUTED, row.tone)
        self.assertEqual("No VPS Release Matched", row.label)

    def test_it_can_be_set_aside_and_named_while_it_is_not_checked(self):
        self.assertTrue(needs.can_set_aside("untied"))
        self.assertEqual("No VPS Release Matched", needs.name_of("untied"))
        self.assertTrue(needs.comes_back("untied"))
        arranged = needs.arrange([], {}, {"untied"})
        self.assertEqual(["untied"], [one.key for one in arranged.aside])

    def test_the_grid_reads_the_same_state_from_the_same_rows(self):
        built = games.table_rows([
            {"id": "a", "game_id": "g", "game_vps_id": "v"},
            {"id": "b", "game_id": "g", "game_vps_id": "v",
             "source": {"vps_file_id": "r", "confirmed_by": "user"}},
            {"id": "c", "game_id": "g"}])

        self.assertEqual(["untied", "user", ""], [row["release"] for row in built])
        self.assertIn("release", [column["field"] for column in games.TABLE_COLUMNS])

    def test_the_view_filters_to_the_untied(self):
        preset = games.TABLE_VIEWS["manager.view.untied"]

        self.assertEqual(games.UNTIED, preset.filters)


class NewerReleases(unittest.TestCase):
    def test_a_newer_release_on_vps_is_no_row_of_what_needs_you(self):
        lib = FakeLibrary(tables=[table("a", rom_installed=True, update_available=True)])
        self.assertNotIn("newer", keys(needs.collect(lib, {})))
        self.assertNotIn("newer", needs.ORDER)


class TheGamesNoSourceCanFill(unittest.TestCase):
    def test_the_stamp_marks_only_the_games_in_the_list(self):
        built = [{"id": "g1"}, {"id": "g2"}]
        stamped = games.stamp_unfilled(built, {"g1"})
        self.assertEqual([[games.unfilled_label()], []],
                         [r[games.UNFILLED_COLUMN] for r in stamped])
        self.assertNotIn(games.UNFILLED_COLUMN, built[0])

    def test_nothing_is_stamped_without_a_list(self):
        built = [{"id": "g1"}]
        self.assertIs(built, games.stamp_unfilled(built, None))

    def test_the_grid_a_row_opens_holds_as_many_games_as_the_row_counts(self):
        lib = FakeLibrary([game("g1"), game("g2"), game("g3")])
        lib.offered = {"playfield": {"g1"}}
        row = next(one for one in needs.collect(lib, {}) if one.key == "no_playfield")
        ids = row.opens.keys[games.UNFILLED_KEY]
        stamped = games.stamp_unfilled([{"id": g} for g in ("g1", "g2", "g3")], ids)
        self.assertEqual(row.count, sum(1 for r in stamped if r[games.UNFILLED_COLUMN]))


RAISED = {"extension": "site", "display_name": "Site", "kind": "share_down",
          "key": "ext:site:share_down", "label": "3 plays are down", "says": "Not sent",
          "tone": "warm", "opens": "list", "to": "plays", "count": 3,
          "covered": ["a", "b", "c"]}


class WhatExtensionsAskFor(unittest.TestCase):
    def test_a_row_is_drawn_after_core_s_with_the_extension_named(self):
        lib = FakeLibrary([game("g1", year="", **present("playfield", "backglass"))],
                          watched={"extension_needs": [RAISED]})

        rows = needs.collect(lib, {})

        self.assertEqual(["no_year", "ext:site:share_down"], keys(rows))
        one = rows[-1]
        self.assertEqual(("3 plays are down", "Not sent", "Site", 3, "warm"),
                         (one.label, one.tip, one.source, one.count, one.tone))

    def test_each_kind_of_act_opens_its_own_place(self):
        opened = {kind: needs.extension_rows([{**RAISED, "opens": kind, "to": "x"}])[0].opens
                  for kind in ("list", "page", "action")}

        self.assertEqual("community:site:x", opened["list"].view)
        self.assertEqual("page:site:x", opened["page"].view)
        self.assertEqual(("site", "x", ""),
                         (opened["action"].extension, opened["action"].action,
                          opened["action"].view))

    def test_a_row_can_be_dismissed_and_comes_back_for_a_different_token(self):
        row = needs.extension_rows([RAISED])[0]
        record = {row.key: ["a", "b", "c"]}

        self.assertEqual([], needs.arrange([row], record, set()).rows)
        arranged = needs.arrange([needs.extension_rows([{**RAISED, "covered": ["a", "z"]}])[0]],
                                 record, set())
        self.assertEqual(("z",), arranged.rows[0].new)

    def test_a_dismissal_outlasts_an_extension_that_could_not_answer(self):
        record = {"ext:site:share_down": ["a"], "ext:other:old": ["b"]}

        away = needs.arrange([], record, set(), [need_keys.AWAY + "site"])
        every = needs.arrange([], record, set(), [need_keys.AWAY_ALL])
        read = needs.arrange([], record, set(), [])

        self.assertEqual({"ext:site:share_down": ["a"]}, away.record)
        self.assertEqual(record, every.record)
        self.assertEqual({}, read.record)

    def test_it_can_be_set_aside_and_stopped_as_a_row_of_this_install(self):
        self.assertTrue(needs.can_set_aside("ext:site:share_down"))
        self.assertTrue(needs.is_extension("ext:site:share_down"))
        self.assertTrue(needs.is_device("ext:site:share_down"))
        self.assertIn("extension", needs.comes_back("ext:site:share_down"))

    def test_a_stopped_row_is_set_aside_as_not_checked_and_the_extension_s_others_stay(self):
        other = {**RAISED, "kind": "other", "key": "ext:site:other", "label": "1 thing"}
        lib = FakeLibrary(watched={"extension_needs": [RAISED, other]},
                          hidden={"ext:site:share_down"})

        arranged = needs.arrange(needs.collect(lib, {}), {}, lib.stopped())

        self.assertEqual(["ext:site:other"], [one.key for one in arranged.rows
                                              if needs.is_extension(one.key)])
        (aside,) = [one for one in arranged.aside if one.key == "ext:site:share_down"]
        self.assertEqual(("3 plays are down", True), (aside.label, aside.stopped))

    def test_a_stopped_row_keeps_what_was_dismissed_over_it(self):
        row = needs.extension_rows([{**RAISED, "covered": ["a"]}])[0]

        arranged = needs.arrange([row], {row.key: ["a"]}, {row.key})

        self.assertEqual({row.key: ["a"]}, arranged.record)

    def test_a_row_with_only_a_count_says_it_waits_for_the_extension_to_stop_asking(self):
        by_ids = needs.extension_rows([RAISED])[0]
        by_kind = needs.extension_rows([{**RAISED, "covered": ["share_down"]}])[0]

        self.assertFalse(by_ids.by_kind)
        self.assertTrue(by_kind.by_kind)
        self.assertEqual(t("manager.needs.back.extension"),
                         needs.comes_back(by_ids.key, by_ids))
        self.assertEqual(t("manager.needs.back.extension_kind"),
                         needs.comes_back(by_kind.key, by_kind))
        self.assertEqual([], needs.arrange([by_kind], {by_kind.key: ["share_down"]}, set()).rows)

    def test_a_read_that_failed_is_said_and_stays_in_the_row_that_says_so(self):
        lib = FakeLibrary(watched={"unread": ["extensions", need_keys.AWAY + "site"]})

        rows = needs.collect(lib, {})

        self.assertEqual(["unread"], keys(rows))
        self.assertIn("what extensions ask for", rows[0].tip)

    def test_the_read_collects_what_the_api_gave_and_who_was_away(self):
        client = SimpleNamespace(
            update_check=lambda: {}, capture_proposals=lambda: {"proposals": []},
            jobs=lambda limit: [],
            extension_needs=lambda: {"needs": [RAISED], "away": ["old"], "failed": ["old"]},
            unaccepted_themes=lambda: {"themes": []}, extensions=lambda: [],
            launchers=lambda: {"launchers": []},
            all_media=lambda: [], devices=lambda: [], library_policy=lambda: {})
        library = Library.__new__(Library)
        library._client = client

        read = library._read_watched()

        self.assertEqual([RAISED], read["extension_needs"])
        self.assertEqual(["away:old", "extensions"], read["unread"])


class WhatWaitsToBeAccepted(unittest.TestCase):
    THEME = {"key": "Mine", "name": "My Theme"}
    EXTENSION = {"name": "tool", "display_name": "Tool", "reason_key": host.NOT_ACCEPTED}

    def test_each_theme_and_extension_is_a_row_of_this_device(self):
        rows = needs.unaccepted_rows({"themes": [self.THEME], "extensions": [self.EXTENSION]})

        self.assertEqual(["unaccepted:theme:Mine", "unaccepted:ext:tool"], keys(rows))
        self.assertEqual(["This Device"], [one.label for one in needs.grouped(rows)])
        self.assertEqual([needs.DEVICE, needs.DEVICE], [needs.owner_of(one) for one in rows])

    def test_the_row_names_it_and_opens_where_it_is_accepted(self):
        theme, extension = needs.unaccepted_rows(
            {"themes": [self.THEME], "extensions": [self.EXTENSION]})

        self.assertEqual(t("manager.needs.untrusted", name="My Theme"), theme.label)
        self.assertEqual(t("manager.needs.untrusted", name="Tool"), extension.label)
        self.assertEqual("themes", theme.opens.view)
        self.assertEqual("settings", extension.opens.view)
        self.assertEqual(t("manager.needs.untrusted.tip"), theme.tip)

    def test_two_themes_are_one_row_and_an_extension_still_has_its_own(self):
        rows = needs.unaccepted_rows({"themes": [self.THEME, {"key": "Other", "name": "Other"}],
                                      "extensions": [self.EXTENSION]})

        self.assertEqual(["unaccepted:themes", "unaccepted:ext:tool"], keys(rows))
        self.assertEqual(t("manager.needs.untrusted_themes", count=2), rows[0].label)
        self.assertEqual("themes", rows[0].opens.view)
        self.assertFalse(needs.can_set_aside("unaccepted:themes"))

    def test_a_row_cannot_be_set_aside(self):
        self.assertFalse(needs.can_set_aside("unaccepted:theme:Mine"))
        self.assertFalse(needs.can_set_aside("unaccepted:ext:tool"))

    def test_nothing_waiting_is_no_row(self):
        self.assertEqual([], needs.unaccepted_rows({}))

    def test_collect_draws_them_from_the_watched_read(self):
        lib = FakeLibrary(watched={"unaccepted": {"themes": [self.THEME], "extensions": []}})

        self.assertEqual(["unaccepted:theme:Mine"], keys(needs.collect(lib, {})))

    def test_the_read_takes_the_themes_and_the_extensions_that_wait(self):
        waiting = {"name": "tool", "reason_key": None,
                   "waiting": {"reason_key": host.NOT_ACCEPTED}}
        running = {"name": "fine", "reason_key": None}
        library = self.library(extensions=lambda: [self.EXTENSION, waiting, running])

        read = library._read_watched()

        self.assertEqual([self.THEME], read["unaccepted"]["themes"])
        self.assertEqual([self.EXTENSION, waiting], read["unaccepted"]["extensions"])
        self.assertNotIn("unaccepted", read["unread"])

    def test_a_read_that_failed_is_said(self):
        def failing():
            raise OSError("down")

        library = self.library(unaccepted_themes=failing)

        self.assertIn("unaccepted", library._read_watched()["unread"])
        self.assertIn(t("manager.needs.unread_untrusted"), needs.collect(
            FakeLibrary(watched={"unread": ["unaccepted"]}), {})[0].tip)

    def library(self, **replaced):
        client = SimpleNamespace(
            update_check=lambda: {}, capture_proposals=lambda: {"proposals": []},
            jobs=lambda limit: [], extension_needs=lambda: {},
            unaccepted_themes=lambda: {"themes": [self.THEME]}, extensions=lambda: [],
            launchers=lambda: {"launchers": []},
            all_media=lambda: [], devices=lambda: [], library_policy=lambda: {})
        for name, value in replaced.items():
            setattr(client, name, value)
        library = Library.__new__(Library)
        library._client = client
        return library


class WhatProgramsAsk(unittest.TestCase):
    ASK = {"id": "a1", "token": "Home", "method": "DELETE", "path": "/games/a/folder",
           "body": {"force": True}, "state": "waiting", "expires_in": 3000}

    def test_each_ask_is_a_row_of_this_device_that_names_the_exact_change(self):
        (row,) = needs.asked_rows([self.ASK])

        self.assertEqual("asked:a1", row.key)
        self.assertEqual(t("manager.needs.asked", program="Home"), row.label)
        self.assertEqual('DELETE /games/a/folder {"force":true}', row.detail)
        self.assertEqual("a1", row.answer)
        self.assertEqual(needs.DEVICE, needs.owner_of(row))

    def test_the_tip_says_how_long_it_has(self):
        (row,) = needs.asked_rows([self.ASK])

        self.assertIn("within 50 minutes", row.tip)

    def test_a_row_is_answered_and_never_set_aside(self):
        self.assertFalse(needs.can_set_aside("asked:a1"))

    def test_a_request_with_no_body_names_only_where_it_goes(self):
        (row,) = needs.asked_rows([{**self.ASK, "body": None}])

        self.assertEqual("DELETE /games/a/folder", row.detail)

    def test_an_empty_body_is_still_shown_as_one(self):
        (row,) = needs.asked_rows([{**self.ASK, "body": {}}])

        self.assertEqual("DELETE /games/a/folder {}", row.detail)

    def test_collect_draws_them_from_the_watched_read(self):
        lib = FakeLibrary(watched={"asked": [self.ASK]})

        self.assertEqual(["asked:a1"], keys(needs.collect(lib, {})))

    def test_nothing_waiting_is_no_row(self):
        self.assertEqual([], needs.asked_rows([]))
        self.assertEqual([], needs.asked_rows(()))

    def test_the_read_takes_what_the_api_lists_and_a_failed_read_is_no_rows(self):
        def library(asks):
            client = SimpleNamespace(
                update_check=lambda: {}, capture_proposals=lambda: {"proposals": []},
                jobs=lambda limit: [], extension_needs=lambda: {},
                unaccepted_themes=lambda: {"themes": []}, extensions=lambda: [], asks=asks,
                launchers=lambda: {"launchers": []},
                all_media=lambda: [], devices=lambda: [], library_policy=lambda: {})
            read = Library.__new__(Library)
            read._client = client
            return read

        def refused():
            raise OSError("not allowed")

        self.assertEqual([self.ASK], library(lambda: [self.ASK])._read_watched()["asked"])
        self.assertNotIn("asked", library(refused)._read_watched())
        self.assertEqual([], library(refused)._read_watched()["unread"])

    def test_an_answered_ask_leaves_what_overview_holds(self):
        answered = []
        library = Library.__new__(Library)
        library._client = SimpleNamespace(answer_ask=lambda ask_id, approve:
                                          answered.append((ask_id, approve)))
        library._watched = {"asked": [self.ASK, {**self.ASK, "id": "a2"}]}

        library.answer_ask("a1", True)

        self.assertEqual([("a1", True)], answered)
        self.assertEqual(["a2"], [one["id"] for one in library._watched["asked"]])



def left(launcher_id="vpx", *items, name="Visual Pinball X"):
    return {"launcher_id": launcher_id, "name": name, "items": list(items)}


def item(key, action="move", files=0):
    return {"key": key, "action": action, "files": files}


class WhatAnUpgradeLeftBehind(unittest.TestCase):
    def row(self, *held):
        return needs.leftovers_row(held)

    def test_a_launcher_with_nothing_to_act_on_has_no_row(self):
        self.assertIsNone(self.row())
        self.assertIsNone(self.row(left("vpx", item("A.b", "name"))))

    def test_a_box_with_no_file_to_go_into_is_not_counted(self):
        self.assertIsNone(self.row(left("vpx", item("A.box", "place", 0))))
        self.assertEqual(("vpx:A.box",),
                         self.row(left("vpx", item("A.box", "place", 2))).ids)

    def test_a_move_and_a_turn_on_are_counted_by_launcher_and_key(self):
        row = self.row(left("vpx", item("A.b"), item("A.c", "turn_on"), item("A.d", "name")),
                       left("other", item("A.b")))

        self.assertEqual(("other:A.b", "vpx:A.b", "vpx:A.c"), row.ids)
        self.assertEqual("manager.needs.settings_count", row.unit)

    def test_a_click_opens_the_first_launcher_that_has_some(self):
        row = self.row(left("a", item("A.d", "name")), left("b", item("A.b")))

        self.assertEqual("launchers", row.opens.view)
        self.assertEqual({"launcher": "b"}, dict(row.opens.keys))

    def test_it_is_this_installs_row_between_failures_and_updates(self):
        self.assertTrue(needs.is_device("leftovers"))
        self.assertEqual(needs.ORDER.index("fill_errors") + 1, needs.ORDER.index("leftovers"))
        self.assertEqual(needs.ORDER.index("leftovers") + 1, needs.ORDER.index("update"))

    def test_collect_draws_it_from_the_watched_read(self):
        lib = FakeLibrary(watched={"leftovers": [left("vpx", item("A.b"))]})

        self.assertEqual(["leftovers"], keys(needs.collect(lib, {})))

    def test_not_now_keeps_it_away_until_an_upgrade_leaves_more(self):
        lib = FakeLibrary(watched={"leftovers": [left("vpx", item("A.b"))]})
        (row,) = needs.collect(lib, {})
        self.assertEqual([], needs.arrange([row], {"leftovers": ["vpx:A.b"]}, set()).rows)

        lib = FakeLibrary(watched={"leftovers": [left("vpx", item("A.b"), item("A.c"))]})
        (row,) = needs.collect(lib, {})
        arranged = needs.arrange([row], {"leftovers": ["vpx:A.b"]}, set())

        self.assertEqual(["leftovers"], keys(arranged.rows))
        self.assertEqual(("vpx:A.c",), arranged.rows[0].new)

    def test_a_read_that_failed_is_said_and_keeps_the_dismissal(self):
        lib = FakeLibrary(watched={"unread": ["leftovers"]})

        self.assertIn(t("manager.needs.unread_leftovers"), needs.collect(lib, {})[0].tip)
        self.assertEqual({"leftovers": ["vpx:A.b"]},
                         needs.arrange([], {"leftovers": ["vpx:A.b"]}, set(),
                                       ["leftovers"]).record)

    def test_the_read_takes_each_enabled_launcher_with_settings_and_skips_the_rest(self):
        launchers = [{"launcher_id": "a", "display_name": "A", "has_config": True,
                      "enabled": True},
                     {"launcher_id": "b", "has_config": False, "enabled": True},
                     {"launcher_id": "c", "has_config": True, "enabled": False},
                     {"launcher_id": "d", "has_config": True, "enabled": True}]
        said = {"a": [item("A.b")], "d": []}
        library = Library.__new__(Library)
        library._client = SimpleNamespace(launchers=lambda: {"launchers": launchers},
                                          launcher_leftovers=lambda one: {"items": said[one]})

        self.assertEqual([{"launcher_id": "a", "name": "A", "items": [item("A.b")]}],
                         library._read_leftovers())


if __name__ == "__main__":
    unittest.main()
