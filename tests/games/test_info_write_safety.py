"""Surviving an interrupted write, and a file that did not survive one.

The id backfill rewrites every .info in the library in one burst at first launch, so the
window for being interrupted mid-write went from "one file while somebody plays" to "the
whole library, at the worst moment to reboot". These are the two guards that makes safe.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from common.games.game_parser import GameParser
from common.games.info_file import MetaConfig
from common.games.info_migration import write_json_atomic
from tests.support.library import TempTree


class AtomicWriteTests(TempTree):
    def setUp(self):
        super().setUp()
        self.info = self.root / "Example.info"

    def test_an_interrupted_write_leaves_the_previous_file_intact(self):
        """The point of the whole thing: a reader sees the old file or the new one."""
        self.info.write_text(json.dumps({"User": {"Rating": 4}}), encoding="utf-8")

        with mock.patch("json.dump", side_effect=OSError("share went away")):
            with self.assertRaises(OSError):
                write_json_atomic(str(self.info), {"User": {"Rating": 5}})

        self.assertEqual(json.loads(self.info.read_text(encoding="utf-8")),
                         {"User": {"Rating": 4}})

    def test_an_interrupted_write_leaves_no_debris_behind(self):
        self.info.write_text(json.dumps({"a": 1}), encoding="utf-8")

        with mock.patch("json.dump", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                write_json_atomic(str(self.info), {"a": 2})

        self.assertEqual([p.name for p in self.root.iterdir()], ["Example.info"])

    def test_the_temp_file_is_never_mistaken_for_metadata(self):
        """It lands in the game folder, so a scan must not read it as an .info or a
        backup while it is there."""
        seen = {}

        def capture(data, handle, **kwargs):
            seen["names"] = sorted(p.name for p in self.root.iterdir())
            handle.write("{}")

        with mock.patch("json.dump", side_effect=capture):
            write_json_atomic(str(self.info), {"a": 1})

        temps = [n for n in seen["names"] if n != "Example.info"]
        self.assertEqual(len(temps), 1)
        self.assertTrue(temps[0].startswith("."), "hidden, so a folder listing ignores it")
        self.assertFalse(temps[0].endswith(".info"))
        self.assertNotIn(".vpinfe-", temps[0], "must not look like a restore point")

    def test_a_normal_write_still_lands(self):
        MetaConfig(str(self.info)).write_config()

        self.assertTrue(self.info.exists())
        self.assertIsInstance(json.loads(self.info.read_text(encoding="utf-8")), dict)


class UnreadableGameTests(TempTree):

    def _game(self, name, info_text):
        d = self.root / name
        d.mkdir()
        (d / f"{name}.vpx").write_text("not really a vpx", encoding="utf-8")
        (d / f"{name}.info").write_text(info_text, encoding="utf-8")
        return d

    def test_one_truncated_file_costs_one_game_not_the_library(self):
        """It used to cost all of them: the error came out of load_games and the app saw
        zero tables, so a single bad file looked like an empty library."""
        good = json.dumps({"Info": {}, "User": {}})
        self._game("Good One", good)
        self._game("Bad One", "{ truncated")
        self._game("Good Two", good)

        parser = GameParser(str(self.root))

        self.assertEqual(parser.get_game_count(), 2)
        self.assertEqual([r["folder"] for r in parser.get_unreadable_games()], ["Bad One"])

    def test_an_empty_file_counts_as_unreadable_too(self):
        self._game("Good One", json.dumps({"Info": {}}))
        self._game("Empty One", "")

        parser = GameParser(str(self.root))

        self.assertEqual(parser.get_game_count(), 1)
        self.assertEqual(len(parser.get_unreadable_games()), 1)

    def test_the_unreadable_file_is_left_alone(self):
        """Excluded rather than loaded empty, so nothing can write over a file we could
        not read."""
        bad = self._game("Bad One", "{ truncated")

        GameParser(str(self.root))

        self.assertEqual((bad / "Bad One.info").read_text(encoding="utf-8"), "{ truncated")

    def test_a_fixed_file_comes_back_on_the_next_scan(self):
        bad = self._game("Bad One", "{ truncated")
        parser = GameParser(str(self.root))
        self.assertEqual(parser.get_game_count(), 0)

        (bad / "Bad One.info").write_text(json.dumps({"Info": {}}), encoding="utf-8")
        parser.load_games(reload=True)

        self.assertEqual(parser.get_game_count(), 1)
        self.assertEqual(parser.get_unreadable_games(), [])



class StaleCountTests(TempTree):
    """What the Tables page reports after the startup id backfill has run.

    The backfill upgrades the whole library, and it does it to tables the scan has
    already loaded. Anything the scan recorded about "does this need upgrading" is out
    of date the moment it finishes, and the page reads those recorded values.
    """

    def setUp(self):
        super().setUp()
        legacy = {
            "Info": {"Title": "Dr. Dude"},
            "User": {"Rating": 4},
            "VPXFile": {"filename": "Dr. Dude.vpx", "filehash": "abc"},
        }
        for name in ("Dr. Dude", "Taxi"):
            d = self.root / name
            d.mkdir()
            (d / f"{name}.vpx").write_text("x", encoding="utf-8")
            (d / f"{name}.info").write_text(json.dumps(legacy), encoding="utf-8")

    def test_the_backfill_leaves_no_game_still_claiming_it_needs_upgrading(self):
        from common.games.game_identity import ensure_unique_ids

        parser = GameParser(str(self.root))
        games = parser.get_all_games()
        self.assertTrue(all(t.info_pending_upgrade for t in games),
                        "they do need upgrading before the backfill runs")

        ensure_unique_ids(games)

        self.assertEqual([t.game_dir_name for t in games if t.info_pending_upgrade], [])
        self.assertTrue(all(t.info_restorable for t in games),
                        "each upgrade left a restore point")


if __name__ == "__main__":
    unittest.main()
