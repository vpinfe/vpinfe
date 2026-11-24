"""Tags an extension's Community list derives: where they land, what keeps them, and who
may move them.

A test extension holds two tagged lists, one by machine and one by release, and each test
says which ids are on them this week.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from starlette.testclient import TestClient

import httpapi
from common import events, extensions, service_errors
from common.extensions import host, store
from common.games import community_lists, derived_tags, library_ops, rankings
from common.games.collection_resolver import resolve
from common.games.collection_store import CollectionStore
from common.games.game import Game
from common.games.game_identity import game_id
from common.games.tables import table_entries
from common.i18n import t
from tests.support.accepted import owner_accepts

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "community"
MACHINE = "Machine of the Month"
CHALLENGE = "Weekly Challenge"


def _game(gid: str, title: str, entry: str, releases: dict[str, str],
          default: str) -> Game:
    return Game(game_dir_name=title, creation_time=0, meta_config={
        "Info": {"Title": title, "VPSId": entry},
        "User": {},
        "vpinfe": {"game_id": gid, "default_table": default},
        "tables": {key: {"id": key, "filename": f"{title} {key}.vpx",
                         "source": {"vps_file_id": release}}
                   for key, release in releases.items()},
    })


def _unanswered(_path: str) -> dict:
    sent = requests.Request("GET", "https://vpinplay.example/api").prepare()
    raise requests.ConnectionError(ConnectionRefusedError(), request=sent)


class DerivedTagCase(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for target, value in (("common.config.paths.DERIVED_TAGS_PATH", self.root / "derived.json"),
                              ("common.config.paths.TAGS_PATH", self.root / "tags.json"),
                              ("common.config.paths.COMMUNITY_KEPT_DIR", self.root / "kept")):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for held in (derived_tags, rankings):
            held.forget()
            self.addCleanup(held.forget)

        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)
        self.registry.load_from(ROOT)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        # Two builds of one machine, the default the older, and a machine with one.
        self.afm = _game("afm", "Attack from Mars", "afm-entry",
                         {"old": "afm-1-2", "new": "afm-1-3"}, default="old")
        self.mm = _game("mm", "Medieval Madness", "mm-entry", {"vpw": "mm-vpw"},
                        default="vpw")
        self.games = [self.afm, self.mm]
        listing = patch("common.games.game_repository.all_games", return_value=self.games)
        listing.start()
        self.addCleanup(listing.stop)

    def week(self, machines: str = "", releases: str = "", fail: bool = False,
             ratings: str = "", builds: str = "") -> None:
        self.store.set_setting("challenge", "machines", machines)
        self.store.set_setting("challenge", "releases", releases)
        self.store.set_setting("challenge", "ratings", ratings)
        self.store.set_setting("challenge", "builds", builds)
        self.store.set_setting("challenge", "fail", "yes" if fail else "")

    def fetch(self, path: str) -> dict:
        answer = self.client.get(path)
        answer.raise_for_status()
        return answer.json()

    def read(self) -> bool:
        return community_lists.refresh(self.fetch)

    def table(self, game: Game, key: str) -> dict:
        return table_entries(game.meta_config)[key]


class WhereTheyLand(DerivedTagCase):
    def test_a_machine_on_the_list_tags_the_game(self) -> None:
        self.week(machines="mm-entry")
        self.read()

        self.assertEqual([MACHINE], derived_tags.game_tags(self.mm))
        self.assertEqual([], derived_tags.table_tags(self.table(self.mm, "vpw")))
        self.assertEqual([], derived_tags.game_tags(self.afm))

    def test_a_release_on_the_list_tags_only_that_table(self) -> None:
        self.week(releases="afm-1-3,tz-not-owned")
        self.read()

        self.assertEqual([CHALLENGE], derived_tags.table_tags(self.table(self.afm, "new")))
        self.assertEqual([], derived_tags.table_tags(self.table(self.afm, "old")))
        self.assertEqual([], derived_tags.game_tags(self.afm))

    def test_a_rule_on_a_release_tag_takes_that_table_not_the_default(self) -> None:
        self.week(releases="afm-1-3")
        self.read()
        collections = CollectionStore(str(self.root / "collections.json"))
        collections.add_collection("Challenge")
        collections.make_filter_collection("Challenge", {"tags": CHALLENGE})

        entries = resolve("Challenge", collections, self.games)

        self.assertEqual([("afm", "new")],
                         [(game_id(entry.game), entry.table_id) for entry in entries])

    def test_a_version_you_do_not_have_tags_nothing(self) -> None:
        """So the challenge's collection leaves the machine off the cabinet rather than
        offering a build whose scores do not count."""
        self.week(releases="afm-1-4")
        self.read()
        collections = CollectionStore(str(self.root / "collections.json"))
        collections.add_collection("Challenge")
        collections.make_filter_collection("Challenge", {"tags": CHALLENGE})

        self.assertEqual([], resolve("Challenge", collections, self.games))

    def test_a_list_of_vpinfe_ids_tags_by_this_library_own_ids(self) -> None:
        """A game and a table are told apart by the ids VPinFE gave them, not VPS's."""
        kept = [{"key": "challenge/kept", "extension": "challenge", "display_name": "",
                 "list": "kept", "title": "Kept", "base": "", "tag": "Kept",
                 "field": "id", "keys": keys, "ids": [one], "read_at": "", "stale": False,
                 "error": ""}
                for keys, one in (("vpinfe_game", "mm"), ("vpinfe_table", "new"))]
        with patch.object(derived_tags, "declared", return_value=kept):
            derived_tags.forget()
            self.assertEqual(["Kept"], derived_tags.game_tags(self.mm))
            self.assertEqual([], derived_tags.game_tags(self.afm))
            self.assertEqual(["Kept"], derived_tags.table_tags(self.table(self.afm, "new")))
            self.assertEqual([], derived_tags.table_tags(self.table(self.afm, "old")))

    def test_the_tags_list_counts_them_and_names_their_source(self) -> None:
        self.week(machines="mm-entry", releases="afm-1-3")
        self.read()

        said = {one["name"]: one for one in library_ops.tags()["tags"]}

        self.assertEqual((1, 0), (said[MACHINE]["games"], said[MACHINE]["tables"]))
        self.assertEqual((1, 1), (said[CHALLENGE]["games"], said[CHALLENGE]["tables"]))
        (source,) = said[CHALLENGE]["sources"]
        self.assertEqual(("challenge", "releases", "Weekly Challenge", False),
                         (source["extension"], source["list"], source["title"],
                          source["stale"]))
        self.assertTrue(source["read_at"])


