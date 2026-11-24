"""What a list's `detail(row_id)` may answer: the pane's closed vocabulary."""

from __future__ import annotations

import unittest
from typing import Any

from common.extensions import pane


def answer(**section: Any) -> dict[str, Any]:
    return {"title": "Taxi", "sections": [{"key": "a", **section}]}


def field(**more: Any) -> dict[str, Any]:
    return {"key": "f", "type": "number", "label": "F", "value": 1, **more}


class Fields(unittest.TestCase):
    def shaped(self, one: dict[str, Any]) -> dict[str, Any]:
        return pane.shaped(answer(fields=[one]), "r")["sections"][0]["fields"][0]

    def test_a_limit_is_a_finite_number(self) -> None:
        self.assertEqual((0.0, 9.0), (self.shaped(field(min="0", max=9))["min"],
                                      self.shaped(field(min="0", max=9))["max"]))
        for bad in ("lots", [1], {"n": 1}, True, float("inf"), float("nan")):
            with self.subTest(bad=bad), self.assertRaises(pane.PaneError):
                self.shaped(field(max=bad))

    def test_a_path_wants_one_kind_core_has(self) -> None:
        self.assertEqual("file", self.shaped(field(type="path", wants="file"))["wants"])
        with self.assertRaises(pane.PaneError):
            self.shaped(field(type="path", wants={"x": 1}))

    def test_a_choice_that_is_not_a_pair_is_refused_not_dropped(self) -> None:
        with self.assertRaises(pane.PaneError):
            self.shaped(field(type="select", choices=[["a"], ["b", "B"]]))


class Facts(unittest.TestCase):
    def fact(self, **more: Any) -> dict[str, Any]:
        one = {"label": "L", "value": "v", **more}
        return pane.shaped(answer(facts=[one]), "r")["sections"][0]["facts"][0]

    def test_a_link_needs_an_address_a_browser_can_open(self) -> None:
        self.assertEqual("https://example.com/x",
                         self.fact(kind="link", to="https://example.com/x")["to"])
        for bad in ("", "javascript:alert(1)", "ftp://example.com", "/relative"):
            with self.subTest(bad=bad), self.assertRaises(pane.PaneError):
                self.fact(kind="link", to=bad)

    def test_a_state_takes_a_tier_and_nothing_else_does(self) -> None:
        self.assertEqual("warn", self.fact(kind="state", tier="warn")["tier"])
        with self.assertRaises(pane.PaneError):
            self.fact(kind="text", tier="warn")

    def test_an_icon_is_not_a_key_a_fact_has(self) -> None:
        with self.assertRaises(pane.PaneError):
            self.fact(kind="state", icon="block")



    def test_a_game_or_table_fact_names_whose_ids_it_holds(self) -> None:
        for kind in ("game", "table"):
            with self.subTest(kind=kind):
                one = self.fact(kind=kind, says="Medieval Madness",
                                picture="https://x.test/a.png")
                self.assertEqual(("vps", "Medieval Madness"), (one["keys"], one["says"]))
                self.assertEqual("vpinfe", self.fact(kind=kind, keys="vpinfe")["keys"])
        with self.assertRaises(pane.PaneError):
            self.fact(kind="game", keys="rom")

    def test_says_keys_and_picture_belong_to_a_game_or_table_fact(self) -> None:
        for own in ({"says": "x"}, {"keys": "vps"}, {"picture": "https://x.test/a.png"}):
            with self.subTest(own=own), self.assertRaises(pane.PaneError):
                self.fact(kind="text", **own)


if __name__ == "__main__":
    unittest.main()
