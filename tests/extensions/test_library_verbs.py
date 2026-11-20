"""An entry an extension puts in the Games and Tables menus: what it may declare, what the
route asks it, what a Private game keeps from it, and how it is worded."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from common import i18n
from common.extensions import contract, host, store
from common.extensions.context import ExtensionUI
from common.i18n import t
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY
from tests.support.served import client_for

MODULE = "tests.extensions.test_library_verbs"
NAME = "verbed"

STATE: dict[str, Any] = {}

PUBLIC = {"private": False, "vps_id": "vps-1", "name": "Taxi"}
HIDDEN = {"private": True, "vps_id": "vps-2", "name": "Secret"}


def fresh_state() -> None:
    STATE.clear()
    STATE.update(asked=[], answer="Done")


def verbed(ctx: Any) -> None:
    def run(who: list[dict]) -> Any:
        STATE["asked"].append([dict(one) for one in who])
        return STATE["answer"]

    ctx.ui.verb("game", "ship", run, label="Ship it")
    ctx.ui.verb("game", "just_one", run, one=True)
    ctx.ui.verb("table", "scrub", run, danger=True)
    ctx.ui.verb("game", "tidy", run, private_ok=True)


def _installed(root: Path) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Verbed", "version": "1.0.0",
        "description": "Puts entries in the Games and Tables menus, for the tests that ask.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": ["ui:mount"], "scopes": ["games:read", "games:write"]}),
        encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(json.dumps({
        "verb.game.just_one.label": "Just One", "verb.game.just_one.tip": "Takes one game",
        "verb.table.scrub.label": "Scrub", "verb.table.scrub.confirm": "Scrub {count}?"}),
        encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import verbed as register\n", encoding="utf-8")
    return directory


class Declaring(unittest.TestCase):
    def ui(self, *, allowed: bool = True, scopes: tuple[str, ...] = ("games:read",)
           ) -> ExtensionUI:
        return ExtensionUI(NAME, allowed, scopes)

    def test_an_entry_is_declared_for_a_game_and_for_a_table(self) -> None:
        ui = self.ui()

        ui.verb("game", "ship", lambda who: None, label=" Ship ", one=True)
        ui.verb("table", "scrub", lambda who: None, danger=True)

        self.assertEqual(ui.declared_library_verbs, [
            {"subject": "game", "key": "ship", "label": "Ship", "one": True, "danger": False,
             "private_ok": False, "tier": "library"},
            {"subject": "table", "key": "scrub", "label": "", "one": False, "danger": True,
             "private_ok": False, "tier": "library"}])

    def test_an_entry_needs_the_ui_capability_and_the_scope_to_read_games(self) -> None:
        with self.assertRaisesRegex(contract.ContractError, "ui:mount"):
            self.ui(allowed=False).verb("game", "x", lambda who: None)
        with self.assertRaisesRegex(contract.ContractError, "games:read"):
            self.ui(scopes=()).verb("game", "x", lambda who: None)

    def test_a_subject_is_a_game_or_a_table_and_a_key_is_there_once(self) -> None:
        ui = self.ui()
        ui.verb("game", "x", lambda who: None)

        with self.assertRaisesRegex(contract.ContractError, "not one of"):
            ui.verb("player", "x", lambda who: None)
        with self.assertRaisesRegex(contract.ContractError, "two with the same one"):
            ui.verb("game", "x", lambda who: None)
        with self.assertRaisesRegex(contract.ContractError, "two with the same one"):
            ui.verb("table", " ", lambda who: None)
        ui.verb("table", "x", lambda who: None)

    def test_a_key_is_lower_case_words_joined_by_underscores(self) -> None:
        for key in ("a/b", "a.label", "Flag", "1up", "two words"):
            with self.subTest(key=key), self.assertRaisesRegex(contract.ContractError,
                                                               "lower-case words"):
                self.ui().verb("game", key, lambda who: None)

    def test_a_run_is_a_function(self) -> None:
        ui = self.ui()

        with self.assertRaisesRegex(contract.ContractError, "not a function"):
            ui.verb("game", "x", "run")  # type: ignore[arg-type]
        self.assertEqual(ui.declared_library_verbs, [])


class DeclaringWhereAnActionIsOffered(unittest.TestCase):
    def ui(self, scopes: tuple[str, ...] = ("games:read",)) -> ExtensionUI:
        return ExtensionUI(NAME, True, scopes)

    class Dialog:
        def ask(self, step: str) -> dict:
            return {}

    def test_an_action_records_the_pages_and_subjects_it_is_offered_in(self) -> None:
        ui = self.ui()

        ui.action("share", self.Dialog(), add=("games", "tables"), menu="game", private_ok=True)

        record = ui.actions[0]
        self.assertEqual((record["add"], record["menu"], record["private_ok"]),
                         (["games", "tables"], ["game"], True))

    def test_a_place_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(contract.ContractError):
            self.ui().action("share", self.Dialog(), add="nowhere")
        with self.assertRaises(contract.ContractError):
            self.ui().action("share", self.Dialog(), menu="row")

    def test_a_menu_entry_needs_to_read_games_and_private_ok_needs_a_menu(self) -> None:
        with self.assertRaises(contract.ContractError):
            self.ui(()).action("share", self.Dialog(), menu="game")
        with self.assertRaises(contract.ContractError):
            self.ui().action("share", self.Dialog(), add="games", private_ok=True)


class Served(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = store.ExtensionStore(Path(tmp.name))
        self.registry = host.Registry(self.store)
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        fresh_state()
        self.record = self.registry.load(_installed(Path(tmp.name) / "installed"))
        self.assertEqual(self.record.state, host.LOADED, self.record.error)
        self.client = client_for(self, self.registry, self.record)
        self.resources = {"g1": PUBLIC, "g2": dict(PUBLIC, name="Medusa"), "hidden": HIDDEN}
        for target, replacement in (
                ("httpapi.extensions_served.game_lens.resource_of",
                 lambda game_id: dict(self.resources[game_id])),
                ("httpapi.extensions_served.game_lens.game_or_refuse", lambda _id: {}),
                ("httpapi.extensions_served.table_ops.row_or_refuse",
                 lambda _game, table_id: {"id": table_id, "version": "1.2",
                                          "source": {"vps_file_id": "file-9"}})):
            patch = mock.patch(target, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    def press(self, kind: str, verb: str, *targets: dict[str, str]) -> Any:
        return self.client.post(f"/ext/{NAME}/verbs/{kind}/{verb}", json={"targets": list(targets)})


class Pressing(Served):
    def test_an_entry_is_called_with_the_games_chosen_and_answers_what_to_say(self) -> None:
        response = self.press("game", "ship", {"game_id": "g1"}, {"game_id": "g2"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"message": "Done"})
        self.assertEqual(STATE["asked"], [[
            {"game_id": "g1", "vps_id": "vps-1", "name": "Taxi"},
            {"game_id": "g2", "vps_id": "vps-1", "name": "Medusa"}]])

    def test_a_table_entry_is_told_the_table_too(self) -> None:
        self.press("table", "scrub", {"game_id": "g1", "table_id": "t1"})

        self.assertEqual(STATE["asked"], [[
            {"game_id": "g1", "vps_id": "vps-1", "name": "Taxi", "table_id": "t1",
             "vps_file_id": "file-9", "version": "1.2"}]])

    def test_a_private_game_is_left_out_and_a_verb_with_none_left_refuses(self) -> None:
        self.press("game", "ship", {"game_id": "g1"}, {"game_id": "hidden"})
        refused = self.press("game", "ship", {"game_id": "hidden"})

        self.assertEqual([[one["game_id"] for one in who] for who in STATE["asked"]], [["g1"]])
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(len(STATE["asked"]), 1)

    def test_a_table_entry_is_not_given_a_game_alone_and_a_game_chosen_twice_is_one(
            self) -> None:
        none = self.press("table", "scrub", {"game_id": "g1"})
        self.press("game", "ship", {"game_id": "g1"}, {"game_id": "g1"})

        self.assertEqual(none.status_code, 400, none.text)
        self.assertEqual(
            none.json()["error"]["message"],
            t("error.extensions.verb_none", display=self.record.display_name, key="scrub"))
        self.assertEqual([[one["game_id"] for one in who] for who in STATE["asked"]], [["g1"]])

    def test_a_table_entry_on_a_private_game_is_left_out(self) -> None:
        refused = self.press("table", "scrub", {"game_id": "hidden", "table_id": "t1"})

        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(STATE["asked"], [])

    def test_a_private_ok_entry_is_given_a_private_game_marked_as_one(self) -> None:
        self.press("game", "tidy", {"game_id": "g1"}, {"game_id": "hidden"})

        self.assertEqual(STATE["asked"], [[
            {"game_id": "g1", "vps_id": "vps-1", "name": "Taxi", "private": False},
            {"game_id": "hidden", "vps_id": "vps-2", "name": "Secret", "private": True}]])

    def test_an_entry_for_one_refuses_several(self) -> None:
        refused = self.press("game", "just_one", {"game_id": "g1"}, {"game_id": "g2"})

        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(
            refused.json()["error"]["message"],
            t("error.extensions.verb_rows", display=self.record.display_name,
              key="just_one"))
        self.assertEqual(STATE["asked"], [])
        self.assertEqual(self.press("game", "just_one", {"game_id": "g1"}).status_code, 200)

    def test_an_entry_the_extension_did_not_declare_is_not_there(self) -> None:
        for kind, verb in (("game", "scrub"), ("table", "ship"), ("game", "nothing"),
                           ("player", "ship")):
            with self.subTest(kind=kind, verb=verb):
                self.assertEqual(self.press(kind, verb, {"game_id": "g1"}).status_code, 404)
        self.assertEqual(STATE["asked"], [])

    def test_pressing_wants_the_write_scope(self) -> None:
        self.client = client_for(self, self.registry, self.record,
                                 allowed=lambda scope: scope.endswith(":read"))

        self.assertEqual(self.press("game", "ship", {"game_id": "g1"}).status_code, 403)
        self.assertEqual(STATE["asked"], [])

    def test_an_entry_that_raises_is_the_extensions_failure(self) -> None:
        def broken(who: list[dict]) -> None:
            raise RuntimeError("no")

        assert self.record.context is not None
        self.record.context.ui.library_verb_functions[("game", "ship")] = broken

        self.assertEqual(self.press("game", "ship", {"game_id": "g1"}).status_code, 400)


class Worded(Served):
    def test_each_entry_takes_its_words_from_the_catalog_and_the_key_when_it_has_none(
            self) -> None:
        said = {(one["subject"], one["key"]): one
                for one in self.record.as_dict()["library_verbs"]}

        self.assertEqual(said[("game", "ship")]["label"], "Ship it")
        self.assertEqual(said[("game", "just_one")]["label"], "Just One")
        self.assertEqual(said[("game", "just_one")]["tip"], "Takes one game")
        self.assertEqual(said[("table", "scrub")]["label"], "Scrub")
        self.assertTrue(
            said[("table", "scrub")]["confirm_key"].endswith("verb.table.scrub.confirm"))
        self.assertEqual(said[("game", "ship")]["confirm_key"], "")

    def test_a_bundled_extension_says_so_and_an_installed_one_does_not(self) -> None:
        self.assertFalse(self.record.as_dict()["bundled"])

    def test_nothing_is_offered_while_the_extension_is_not_running(self) -> None:
        self.record.state = host.FAILED

        self.assertEqual(self.record.as_dict()["library_verbs"], [])
