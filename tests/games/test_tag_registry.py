"""What a tag means and what color it wears, and that it follows a rename."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.games import library_ops, tag_registry
from tests.support.library import TempTree, fake_game


def _game(game_id: str, tags: list[str]):
    meta = {"VPinFE": {"game_id": game_id}, "User": {"Tags": tags}}
    return fake_game(f"/nowhere/{game_id}", game_id, meta=meta)


class Registry(TempTree):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch("common.config.paths.TAGS_PATH", self.root / "tags.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_tag_nobody_described_wears_a_calm_derived_color(self) -> None:
        said = tag_registry.describe("Wide Body")

        self.assertEqual(("", False), (said["description"], said["chosen"]))
        self.assertIn(said["color"], tag_registry.DERIVED)
        self.assertEqual(said["color"], tag_registry.describe("Wide Body")["color"])

    def test_a_derived_color_never_calls_attention(self) -> None:
        self.assertFalse({"red", "orange", "amber"} & set(tag_registry.DERIVED))
        self.assertTrue(set(tag_registry.DERIVED) < set(tag_registry.COLORS))

    def test_a_chosen_color_and_a_description_are_kept(self) -> None:
        tag_registry.put("Wide Body", description="Stern wide  cabinets", color="amber")

        self.assertEqual({"description": "Stern wide cabinets", "color": "amber",
                          "chosen": True}, tag_registry.describe("Wide Body"))

    def test_an_empty_color_goes_back_to_the_derived_one(self) -> None:
        tag_registry.put("Wide Body", color="red")
        tag_registry.put("Wide Body", color="")

        self.assertFalse(tag_registry.describe("Wide Body")["chosen"])

    def test_a_color_the_palettes_do_not_have_is_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            tag_registry.put("Wide Body", color="#ff0000")

    def test_a_rename_carries_the_entry(self) -> None:
        tag_registry.put("widebody", description="Wide ones")

        tag_registry.moved(["widebody"], "Wide Body")

        self.assertEqual(["Wide Body"], list(tag_registry.load()))

    def test_a_merge_keeps_the_survivor_s_own_entry(self) -> None:
        tag_registry.put("Wide Body", description="Kept")
        tag_registry.put("widebody", description="Dropped")

        tag_registry.moved(["widebody"], "Wide Body")

        self.assertEqual({"Wide Body": {"description": "Kept", "color": ""}},
                         tag_registry.load())


class TheSweepComesFirst(TempTree):
    def setUp(self) -> None:
        super().setUp()
        replacements: tuple[tuple[str, Any], ...] = (
            ("common.config.paths.TAGS_PATH", self.root / "tags.json"),
            ("common.games.library_folders.blocked_games", lambda games: []),
            ("common.games.game_repository.all_games",
             lambda: [_game("g1", ["widebody"])]))
        for target, value in replacements:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        tag_registry.put("widebody", description="Wide ones")

    def test_a_sweep_that_fails_leaves_the_registry_as_it_was(self) -> None:
        with patch("common.games.library_ops.retag_library", side_effect=OSError("disk")), \
                self.assertRaises(OSError):
            library_ops.merge_tags(["widebody"], "Wide Body")

        self.assertEqual(["widebody"], list(tag_registry.load()))

    def test_deleting_a_tag_takes_its_entry(self) -> None:
        with patch("common.games.library_ops.retag_library", return_value=1):
            library_ops.drop_tag("widebody")

        self.assertEqual({}, tag_registry.load())


class OverHttp(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "tags.json"
        for target, value in (("common.config.paths.TAGS_PATH", self.path),
                              ("common.games.game_repository.all_games",
                               lambda: [_game("g1", ["Wide Body"]),
                                        _game("g2", ["Wide Body", "EM"])])):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_every_tag_is_listed_with_its_games(self) -> None:
        tag_registry.put("Someday")

        said = self.client.get("/library/tags").json()["tags"]

        self.assertEqual([("EM", 1), ("Someday", 0), ("Wide Body", 2)],
                         [(one["name"], one["games"]) for one in said])

    def test_a_put_writes_the_entry_down(self) -> None:
        response = self.client.put("/library/tags/Wide Body",
                                   json={"description": "Wide", "color": "teal"})

        self.assertEqual((200, 2, "teal"), (response.status_code, response.json()["games"],
                                            response.json()["color"]))
        self.assertEqual("teal", json.loads(self.path.read_text())["tags"]["Wide Body"]
                         ["color"])

    def test_a_color_that_is_not_one_is_refused(self) -> None:
        self.assertEqual(400, self.client.put("/library/tags/EM",
                                              json={"color": "chartreuse"}).status_code)
