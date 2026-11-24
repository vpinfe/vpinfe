"""What an extension asks for under Needs You: what it may declare, how its answer is
read, and what is left out when it cannot answer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from common import i18n
from common.extensions import contract, host, serving, store
from common.extensions.context import ExtensionUI, check_references
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY

MODULE = "tests.extensions.test_needs"
NAME = "asking"

STATE: dict[str, Any] = {}

KINDS = {"share_down": {"list": "plays"}, "update_ready": {"action": "update", "tone": "accent"},
         "review": {"section": "week", "tone": "danger"}}


def fresh_state() -> None:
    STATE.clear()
    STATE.update(answer=[], raises=None)


class Dialog:
    def ask(self) -> dict:
        return {}


def asking(ctx: Any) -> None:
    def read() -> Any:
        if STATE["raises"] is not None:
            raise STATE["raises"]
        return STATE["answer"]

    ctx.ui.list("plays", lambda: [], columns=[{"field": "name"}], title="Plays", area="community")
    ctx.ui.section("week", lambda: {}, title="Week")
    ctx.ui.action("update", Dialog(), label="Update")
    ctx.ui.needs(KINDS, read)


def _installed(root: Path, catalog: dict | None = None) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Asking", "version": "1.0.0",
        "description": "Asks for things on Overview, for the tests that need it.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": ["ui:mount"], "scopes": []}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(json.dumps({
        "need.share_down.label": "{count} shared plays are down",
        "need.share_down.says": "Asking could not send them",
        "need.update_ready.label": "An update is ready", **(catalog or {})}),
        encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import asking as register\n", encoding="utf-8")
    return directory


class Declaring(unittest.TestCase):
    def ui(self, *, allowed: bool = True) -> ExtensionUI:
        return ExtensionUI(NAME, allowed)

    def test_each_kind_names_what_it_opens_and_how_it_is_drawn(self) -> None:
        ui = self.ui()

        ui.needs(KINDS, lambda: [])

        self.assertEqual(
            [(one["kind"], one["opens"], one["to"], one["tone"]) for one in ui.declared_needs],
            [("share_down", "list", "plays", "warm"),
             ("update_ready", "action", "update", "accent"),
             ("review", "section", "week", "danger")])

    def test_needs_want_the_ui_capability(self) -> None:
        with self.assertRaisesRegex(contract.ContractError, "ui:mount"):
            self.ui(allowed=False).needs(KINDS, lambda: [])

    def test_what_cannot_be_drawn_is_refused_and_nothing_is_kept(self) -> None:
        ui = self.ui()
        for kinds, why in (({}, "dict of kinds"),
                           ({"a:b": {"list": "x"}}, "colon"),
                           ({"a": {}}, "no list or section or action"),
                           ({"a": {"list": "x", "section": "y"}}, "more than one"),
                           ({"a": {"list": ""}}, "no list or section or action|more than one"),
                           ({"a": {"list": "x", "tone": "loud"}}, "tone")):
            with self.subTest(kinds=kinds), self.assertRaisesRegex(contract.ContractError, why):
                ui.needs(kinds, lambda: [])
        with self.assertRaisesRegex(contract.ContractError, "not a function"):
            ui.needs({"a": {"list": "x"}}, "read")  # type: ignore[arg-type]
        self.assertEqual(ui.declared_needs, [])

    def test_declaring_twice_is_refused(self) -> None:
        ui = self.ui()
        ui.needs({"a": {"list": "x"}}, lambda: [])

        with self.assertRaisesRegex(contract.ContractError, "twice"):
            ui.needs({"b": {"list": "x"}}, lambda: [])

    def test_a_kind_that_opens_something_never_offered_is_refused(self) -> None:
        ui = self.ui()
        ui.needs({"a": {"action": "gone"}}, lambda: [])

        with self.assertRaisesRegex(contract.ContractError, "'a'.*action 'gone'"):
            check_references(ui)


class Reading(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = host.Registry(store.ExtensionStore(Path(tmp.name)))
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        fresh_state()
        self.record = self.registry.load(_installed(Path(tmp.name) / "installed"))
        self.assertEqual(self.record.state, host.LOADED, self.record.error)

    def raised(self) -> list[dict]:
        return serving.needs_read(self.record)

    def test_a_kind_that_holds_is_worded_from_the_catalog_with_its_count(self) -> None:
        STATE["answer"] = [{"kind": "share_down", "ids": ["a", "b", "c"]}]

        (one,) = self.raised()

        self.assertEqual(
            (one["key"], one["label"], one["says"], one["count"], one["covered"],
             one["opens"], one["to"], one["display_name"]),
            (f"ext:{NAME}:share_down", "3 shared plays are down", "Asking could not send them",
             3, ["a", "b", "c"], "list", "plays", "Asking"))

    def test_a_count_with_a_token_is_dismissed_against_the_token(self) -> None:
        STATE["answer"] = [{"kind": "update_ready", "count": 1, "token": "2.1"}]

        (one,) = self.raised()

        self.assertEqual((one["count"], one["covered"], one["tone"]), (1, ["2.1"], "accent"))

    def test_a_count_with_no_token_is_dismissed_against_its_kind(self) -> None:
        STATE["answer"] = [{"kind": "update_ready", "count": 2}]

        self.assertEqual(self.raised()[0]["covered"], ["update_ready"])

    def test_a_kind_with_nothing_to_say_is_not_raised(self) -> None:
        STATE["answer"] = [{"kind": "share_down", "ids": []},
                           {"kind": "update_ready", "count": 0}]

        self.assertEqual(self.raised(), [])

    def test_a_kind_with_no_words_is_labelled_with_the_extension(self) -> None:
        STATE["answer"] = [{"kind": "review", "count": 1}]

        (one,) = self.raised()

        self.assertEqual((one["label"], one["says"]), ("Asking", ""))

    def test_an_answer_core_cannot_use_is_the_extensions_failure(self) -> None:
        for answer in ({"kind": "share_down"}, [{"kind": "other", "count": 1}],
                       ["share_down"], [{"kind": "share_down", "count": "many"}]):
            with self.subTest(answer=answer):
                STATE["answer"] = answer

                with self.assertRaises(serving.ExtensionError):
                    self.raised()

    def test_an_answer_no_one_could_hash_or_count_is_the_extensions_failure(self) -> None:
        for answer in ([{"kind": ["share_down"]}], [{"kind": {"a": 1}}],
                       [{"kind": "share_down", "count": float("inf")}],
                       [{"kind": "share_down", "ids": ["a"]},
                        {"kind": "share_down", "ids": ["b"]}]):
            with self.subTest(answer=answer):
                STATE["answer"] = answer

                with self.assertRaises(serving.ExtensionError):
                    self.raised()

    def test_an_extension_that_never_loaded_is_away_so_its_dismissals_are_kept(self) -> None:
        self.record.state = host.DISABLED
        self.record.declared_needs = []

        self.assertEqual(serving.needs_all(self.registry.records()),
                         {"needs": [], "away": [NAME], "failed": []})

    def test_every_running_extension_is_asked_and_the_failing_one_is_away(self) -> None:
        STATE["answer"] = [{"kind": "share_down", "ids": ["a"]}]
        answered = serving.needs_all(self.registry.records())
        STATE["raises"] = RuntimeError("down")
        failing = serving.needs_all(self.registry.records())

        self.assertEqual([one["key"] for one in answered["needs"]], [f"ext:{NAME}:share_down"])
        self.assertEqual((answered["away"], answered["failed"]), ([], []))
        self.assertEqual(failing, {"needs": [], "away": [NAME], "failed": [NAME]})

    def test_a_stopped_extension_raises_none_and_is_away_but_not_failed(self) -> None:
        STATE["answer"] = [{"kind": "share_down", "ids": ["a"]}]
        self.record.state = host.DISABLED

        self.assertEqual(serving.needs_all(self.registry.records()),
                         {"needs": [], "away": [NAME], "failed": []})

    def test_the_record_lists_the_kinds_while_it_runs(self) -> None:
        self.assertEqual([one["kind"] for one in self.record.as_dict()["needs"]],
                         ["share_down", "update_ready", "review"])
        self.record.state = host.DISABLED
        self.assertEqual(self.record.as_dict()["needs"], [])


if __name__ == "__main__":
    unittest.main()
