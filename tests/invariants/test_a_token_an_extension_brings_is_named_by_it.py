"""A name a user types into a command says where it came from.

The shape is what is checked: core's names bare, an extension's owned and dotted, and
gone when it unloads. Whether a name belongs to core or to an extension is a judgement
and no test here holds it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common import i18n, tokens


class CoreNamesAreBare(unittest.TestCase):
    def test_nothing_core_declares_is_owned(self) -> None:
        for one in tokens.TOKENS:
            with self.subTest(token=one.name):
                self.assertEqual(one.extension, "")
                self.assertRegex(one.name, r"\A[a-z][a-z0-9_]*\Z")


class ABroughtNameCarriesItsId(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("one", "two"):
            tokens.forget(name)

    def offer(self, extension: str, name: str = "player", value: str = "ABC", **kw) -> str:
        return tokens.register(extension, name, frozenset({tokens.TABLE}),
                               lambda _base: value, says="Says something", **kw)

    def test_the_registry_builds_the_name(self) -> None:
        self.assertEqual(self.offer("one"), "one.player")

    def test_two_extensions_offering_one_idea_do_not_collide(self) -> None:
        self.offer("one", value="A")
        self.offer("two", value="B")

        answers = tokens.contributed_values(tokens.TABLE, {})

        self.assertEqual(answers["one.player"], "A")
        self.assertEqual(answers["two.player"], "B")

    def test_offering_again_replaces_so_a_reload_works(self) -> None:
        self.offer("one", value="FIRST")
        self.offer("one", value="AGAIN")

        self.assertEqual(tokens.contributed_values(tokens.TABLE, {})["one.player"],
                         "AGAIN")

    def test_a_name_with_an_id_already_in_it_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.offer("one", name="two.player")

    def test_a_context_commands_do_not_run_in_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tokens.register("one", "player", frozenset({"whenever"}),
                            lambda _base: "", says="Says")

    def test_a_name_nobody_describes_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            tokens.register("one", "player", frozenset({tokens.TABLE}),
                            lambda _base: "", says="  ")

    def test_a_name_its_own_catalog_describes_is_described(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "en.json").write_text(
                json.dumps({"token.player.says": "Who is playing"}), encoding="utf-8")
            i18n.own("ext.one", Path(folder))
            self.addCleanup(i18n.disown, "ext.one")
            full = tokens.register("one", "player", frozenset({tokens.TABLE}),
                                   lambda _base: "")

            (offered,) = [one for one in tokens.offered(tokens.TABLE) if one.name == full]
            self.assertEqual(tokens.stands_for(offered), "Who is playing")


class WhatHappensWhenItGoes(unittest.TestCase):
    def tearDown(self) -> None:
        tokens.forget("one")

    def test_a_command_still_naming_it_refuses_by_that_name(self) -> None:
        tokens.register("one", "player", frozenset({tokens.TABLE}),
                        lambda _base: "ABC", says="Says")
        tokens.forget("one")

        with self.assertRaises(tokens.UnknownTokenError) as raised:
            tokens.resolve("{one.player}", {}, context=tokens.TABLE)

        self.assertEqual(str(raised.exception), "one.player")

    def test_an_extension_that_raises_resolves_to_nothing(self) -> None:
        def broken(_base: dict) -> str:
            raise RuntimeError("no")

        tokens.register("one", "player", frozenset({tokens.TABLE}), broken, says="Says")

        with self.assertLogs("vpinfe.common.tokens", level="ERROR"):
            self.assertEqual(tokens.contributed_values(tokens.TABLE, {})["one.player"],
                             "")


class WhatWasTakenBeforeStands(unittest.TestCase):
    def tearDown(self) -> None:
        tokens.forget("one")

    def test_a_captured_value_is_not_asked_again(self) -> None:
        """A table's closing commands are told what its opening ones were."""
        tokens.register("one", "player", frozenset({tokens.TABLE}),
                        lambda _base: "NOW", says="Says")

        self.assertEqual(
            tokens.filled(tokens.TABLE, {"one.player": "THEN"})["one.player"], "THEN")


if __name__ == "__main__":
    unittest.main()
