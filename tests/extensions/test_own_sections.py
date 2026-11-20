"""Protects: (b) what an extension declares for its own panel and what its routes serve."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from common import i18n
from common.extensions import contract, host, pictures, store
from common.extensions.context import ExtensionUI, check_references
from common.i18n import t
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY
from tests.support.served import client_for

MODULE = "tests.extensions.test_own_sections"
NAME = "sectioned"

STATE: dict[str, Any] = {}

ANSWER: dict[str, Any] = {
    "facts": [{"label": "Plays", "value": 4, "kind": "number"}],
    "fields": [{"key": "note", "type": "string", "label": "Note", "value": "hi"},
               {"key": "token", "type": "secret", "label": "Token", "value": "hunter2"}],
    "rows": [{"label": "Bally", "under": "1990"}]}


def fresh_state() -> None:
    STATE.clear()
    STATE.update(saved=[], answer=ANSWER, raises=None)


def sectioned(ctx: Any) -> None:
    def read() -> Any:
        return STATE["answer"]

    def save(changes: dict) -> None:
        if STATE["raises"] is not None:
            raise STATE["raises"]
        STATE["saved"].append(dict(changes))

    ctx.ui.section("week", read, title="This week", save=save)
    ctx.ui.panel(area="community")


def _installed(root: Path, *, capabilities: list[str]) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Sectioned", "version": "1.0.0",
        "description": "Gives its panel a section, for the tests that ask for it.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": capabilities, "scopes": []}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text("{}", encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import sectioned as register\n", encoding="utf-8")
    return directory


class _Dialog:
    def ask(self, step: str) -> dict:
        return {}


class Declaring(unittest.TestCase):
    def ui(self, *, allowed: bool = True) -> ExtensionUI:
        return ExtensionUI(NAME, allowed)

    def test_a_section_is_declared_with_where_it_is_served(self) -> None:
        ui = self.ui()

        ui.section("week", lambda: {}, title="Week", save=lambda values: None)

        self.assertEqual(ui.declared_sections, [{
            "key": "week", "title": "Week", "base": "/sections/week", "save": True,
            "icon": "", "tier": "library"}])

    def test_a_section_and_a_panel_need_the_ui_capability(self) -> None:
        with self.assertRaisesRegex(contract.ContractError, "ui:mount"):
            self.ui(allowed=False).section("week", lambda: {})
        with self.assertRaisesRegex(contract.ContractError, "ui:mount"):
            self.ui(allowed=False).panel(area="community")

    def test_a_section_has_a_key_that_is_its_own_and_functions_for_a_read_and_save(self) -> None:
        ui = self.ui()
        ui.section("week", lambda: {})

        with self.assertRaisesRegex(contract.ContractError, "twice"):
            ui.section("week", lambda: {})
        with self.assertRaisesRegex(contract.ContractError, "lower-case"):
            ui.section("Two Words", lambda: {})
        with self.assertRaisesRegex(contract.ContractError, "not a function"):
            ui.section("a", "read")  # type: ignore[arg-type]
        with self.assertRaisesRegex(contract.ContractError, "not a function"):
            ui.section("b", lambda: {}, save="save")  # type: ignore[arg-type]
        self.assertEqual([one["key"] for one in ui.declared_sections], ["week"])

    def test_a_panel_has_an_area_core_has_and_is_declared_once(self) -> None:
        ui = self.ui()

        for area in ("", "  ", None):
            with self.subTest(area=area), self.assertRaisesRegex(
                    contract.ContractError, "no area"):
                ui.panel(area=area)  # type: ignore[arg-type]
        with self.assertRaisesRegex(contract.ContractError, "not one of"):
            ui.panel(area="attic")
        ui.panel(area="community")
        with self.assertRaisesRegex(contract.ContractError, "twice"):
            ui.panel(area="community")

    def test_an_action_in_a_section_the_extension_never_declares_is_refused(self) -> None:
        ui = self.ui()
        ui.action("add", _Dialog(), section="missing")

        with self.assertRaisesRegex(contract.ContractError, "'add'.*'missing'"):
            check_references(ui)


class Served(unittest.TestCase):
    capabilities = ["ui:mount"]

    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = store.ExtensionStore(Path(tmp.name))
        self.registry = host.Registry(self.store)
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        fresh_state()
        self.record = self.registry.load(
            _installed(Path(tmp.name) / "installed", capabilities=self.capabilities))
        self.assertEqual(self.record.state, host.LOADED, self.record.error)
        self.client = client_for(self, self.registry, self.record)
        self.base = f"/ext/{NAME}/sections/week"

    def ok(self, response: Any) -> Any:
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def put(self, **values: Any) -> Any:
        return self.client.put(self.base, json={"values": values})


class Reading(Served):
    def test_a_section_answers_what_the_extension_read_and_holds_a_secret_back(self) -> None:
        answer = self.ok(self.client.get(self.base))

        shown = answer["section"]
        self.assertEqual([one["label"] for one in shown["facts"]], ["Plays"])
        self.assertEqual(shown["rows"], [{"label": "Bally", "under": "1990"}])
        self.assertNotIn("hunter2", json.dumps(answer))

    def test_a_section_the_extension_did_not_declare_is_not_there(self) -> None:
        response = self.client.get(f"/ext/{NAME}/sections/other")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.extensions.no_section", display=self.record.display_name,
                           key="other"))


class Saving(Served):
    def test_changed_fields_are_written_and_the_section_comes_back(self) -> None:
        answer = self.ok(self.put(note="there"))

        self.assertEqual(STATE["saved"], [{"note": "there"}])
        self.assertEqual(answer["key"], "week")

    def test_a_key_the_section_does_not_offer_is_refused_and_nothing_is_saved(self) -> None:
        response = self.put(other="x")

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(STATE["saved"], [])

    def test_a_save_that_raises_is_a_refusal_in_its_words_with_the_field_named(self) -> None:
        STATE["raises"] = ValueError("That note is too long")

        error = self.put(note="x").json()["error"]

        self.assertEqual((error["message"], error["details"]),
                         ("That note is too long", {"field": "note"}))

    def test_a_section_with_no_save_cannot_be_written(self) -> None:
        assert self.record.context is not None
        self.record.context.ui.section_functions["week"].save = None

        response = self.put(note="x")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.extensions.no_section_save",
                           display=self.record.display_name, key="week"))

    def test_a_section_at_library_is_closed_to_a_caller_holding_only_play(self) -> None:
        self.client = client_for(self, self.registry, self.record,
                                 allowed=lambda scope: scope.endswith(":play"))

        self.assertEqual(self.client.get(self.base).status_code, 403)
        self.assertEqual(self.put(note="x").status_code, 403)
        self.assertEqual(STATE["saved"], [])


class Declared(Served):
    def test_the_record_lists_its_sections_and_panel_with_the_titles_resolved(self) -> None:
        shown = self.record.as_dict()

        self.assertEqual([(one["key"], one["title"]) for one in shown["sections"]],
                         [("week", "This week")])
        self.assertEqual([(one["area"], one["title"]) for one in shown["panels"]],
                         [("community", "Sectioned")])

    def test_a_switched_off_extension_lists_none(self) -> None:
        self.record.state = host.DISABLED

        shown = self.record.as_dict()

        self.assertEqual((shown["sections"], shown["panels"]), ([], []))


class PicturedWithoutOutbound(Served):
    def test_a_web_picture_is_refused_and_says_which_section(self) -> None:
        STATE["answer"] = {**ANSWER, "facts": [
            {"label": "Cover", "value": "https://pictures.example/week.png",
             "kind": "picture"}]}

        response = self.client.get(self.base)

        self.assertEqual(response.status_code, 400)
        self.assertIn(t("error.extensions.picture_outbound", display="Sectioned",
                        key="section week"), response.text)


class Pictured(Served):
    capabilities = ["ui:mount", "net:outbound"]

    def setUp(self) -> None:
        super().setUp()
        pictures._sources.clear()

    def test_a_picture_fact_is_an_address_of_cores_own(self) -> None:
        STATE["answer"] = {**ANSWER, "facts": [
            {"label": "Cover", "value": "https://pictures.example/week.png",
             "kind": "picture"}]}

        fact = self.ok(self.client.get(self.base))["section"]["facts"][0]

        self.assertRegex(fact["value"], rf"^/api/v1/ext/{NAME}/pictures/[0-9a-f]{{20}}$")


class Titled(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)

    def test_a_panel_may_not_be_named_for_a_core_page(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        registry = host.Registry(store.ExtensionStore(Path(tmp.name)))
        self.addCleanup(registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        fresh_state()
        directory = _installed(Path(tmp.name) / "installed", capabilities=["ui:mount"])
        (directory / "i18n" / "en.json").write_text(
            json.dumps({"panel.title": t("manager.section.games")}), encoding="utf-8")

        record = registry.load(directory)

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("a panel", record.error)


if __name__ == "__main__":
    unittest.main()