class WhatKeepsThem(DerivedTagCase):
    def test_a_failed_read_keeps_the_last_good_one_and_says_it_is_stale(self) -> None:
        self.week(releases="afm-1-3")
        self.read()
        self.week(releases="", fail=True)
        self.read()

        self.assertEqual([CHALLENGE], derived_tags.table_tags(self.table(self.afm, "new")))
        (source,) = derived_tags.sources()[CHALLENGE]
        self.assertTrue(source["stale"])

    def test_a_failed_read_says_why_where_the_tag_is_listed(self) -> None:
        self.week(releases="afm-1-3")
        self.read()

        community_lists.refresh(_unanswered)
        listed = self.client.get("/library/tags").json()["tags"]

        (source,) = next(one["sources"] for one in listed if one["name"] == CHALLENGE)
        self.assertEqual((True, t("said.why.no_answer_at", host="vpinplay.example")),
                         (source["stale"], source["error"]))

    def test_the_last_good_read_outlives_a_restart(self) -> None:
        self.week(releases="afm-1-3")
        self.read()
        derived_tags.forget()

        self.assertEqual([CHALLENGE], derived_tags.table_tags(self.table(self.afm, "new")))

    def test_a_stopped_extension_takes_its_tags_with_it(self) -> None:
        self.week(machines="mm-entry")
        self.read()
        self.registry.disable("challenge", "switched off")

        self.assertEqual([], derived_tags.game_tags(self.mm))
        self.assertNotIn(MACHINE, {one["name"] for one in library_ops.tags()["tags"]})

    def test_a_read_that_changes_the_list_tells_vpinfe(self) -> None:
        told: list[dict] = []

        def heard(**payload) -> None:
            told.append(payload)

        events.subscribe(events.COLLECTIONS_CHANGED, heard)
        self.addCleanup(events.unsubscribe, events.COLLECTIONS_CHANGED, heard)
        self.week(releases="afm-1-3")

        self.assertTrue(self.read())
        self.assertFalse(self.read())
        self.assertEqual(1, len(told))


class WhoMovesThem(DerivedTagCase):
    def setUp(self) -> None:
        super().setUp()
        self.week(machines="mm-entry")
        self.read()

    def test_renaming_merging_or_removing_one_is_refused(self) -> None:
        for attempt in (lambda: library_ops.merge_tags([MACHINE], "Renamed"),
                        lambda: library_ops.merge_tags(["Mine"], MACHINE),
                        lambda: library_ops.drop_tag(MACHINE)):
            with self.subTest(), self.assertRaises(service_errors.RefusedError):
                attempt()

    def test_putting_one_on_by_hand_is_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            derived_tags.refuse_added(self.afm, ["Mine", MACHINE])

    def test_one_the_user_already_had_does_not_block_an_edit(self) -> None:
        assert self.afm.meta_config is not None
        self.afm.meta_config["User"]["Tags"] = [MACHINE]

        derived_tags.refuse_added(self.afm, [MACHINE, "Mine"])

    def test_its_color_and_description_are_still_the_users(self) -> None:
        said = library_ops.put_tag(MACHINE, "This month's machine", "teal")

        self.assertEqual(("This month's machine", "teal"),
                         (said["description"], said["color"]))


if __name__ == "__main__":
    unittest.main()
