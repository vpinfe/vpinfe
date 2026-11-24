"""What core answers when the Manager asks for something an extension declared: its settings,
its lists, its dialogs and the jobs they start. Every route is core's, driven here over HTTP
through an extension built in a temp folder."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

import httpapi
from common import i18n
from common.extensions import contract, host, store
from common.extensions.contract import Unavailable
from common.i18n import t
from common.runtime import jobs
from httpapi import errors
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY
from tests.support.served import client_for

MODULE = "tests.extensions.test_serving"
NAME = "served"
TOKEN = "tok-" + "Zq9" * 10

WORDS = {
    "settings.greeting.label": "Greeting",
    "settings.greeting.help": "Said at the top of the page",
    "settings.token.label": "Token",
    "settings.mode.choice.fast": "Quick",
    "list.things.title": "Things",
    "list.things.act.refresh.label": "Refresh now",
    "list.verbed.verb.mark.label": "Mark",
    "list.verbed.verb.mark.tip": "Marks the rows",
    "list.verbed.verb.open.label": "Open page",
    "list.verbed.verb.remove.label": "Remove",
    "list.verbed.verb.remove.confirm": {"one": "Remove {count} row?",
                                        "other": "Remove {count} rows?"},
    "list.verbed.verb.remove.needs": "Only a row you can remove",
    "action.greet.label": "Say hello",
    "action.rowed.label": "Add a note",
    "action.gamed.label": "Get a file",
    "action.bare.label": "Add bare",
    "list.opening.title": "Opening",
    "list.opening.verb.each.label": "Each",
    "list.opening.verb.all.label": "All",
    "list.opening.act.open.label": "Open the dialog",
}

# What the functions below answer with. A test sets what it wants before it asks.
STATE: dict[str, Any] = {}


def fresh_state() -> None:
    STATE.clear()
    STATE.update(
        checked=[], steps=[], plain_values=None, seen=None, rows=[], status="", acts=None,
        pressed=[], asked_rows=[], ran_rows=[], asked_who=[], checked_who=[], acted_who=[],
        ran_who=[], pane=None, saved=[], pane_asked=[],
        save_raises=None,
        progress=None, running=threading.Event(), release=threading.Event())


def served(ctx: Any) -> None:
    """One extension with a settings page, three lists, and a dialog of each shape."""

    def check(values: dict) -> None:
        if values["greeting"] == "bad":
            raise ValueError("That greeting is not allowed")
        STATE["checked"].append(dict(values))

    ctx.ui.settings([
        {"key": "greeting", "type": "string", "default": "hello"},
        {"key": "token", "type": "secret"},
        {"key": "loud", "type": "switch"},
        {"key": "mode", "type": "select", "choices": [["fast", "Fast"], ["slow", "Slow"]]},
        {"key": "tags", "type": "multi", "default": ["a"],
         "choices": [["a", "A"], ["b", "B"], ["c", "C"]]},
        {"key": "volume", "type": "number", "min": 0, "max": 10, "unit": "dB", "default": 5},
        {"key": "folder", "type": "path", "wants": "file"},
    ], check=check)

    def refresh() -> str:
        return "Refreshed"

    def down() -> None:
        raise Unavailable("The service did not answer")

    def refuse() -> None:
        raise ValueError("Nothing to refresh")

    ctx.ui.list(
        "things", lambda: [{"name": "a"}, {"name": "b"}], columns=[{"field": "name"}],
        status=lambda: "2 things",
        acts={"refresh": refresh, "docs": "https://example.invalid/docs", "down": down,
              "refuse": refuse, "quiet": lambda: None, "data": lambda: {"added": 2}},
              area="community")
    ctx.ui.list("shaped", lambda: STATE["rows"], columns=[{"field": "name"}], title="Shaped",
                status=lambda: STATE["status"], acts=lambda: STATE["acts"], area="community")

    def failing() -> list:
        raise STATE["raises"]

    ctx.ui.list("failing", failing, columns=[{"field": "name"}], title="Failing", area="community")

    def mark(rows: list) -> str:
        STATE["pressed"].append(("mark", [dict(one) for one in rows]))
        return f"Marked {len(rows)}"

    def remove(rows: list) -> dict:
        STATE["pressed"].append(("remove", [dict(one) for one in rows]))
        return {"message": "Removed", "url": "https://example.invalid/after"}

    def tidy(rows: list) -> None:
        STATE["pressed"].append(("tidy", [dict(one) for one in rows]))

    def break_it(rows: list) -> None:
        raise ValueError("Could not break it")

    ctx.ui.list(
        "verbed",
        lambda: [{"id": 1, "name": "a", "can": True, "page": "https://example.invalid/a"},
                 {"id": "b", "name": "b", "can": False},
                 {"name": "no id"}],
        columns=[{"field": "name"}], title="Verbed",
        verbs=[{"key": "mark", "run": mark},
               {"key": "open", "link": "page"},
               {"key": "remove", "run": remove, "danger": True, "needs": "can", "one": True},
               {"key": "tidy", "run": tidy, "needs": "can"},
               {"key": "break", "run": break_it, "label": "Break it"}], area="community")

    def detail(row_id: str) -> Any:
        STATE["pane_asked"].append(row_id)
        answer = STATE["pane"]
        return answer(row_id) if callable(answer) else answer

    def save(row_id: str, values: dict) -> None:
        if STATE["save_raises"] is not None:
            raise STATE["save_raises"]
        STATE["saved"].append((row_id, dict(values)))

    ctx.ui.list("paned", lambda: [{"id": "a", "name": "a"}], columns=[{"field": "name"}],
                title="Paned", detail=detail, save=save, area="community")
    ctx.ui.list("read_only", lambda: [{"id": "a", "name": "a"}], columns=[{"field": "name"}],
                title="Read only", detail=detail, area="community")

    class Rowed:
        def ask(self, step: str, rows: list[str]) -> dict:
            STATE["asked_rows"].append(list(rows))
            return {"fields": [{"key": "note", "type": "string"}]}

        def run(self, values: dict) -> None:
            STATE["ran_rows"].append(list(values.get("rows") or []))

    class Gamed:
        def ask(self, step: str, rows: list[str], who: dict) -> dict:
            STATE["asked_who"].append((list(rows), dict(who)))
            if step == "field":
                return {"fields": [{"key": "who", "type": "string"}]}
            return {"fields": [{"key": "note", "type": "string"}]}

        def check(self, values: dict, who: dict) -> dict | None:
            STATE["checked_who"].append((dict(values), dict(who)))
            if values.get("again"):
                return None
            return {"fields": [{"key": "note", "type": "string"}]}

        def act(self, act: str, values: dict, who: dict) -> dict | None:
            STATE["acted_who"].append(dict(who))
            if values.get("again"):
                return None
            return {"fields": [{"key": "note", "type": "string"}]}

        def run(self, values: dict, who: dict) -> None:
            STATE["ran_who"].append(dict(who))

    class Bare:
        def ask(self, step: str) -> dict:
            return {"fields": []}

    ctx.ui.list(
        "opening", lambda: [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        columns=[{"field": "name"}], title="Opening", add=["rowed", "bare"],
        acts={"open": {"action": "rowed"}},
        verbs=[{"key": "each", "action": "rowed", "each": True},
               {"key": "all", "action": "rowed"}], area="community")

    class Greet:
        def ask(self, step: str) -> dict:
            if step == "two":
                return {"fields": [{"key": "second", "type": "string"}]}
            return {"fields": [{"key": "name", "type": "string"}]}

        def check(self, values: dict, step: str = "") -> dict | None:
            STATE["steps"].append(step)
            if not values.get("name"):
                return {"fields": [{"key": "name", "type": "string"}],
                        "errors": {"name": "A name is needed"}}
            return None

        def act(self, act: str, values: dict, step: str = "") -> dict | None:
            if act == "clear":
                return {"fields": [], "title": "Cleared"}
            if act == "down":
                raise Unavailable("The service did not answer")
            if act == "refuse":
                raise ValueError("Cannot do that")
            return None

        def run(self, values: dict, progress: Any, step: str = "") -> Any:
            progress.stage("Working", total=3)
            progress.advance()
            STATE["seen"] = progress.snapshot()
            STATE["run_step"] = step
            if values.get("fail"):
                raise ValueError("The run failed")
            if values.get("refuse"):
                return {"started": False, "message": "Nothing to do"}
            if values.get("text"):
                return "Done"
            return None

    class Plain:
        def ask(self, step: str) -> dict:
            return {"fields": [{"key": "a", "type": "string"}], "title": "Plain"}

        def check(self, values: dict) -> None:
            STATE["plain_values"] = values

    class Worker:
        def ask(self, step: str) -> dict:
            return {"fields": []}

        def run(self, values: dict) -> dict:
            def work(job: jobs.Job) -> str:
                STATE["running"].set()
                while not job.halt.wait(0.01):
                    if STATE["release"].is_set():
                        return "finished"
                job.stopped = True
                return "stopped"

            job = ctx.jobs.submit(values.get("kind", "work"), work,
                                  stoppable=values.get("stoppable", True))
            return {"job_id": job.id, "started": True}

    class Tracked:
        def ask(self, step: str) -> dict:
            return {"fields": []}

        def progress(self) -> dict | None:
            return STATE["progress"]

    class Wrong:
        def ask(self, step: str) -> Any:
            if step == "good":
                return {"fields": []}
            if step == "unknown":
                return {"fields": [{"key": "k", "type": "rating"}]}
            if step == "list":
                return ["not", "a", "step"]
            if step == "down":
                raise Unavailable("The service did not answer")
            if step == "misspelled":
                return {"fields": [], "error": "Not that one"}
            if step == "object":
                return {"fields": [], "notes": object()}
            if step == "rows":
                return {"fields": [{"key": "rows", "type": "string"}]}
            if step == "linked":
                return {"notes": ["Plain", {"text": "The notice", "detail": "Read it",
                                            "to": "https://example.com/notice"}]}
            if step == "ftp":
                return {"notes": [{"text": "The notice", "to": "ftp://example.com/notice"}]}
            if step == "unlabeled":
                return {"notes": [{"to": "https://example.com/notice"}]}
            if step == "unparsed":
                return {"notes": [{"text": "The notice", "to": "http://[x"}]}
            if step == "countable":
                return {"notes": 5}
            if step == "numbered":
                return {"notes": [{"text": 5, "to": "https://example.com/notice"}]}
            if step == "linked_error":
                return {"errors": {"a": {"text": "No", "to": "https://example.com/notice"}}}
            raise ValueError("The service said no")

    ctx.ui.action("greet", Greet())
    ctx.ui.action("rowed", Rowed())
    ctx.ui.action("gamed", Gamed())
    ctx.ui.action("bare", Bare())
    ctx.ui.action("plain", Plain())
    ctx.ui.action("worker", Worker())
    ctx.ui.action("tracked", Tracked())
    ctx.ui.action("wrong", Wrong(), label="Wrong")


def _installed(root: Path) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Served", "version": "1.0.0",
        "description": "Declares what core serves, for the tests that ask for it.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": ["config:own", "ui:mount"]}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(json.dumps(WORDS), encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import served as register\n", encoding="utf-8")
    return directory


class ServedCase(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        fresh_state()
        jobs.reset_for_tests()
        self.addCleanup(self._end_jobs)
        self.record = self.registry.load(_installed(self.root / "installed"))
        self.assertEqual(self.record.state, host.LOADED, self.record.error)
        self.client = client_for(self, self.registry, self.record)
        self.base = f"/ext/{NAME}"

    def _end_jobs(self) -> None:
        STATE["release"].set()
        self.settle(lambda: not jobs.active())
        jobs.reset_for_tests()

    def settle(self, done: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 5
        while not done():
            if time.monotonic() > deadline:
                self.fail("Timed out waiting")
            time.sleep(0.01)

    def ok(self, response: Any, status: int = 200) -> Any:
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def refused(self, response: Any, status: int, message: str = "") -> dict:
        self.assertEqual(response.status_code, status, response.text)
        error = response.json()["error"]
        if message:
            self.assertEqual(error["message"], message)
        return error

    def get(self, path: str, **params: str) -> Any:
        return self.client.get(self.base + path, params=params)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self.client.post(self.base + path, json=body if body is not None else {})

    def put_settings(self, **values: Any) -> Any:
        return self.client.put(f"{self.base}/settings", json={"values": values})

    def setting(self, answer: dict, key: str) -> dict:
        return next(one for one in answer["fields"] if one["key"] == key)

    def display(self) -> str:
        return self.record.display_name


class Settings(ServedCase):
    def test_the_page_lists_every_declared_setting_in_order(self) -> None:
        found = self.ok(self.get("/settings"))["fields"]

        self.assertEqual([(one["key"], one["type"]) for one in found],
                         [("greeting", "string"), ("token", "secret"), ("loud", "switch"),
                          ("mode", "select"), ("tags", "multi"), ("volume", "number"),
                          ("folder", "path")])

    def test_a_label_comes_from_the_extensions_catalog_else_the_key(self) -> None:
        found = self.ok(self.get("/settings"))

        self.assertEqual(self.setting(found, "greeting")["label"], "Greeting")
        self.assertEqual(self.setting(found, "loud")["label"], "loud")

    def test_a_string_shows_what_is_stored_and_its_default_as_the_placeholder(self) -> None:
        found = self.setting(self.ok(self.get("/settings")), "greeting")

        self.assertEqual((found["value"], found["placeholder"], found["help"]),
                         ("", "hello", "Said at the top of the page"))

    def test_a_switch_reads_false_until_it_is_turned_on(self) -> None:
        self.assertIs(self.setting(self.ok(self.get("/settings")), "loud")["value"], False)

        self.ok(self.put_settings(loud=True))

        self.assertIs(self.setting(self.ok(self.get("/settings")), "loud")["value"], True)

    def test_a_select_offers_its_choices_with_the_catalogs_words_where_it_has_them(self):
        found = self.setting(self.ok(self.get("/settings")), "mode")

        self.assertEqual(found["value"], "fast")
        self.assertEqual(found["choices"], [["fast", "Quick"], ["slow", "Slow"]])

    def test_a_secret_is_never_echoed_and_says_whether_it_is_set(self) -> None:
        self.assertIs(self.setting(self.ok(self.get("/settings")), "token")["set"], False)

        written = self.ok(self.put_settings(token=TOKEN))
        read = self.ok(self.get("/settings"))

        for answer in (written, read):
            token = self.setting(answer, "token")
            self.assertEqual((token["type"], token["set"]), ("secret", True))
            self.assertNotIn("value", token)
            self.assertNotIn(TOKEN, json.dumps(answer))
        self.assertEqual(self.store.settings(NAME)["token"], TOKEN)

    def test_an_empty_secret_leaves_the_one_held_alone(self) -> None:
        self.put_settings(token=TOKEN)

        self.ok(self.put_settings(token="", greeting="hi"))

        self.assertEqual(self.store.settings(NAME)["token"], TOKEN)

    def test_a_write_answers_the_page_as_it_now_stands(self) -> None:
        found = self.ok(self.put_settings(greeting="good evening", mode="slow"))

        self.assertEqual(self.setting(found, "greeting")["value"], "good evening")
        self.assertEqual(self.setting(found, "mode")["value"], "slow")
        self.assertEqual(self.store.settings(NAME)["greeting"], "good evening")

    def test_a_switch_is_stored_as_text(self) -> None:
        for given, kept in ((True, "true"), ("true", "true"), (False, "false"),
                            ("nonsense", "false")):
            with self.subTest(given=given):
                self.ok(self.put_settings(loud=given))
                self.assertEqual(self.store.settings(NAME)["loud"], kept)

    def test_a_multi_reads_its_default_then_what_was_ticked(self) -> None:
        self.assertEqual(self.setting(self.ok(self.get("/settings")), "tags")["value"], ["a"])

        written = self.ok(self.put_settings(tags=["c", "b", "c"]))

        self.assertEqual(self.setting(written, "tags")["value"], ["c", "b"])
        self.assertEqual(self.store.settings(NAME)["tags"], '["c", "b"]')

    def test_a_multi_with_a_key_that_is_not_a_choice_is_refused(self) -> None:
        self.refused(self.put_settings(tags=["a", "z"]), 400,
                     t("error.extensions.bad_choice", key="tags", value="z"))

        self.assertNotIn("tags", self.store.settings(NAME))

    def test_a_multi_that_is_not_a_list_is_refused_and_keeps_what_was_ticked(self) -> None:
        self.ok(self.put_settings(tags=["a"]))

        for given in ("a", 5, '{"a": 1}'):
            with self.subTest(given=given):
                self.refused(self.put_settings(tags=given), 400,
                             t("error.extensions.bad_choice", key="tags", value=given))
        self.assertEqual(self.store.settings(NAME)["tags"], '["a"]')

    def test_a_number_reads_with_its_limits_and_is_stored_as_digits(self) -> None:
        found = self.setting(self.ok(self.get("/settings")), "volume")
        self.assertEqual((found["value"], found["min"], found["max"], found["unit"]),
                         ("5", 0.0, 10.0, "dB"))

        self.ok(self.put_settings(volume=7.0))

        self.assertEqual(self.store.settings(NAME)["volume"], "7")

    def test_a_number_outside_its_limits_or_not_a_number_is_refused(self) -> None:
        for given in (11, "loud", "inf"):
            with self.subTest(given=given):
                self.refused(self.put_settings(volume=given), 400,
                             t("error.extensions.bad_number", key="volume", value=given))
        self.assertNotIn("volume", self.store.settings(NAME))

    def test_a_path_reads_what_it_wants_and_keeps_the_text(self) -> None:
        self.ok(self.put_settings(folder="/somewhere/file.txt"))

        found = self.setting(self.ok(self.get("/settings")), "folder")

        self.assertEqual((found["value"], found["wants"]), ("/somewhere/file.txt", "file"))

    def test_none_clears_a_secret_and_empty_keeps_it(self) -> None:
        self.put_settings(token=TOKEN)
        self.put_settings(token="")
        self.put_settings(token="   ")
        self.assertEqual(self.store.settings(NAME)["token"], TOKEN)

        found = self.ok(self.put_settings(token=None))

        self.assertIs(self.setting(found, "token")["set"], False)
        self.assertEqual(self.store.settings(NAME)["token"], "")

    def test_none_clears_a_setting(self) -> None:
        self.put_settings(greeting="hi")

        self.ok(self.put_settings(greeting=None))

        self.assertEqual(self.store.settings(NAME)["greeting"], "")

    def test_a_key_nobody_declared_is_refused_and_nothing_is_written(self) -> None:
        self.refused(self.put_settings(greeting="hi", nothing="x"), 400,
                     t("error.extensions.no_setting", key="nothing"))

        self.assertNotIn("greeting", self.store.settings(NAME))

    def test_a_choice_outside_a_select_is_refused(self) -> None:
        self.refused(self.put_settings(mode="medium"), 400,
                     t("error.extensions.bad_choice", key="mode", value="medium"))

        self.assertNotIn("mode", self.store.settings(NAME))

    def test_the_check_is_given_every_setting_as_it_would_be_after_the_change(self) -> None:
        self.put_settings(mode="slow")
        STATE["checked"].clear()

        self.ok(self.put_settings(greeting="hi"))

        self.assertEqual(STATE["checked"],
                         [{"greeting": "hi", "token": "", "loud": "false", "mode": "slow",
                           "tags": '["a"]', "volume": "5", "folder": ""}])

    def test_a_check_that_raises_refuses_with_its_line_and_the_field_named(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.put_settings(greeting="bad")

        error = self.refused(found, 400)
        self.assertIn("That greeting is not allowed", error["message"])
        self.assertEqual(error["details"], {"field": "greeting"})
        self.assertNotIn("greeting", self.store.settings(NAME))

    def test_the_field_named_is_the_first_one_changed(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.client.put(f"{self.base}/settings",
                                    json={"values": {"mode": "slow", "greeting": "bad"}})

        self.assertEqual(self.refused(found, 400)["details"], {"field": "mode"})

    def test_a_refused_check_does_not_disable_the_extension(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            self.put_settings(greeting="bad")

        loaded = self.registry.get(NAME)
        assert loaded is not None
        self.assertEqual(loaded.state, host.LOADED)

    def test_a_write_that_changes_nothing_is_not_checked(self) -> None:
        self.ok(self.put_settings(token=""))

        self.assertEqual(STATE["checked"], [])


class Lists(ServedCase):
    def test_a_list_answers_its_rows(self) -> None:
        self.assertEqual(self.ok(self.get("/lists/things")),
                         {"rows": [{"name": "a"}, {"name": "b"}]})

    def test_an_unknown_list_is_a_404_in_the_catalogs_words(self) -> None:
        self.refused(self.get("/lists/nothing"), 404,
                     t("error.extensions.no_list", display=self.display(), key="nothing"))

    def test_rows_that_are_not_a_list_of_dicts_are_refused(self) -> None:
        for rows in ({"name": "a"}, ["a", "b"], "rows", None):
            with self.subTest(rows=rows):
                STATE["rows"] = rows
                self.refused(self.get("/lists/shaped"), 400,
                             t("error.extensions.not_rows", display=self.display(),
                               key="shaped"))

    def test_rows_nested_past_the_recursion_limit_are_the_extensions_failure_too(self) -> None:
        deep: list = []
        deep.append(deep)
        STATE["rows"] = [{"name": deep}]

        self.refused(self.get("/lists/shaped"), 400,
                     t("error.extensions.not_data", display=self.display(),
                       at="list shaped"))

    def test_rows_that_json_cannot_carry_are_the_extensions_failure_not_a_fault(self) -> None:
        STATE["rows"] = [{"name": object()}]

        self.refused(self.get("/lists/shaped"), 400,
                     t("error.extensions.not_data", display=self.display(),
                       at="list shaped"))

        loaded = self.registry.get(NAME)
        assert loaded is not None
        self.assertEqual(loaded.state, host.LOADED)

    def test_rows_that_raise_are_a_refusal_with_the_line_they_said(self) -> None:
        STATE["raises"] = ValueError("The service said no")

        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.get("/lists/failing")

        self.assertIn("The service said no", self.refused(found, 400)["message"])
        loaded = self.registry.get(NAME)
        assert loaded is not None
        self.assertEqual(loaded.state, host.LOADED)

    def test_two_rows_with_one_id_are_refused_for_any_list(self) -> None:
        STATE["rows"] = [{"id": 1, "name": "a"}, {"id": "1", "name": "b"}]

        found = self.refused(self.get("/lists/shaped"), 400)

        self.assertEqual(found["message"], t("error.extensions.duplicate_row_id",
                                             display=self.display(), key="shaped", id="1"))

    def test_rows_that_cannot_reach_their_service_are_a_502(self) -> None:
        STATE["raises"] = Unavailable("The service did not answer")

        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.get("/lists/failing")

        error = self.refused(found, 502)
        self.assertEqual(error["code"], errors.CODE_UPSTREAM_UNAVAILABLE)
        self.assertIn("The service did not answer", error["message"])

    def test_a_failure_raised_from_an_unavailable_one_is_still_the_services(self) -> None:
        try:
            try:
                raise Unavailable("The service did not answer")
            except Unavailable as exc:
                raise ValueError("Could not read the ratings") from exc
        except ValueError as raised:
            STATE["raises"] = raised

        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.get("/lists/failing")

        self.assertEqual(found.status_code, 502, found.text)

    def test_the_about_page_carries_the_status_and_what_the_list_offers(self) -> None:
        found = self.ok(self.get("/lists/things/about"))

        self.assertEqual(found["status"], "2 things")
        self.assertEqual([one["key"] for one in found["acts"]],
                         ["refresh", "docs", "down", "refuse", "quiet", "data"])

    def test_an_act_is_labelled_from_the_catalog_else_by_its_key(self) -> None:
        acts = {one["key"]: one for one in self.ok(self.get("/lists/things/about"))["acts"]}

        self.assertEqual(acts["refresh"]["label"], "Refresh now")
        self.assertEqual(acts["quiet"]["label"], "quiet")

    def test_an_act_that_is_an_address_is_offered_as_one_and_not_as_something_to_run(self):
        acts = {one["key"]: one for one in self.ok(self.get("/lists/things/about"))["acts"]}

        self.assertEqual(acts["docs"]["url"], "https://example.invalid/docs")
        self.assertNotIn("url", acts["refresh"])

    def test_a_list_with_no_status_says_nothing(self) -> None:
        STATE["acts"] = {}

        self.assertEqual(self.ok(self.get("/lists/shaped/about")), {"status": "", "acts": []})

    def test_a_list_declared_with_no_status_function_says_nothing(self) -> None:
        STATE["raises"] = ValueError("not read")

        self.assertEqual(self.ok(self.get("/lists/failing/about")), {"status": "", "acts": []})

    def test_acts_given_as_a_function_are_asked_each_time(self) -> None:
        STATE["acts"] = {"one": lambda: "1"}
        first = self.ok(self.get("/lists/shaped/about"))["acts"]
        STATE["acts"] = {"two": lambda: "2"}
        second = self.ok(self.get("/lists/shaped/about"))["acts"]

        self.assertEqual(([one["key"] for one in first], [one["key"] for one in second]),
                         (["one"], ["two"]))

    def test_an_act_answers_the_message_it_returned(self) -> None:
        self.assertEqual(self.ok(self.post("/lists/things/about/acts/refresh")),
                         {"message": "Refreshed"})

    def test_an_act_that_returns_nothing_answers_nothing(self) -> None:
        self.assertEqual(self.ok(self.post("/lists/things/about/acts/quiet")), {})

    def test_an_act_that_returns_data_answers_it(self) -> None:
        self.assertEqual(self.ok(self.post("/lists/things/about/acts/data")), {"added": 2})

    def test_an_unknown_act_is_a_404(self) -> None:
        self.refused(self.post("/lists/things/about/acts/nothing"), 404,
                     t("error.extensions.no_act", display=self.display(), key="nothing"))

    def test_an_address_is_opened_by_the_client_and_is_not_an_act_to_run(self) -> None:
        self.refused(self.post("/lists/things/about/acts/docs"), 404,
                     t("error.extensions.no_act", display=self.display(), key="docs"))

    def test_an_act_on_a_list_nobody_declared_is_a_404(self) -> None:
        self.refused(self.post("/lists/nothing/about/acts/refresh"), 404,
                     t("error.extensions.no_list", display=self.display(), key="nothing"))

    def test_an_act_that_cannot_reach_its_service_is_a_502(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.post("/lists/things/about/acts/down")

        error = self.refused(found, 502)
        self.assertEqual(error["code"], errors.CODE_UPSTREAM_UNAVAILABLE)
        self.assertIn("The service did not answer", error["message"])

    def test_an_act_that_raises_anything_else_is_a_400(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.post("/lists/things/about/acts/refuse")

        self.assertIn("Nothing to refresh", self.refused(found, 400)["message"])
        loaded = self.registry.get(NAME)
        assert loaded is not None
        self.assertEqual(loaded.state, host.LOADED)


SOLO = {"title": "Taxi", "under": "Pinball 2000", "sections": [{
    "key": "main", "label": "",
    "facts": [{"label": "Plays", "value": 4, "kind": "number"},
              {"label": "Status", "value": "Waiting", "kind": "state", "tier": "warn"},
              {"label": "Page", "value": "Open it", "kind": "link",
               "to": "https://example.invalid/taxi"},
              {"label": "Tags", "value": ["a", "b"], "kind": "chips"}],
    "fields": [{"key": "nickname", "type": "string", "label": "Nickname", "value": "Cab"},
               {"key": "loud", "type": "switch", "label": "Loud", "value": True},
               {"key": "mode", "type": "select", "label": "Mode", "value": "fast",
                "choices": [["fast", "Fast"], ["slow", "Slow"]]},
               {"key": "volume", "type": "number", "label": "Volume", "value": 3,
                "min": 0, "max": 10},
               {"key": "token", "type": "secret", "label": "Token", "value": "hunter2"}],
    "rows": [{"label": "Bally", "under": "1990"}]}]}


class Panes(ServedCase):
    def pane(self, row: str = "a") -> Any:
        return self.get(f"/lists/paned/detail/{row}")

    def put(self, row: str = "a", **values: Any) -> Any:
        return self.client.put(f"{self.base}/lists/paned/detail/{row}", json={"values": values})

    def test_a_list_declares_whether_it_opens_a_pane(self) -> None:
        by_key = {one["key"]: one for one in self.record.declared_lists}

        self.assertEqual((by_key["paned"]["detail"], by_key["things"]["detail"]),
                         ("/lists/paned/detail", ""))

    def test_a_pane_is_asked_for_the_row_and_answered_in_the_shape_core_draws(self) -> None:
        STATE["pane"] = SOLO

        answer = self.ok(self.pane())

        self.assertEqual(STATE["pane_asked"], ["a"])
        self.assertEqual((answer["id"], answer["title"], answer["under"]),
                         ("a", "Taxi", "Pinball 2000"))
        section = answer["sections"][0]
        self.assertEqual([one["kind"] for one in section["facts"]],
                         ["number", "state", "link", "chips"])
        self.assertEqual((section["facts"][1]["tier"], section["facts"][3]["value"]),
                         ("warn", ["a", "b"]))
        self.assertEqual(section["rows"], [{"label": "Bally", "under": "1990"}])

    def test_a_secret_goes_out_as_set_and_never_with_its_value(self) -> None:
        STATE["pane"] = SOLO

        token = next(one for one in self.ok(self.pane())["sections"][0]["fields"]
                     if one["key"] == "token")

        self.assertEqual(token["set"], True)
        self.assertNotIn("value", token)
        self.assertNotIn("hunter2", self.pane().text)

    def test_an_id_with_a_slash_in_it_reaches_the_function_whole(self) -> None:
        STATE["pane"] = SOLO

        self.ok(self.pane("one/two"))

        self.assertEqual(STATE["pane_asked"], ["one/two"])

    def test_a_row_the_extension_answers_nothing_for_is_a_404(self) -> None:
        STATE["pane"] = None

        self.refused(self.pane("gone"), 404,
                     t("error.extensions.no_row", display=self.display(), id="gone"))

    def test_a_list_with_no_detail_has_no_pane(self) -> None:
        self.refused(self.get("/lists/things/detail/a"), 404,
                     t("error.extensions.no_detail", display=self.display(), key="things"))

    def test_an_answer_core_does_not_draw_is_the_extensions_failure(self) -> None:
        wrong = {
            "a key it does not read": {**SOLO, "colour": "red"},
            "no title": {"sections": []},
            "a fact kind it has no drawing for": {"title": "T", "sections": [
                {"key": "k", "facts": [{"label": "L", "value": "v", "kind": "rating"}]}]},
            "a field type a pane does not draw": {"title": "T", "sections": [
                {"key": "k", "fields": [{"key": "f", "type": "choice"}]}]},
            "a link with nowhere to go": {"title": "T", "sections": [
                {"key": "k", "facts": [{"label": "L", "value": "v", "kind": "link"}]}]},
            "a tier on something that is not a state": {"title": "T", "sections": [
                {"key": "k", "facts": [{"label": "L", "value": "v", "tier": "on"}]}]},
            "two fields of one key": {"title": "T", "sections": [
                {"key": "k", "fields": [{"key": "f"}, {"key": "f"}]}]},
            "several sections and one with no label": {"title": "T", "sections": [
                {"key": "a", "label": "A"}, {"key": "b"}]},
            "a select with no choices": {"title": "T", "sections": [
                {"key": "k", "fields": [{"key": "f", "type": "select"}]}]},
        }
        for what, answer in wrong.items():
            with self.subTest(what):
                STATE["pane"] = answer
                self.refused(self.pane(), 400)

    def test_fields_are_written_with_the_values_that_changed_and_the_pane_comes_back(self) -> None:
        STATE["pane"] = SOLO

        answer = self.ok(self.put(nickname="Taxi", loud=False, mode="slow", volume="4"))

        self.assertEqual(STATE["saved"],
                         [("a", {"nickname": "Taxi", "loud": False, "mode": "slow", "volume": 4})])
        self.assertEqual(answer["title"], "Taxi")

    def test_a_secret_is_saved_when_typed_and_cleared_with_none_and_left_alone_when_empty(
            self) -> None:
        STATE["pane"] = SOLO

        self.ok(self.put(token="new"))
        self.ok(self.put(token=None))
        self.ok(self.put(token=" "))

        self.assertEqual(STATE["saved"], [("a", {"token": "new"}), ("a", {"token": None})])

    def test_a_key_the_pane_does_not_offer_is_refused_and_nothing_is_saved(self) -> None:
        STATE["pane"] = SOLO

        self.refused(self.put(nickname="x", other="y"), 400,
                     t("error.extensions.no_field", key="other"))

        self.assertEqual(STATE["saved"], [])

    def test_a_choice_the_field_does_not_offer_and_a_number_outside_its_limits_are_refused(
            self) -> None:
        STATE["pane"] = SOLO

        self.refused(self.put(mode="medium"), 400,
                     t("error.extensions.bad_choice", key="mode", value="medium"))
        self.refused(self.put(volume="11"), 400,
                     t("error.extensions.bad_number", key="volume", value="11"))

        self.assertEqual(STATE["saved"], [])

    def test_a_save_that_raises_is_a_refusal_in_its_words_with_the_field_named(self) -> None:
        STATE["pane"] = SOLO
        STATE["save_raises"] = ValueError("That nickname is taken")

        error = self.refused(self.put(nickname="Taxi"), 400, "That nickname is taken")

        self.assertEqual(error["details"], {"field": "nickname"})

    def test_a_list_with_a_pane_and_no_save_cannot_be_written(self) -> None:
        STATE["pane"] = SOLO

        self.refused(self.client.put(f"{self.base}/lists/read_only/detail/a",
                                     json={"values": {"nickname": "x"}}), 404,
                     t("error.extensions.no_save", display=self.display(), key="read_only"))

    def test_a_pane_at_library_is_closed_to_a_caller_holding_only_play(self) -> None:
        STATE["pane"] = SOLO
        self.client = client_for(self, self.registry, self.record,
                                 allowed=lambda scope: scope.endswith(":play"))

        self.assertEqual(self.pane().status_code, 403)
        self.assertEqual(self.put(nickname="x").status_code, 403)
        self.assertEqual(STATE["saved"], [])


class Verbs(ServedCase):
    def press(self, verb: str, *ids: str) -> Any:
        return self.post(f"/lists/verbed/verbs/{verb}", {"ids": list(ids)})

    def declared(self) -> dict:
        (found,) = [one for one in self.record.lists() if one["key"] == "verbed"]
        return {one["key"]: one for one in found["verbs"]}

    def test_a_verb_is_declared_with_the_words_the_catalog_has_for_it(self) -> None:
        found = self.declared()

        self.assertEqual((found["mark"]["label"], found["mark"]["tip"]),
                         ("Mark", "Marks the rows"))
        self.assertEqual(found["break"]["label"], "Break it")
        self.assertEqual(found["remove"]["needs_reason"], "Only a row you can remove")
        self.assertEqual(i18n.t(found["remove"]["confirm_key"], count=2), "Remove 2 rows?")
        self.assertNotIn("run", found["mark"])

    def test_a_link_verb_is_for_one_row_and_carries_the_field_it_reads(self) -> None:
        found = self.declared()["open"]

        self.assertEqual((found["link"], found["one"]), ("page", True))
        self.refused(self.press("open", "1"), 404)

    def test_rows_without_an_id_are_answered_without_one_and_the_others_as_text(self) -> None:
        rows = self.ok(self.get("/lists/verbed"))["rows"]

        self.assertEqual([one.get("id") for one in rows], ["1", "b", None])

    def test_a_verb_is_run_on_the_rows_asked_for_and_answers_its_message(self) -> None:
        self.assertEqual(self.ok(self.press("mark", "1", "b")), {"message": "Marked 2"})
        self.assertEqual([one["name"] for one in STATE["pressed"][0][1]], ["a", "b"])

    def test_a_verb_that_answers_an_address_hands_it_on(self) -> None:
        self.assertEqual(self.ok(self.press("remove", "1")),
                         {"message": "Removed", "url": "https://example.invalid/after"})

    def test_a_row_the_needs_field_is_false_on_is_left_out_of_the_call(self) -> None:
        self.ok(self.press("tidy", "1", "b"))

        self.assertEqual([one["id"] for one in STATE["pressed"][0][1]], ["1"])

    def test_a_verb_with_no_row_it_is_for_refuses_in_the_extensions_words(self) -> None:
        self.refused(self.press("remove", "b"), 400, "Only a row you can remove")

        self.assertEqual(STATE["pressed"], [])

    def test_a_one_at_a_time_verb_refuses_several_rows(self) -> None:
        self.refused(self.press("remove", "1", "b"), 400,
                     t("error.extensions.verb_rows", display=self.display(), key="remove"))

    def test_no_rows_is_refused(self) -> None:
        self.refused(self.press("mark"), 400,
                     t("error.extensions.verb_rows", display=self.display(), key="mark"))

    def test_a_row_the_list_no_longer_has_is_a_404(self) -> None:
        self.refused(self.press("mark", "gone"), 404,
                     t("error.extensions.no_row", display=self.display(), id="gone"))

    def test_an_unknown_verb_is_a_404(self) -> None:
        self.refused(self.press("nothing", "1"), 404,
                     t("error.extensions.no_verb", display=self.display(), key="nothing"))

    def test_a_verb_that_raises_is_the_extensions_failure(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.press("break", "1")

        self.assertIn("Could not break it", self.refused(found, 400)["message"])
        loaded = self.registry.get(NAME)
        assert loaded is not None
        self.assertEqual(loaded.state, host.LOADED)

    def test_a_verb_at_library_is_closed_to_a_caller_holding_only_play(self) -> None:
        self.client = client_for(self, self.registry, self.record,
                                 allowed=lambda scope: scope.endswith(":play"))

        self.assertEqual(self.get("/lists/verbed").status_code, 403)
        found = self.press("mark", "1")

        self.assertEqual(found.status_code, 403)
        self.assertIn(f"ext:{NAME}:library", found.json()["error"]["message"])
        self.assertEqual(STATE["pressed"], [])


class DialogsOpenedForAGame(ServedCase):
    WHO = {"game_id": "g1", "vps_id": "abcdefghij", "name": "Taxi", "table_id": ""}

    def asked(self, who: dict) -> Any:
        return self.post("/actions/gamed/ask", {"rows": ["pick-1"], "who": who})

    def test_the_game_a_dialog_was_opened_for_is_handed_to_its_ask_with_the_rows(self) -> None:
        self.ok(self.asked(self.WHO))

        self.assertEqual([(["pick-1"], self.WHO)], STATE["asked_who"])

    def test_a_game_the_library_does_not_hold_has_no_game_id_and_still_has_the_rest(self) -> None:
        self.ok(self.asked({"vps_id": "abcdefghij", "name": "Taxi"}))

        self.assertEqual([(["pick-1"], {"game_id": "", "vps_id": "abcdefghij", "name": "Taxi",
                                        "table_id": ""})],
                         STATE["asked_who"])

    def test_the_table_a_file_is_saved_for_is_passed_on(self) -> None:
        self.ok(self.asked({**self.WHO, "table_id": "t1"}))

        self.assertEqual([(["pick-1"], {**self.WHO, "table_id": "t1"})], STATE["asked_who"])

    def test_a_table_alone_does_not_make_a_game(self) -> None:
        self.ok(self.asked({"table_id": "t1"}))

        self.assertEqual([(["pick-1"], {})], STATE["asked_who"])

    def test_only_the_ids_an_extension_can_use_are_passed_on(self) -> None:
        self.ok(self.asked({**self.WHO, "path": "/Users/someone"}))

        self.assertEqual([(["pick-1"], self.WHO)], STATE["asked_who"])

    def test_a_dialog_opened_for_no_game_is_asked_with_an_empty_who(self) -> None:
        self.ok(self.post("/actions/gamed/ask", {}))
        self.ok(self.get("/actions/gamed"))

        self.assertEqual([([], {}), ([], {})], STATE["asked_who"])

    def test_a_dialog_that_does_not_name_who_is_not_handed_one(self) -> None:
        self.ok(self.post("/actions/rowed/ask", {"rows": ["1"], "who": self.WHO}))

        self.assertEqual([["1"]], STATE["asked_rows"])

    def test_a_later_call_is_handed_the_game_beside_its_values(self) -> None:
        self.ok(self.post("/actions/gamed/check", {"values": {"note": "a"}, "who": self.WHO}))
        self.ok(self.post("/actions/gamed/act",
                          {"act": "more", "values": {"note": "a"}, "who": self.WHO}))
        self.ok(self.post("/actions/gamed/run", {"values": {"note": "a"}, "who": self.WHO}))

        self.assertEqual([({"note": "a"}, self.WHO)], STATE["checked_who"])
        self.assertEqual([self.WHO], STATE["acted_who"])
        self.assertEqual([self.WHO], STATE["ran_who"])

    def test_a_check_and_an_act_that_ask_again_do_so_for_the_game_and_rows_they_were_sent(
            self) -> None:
        self.ok(self.post("/actions/gamed/check", {"values": {"again": "1", "rows": ["pick-1"]},
                                                   "who": self.WHO}))
        self.ok(self.post("/actions/gamed/act", {"act": "more", "who": self.WHO,
                                                 "values": {"again": "1", "rows": ["pick-2"]}}))

        self.assertEqual([(["pick-1"], self.WHO), (["pick-2"], self.WHO)], STATE["asked_who"])

    def test_the_game_is_not_put_into_the_values(self) -> None:
        self.ok(self.post("/actions/gamed/check", {"values": {"note": "a"}, "who": self.WHO}))

        self.assertNotIn("who", STATE["checked_who"][0][0])

    def test_a_check_that_is_not_there_asks_again_for_the_game_and_rows_it_was_sent(self) -> None:
        self.ok(self.post("/actions/rowed/check", {"values": {"rows": ["1"]}, "who": self.WHO}))

        self.assertEqual([["1"]], STATE["asked_rows"])

    def test_a_call_that_names_no_game_is_handed_an_empty_one(self) -> None:
        self.ok(self.post("/actions/gamed/run", {"values": {"note": "a"}}))

        self.assertEqual([{}], STATE["ran_who"])

    def test_a_field_named_who_is_an_ordinary_field(self) -> None:
        asked = self.ok(self.get("/actions/gamed", step="field"))

        self.assertEqual(["who"], [one["key"] for one in asked["fields"]])


class DialogsOpenedFromAList(ServedCase):
    def asked(self, key: str, rows: list[str]) -> Any:
        return self.post(f"/actions/{key}/ask", {"rows": rows})

    def test_the_rows_a_dialog_was_opened_over_are_handed_to_its_ask(self) -> None:
        self.ok(self.asked("rowed", ["1", "2"]))

        self.assertEqual([["1", "2"]], STATE["asked_rows"])

    def test_the_rows_travel_in_the_body_so_a_long_selection_is_not_a_long_address(self) -> None:
        many = [f"game-{one}" for one in range(1500)]

        self.ok(self.asked("rowed", many))

        self.assertEqual([many], STATE["asked_rows"])

    def test_a_dialog_opened_over_no_rows_is_asked_with_none(self) -> None:
        self.ok(self.get("/actions/rowed"))

        self.assertEqual([[]], STATE["asked_rows"])

    def test_a_dialog_that_does_not_name_rows_is_not_handed_any(self) -> None:
        self.ok(self.asked("bare", ["1"]))

        self.assertEqual([], STATE["asked_rows"])

    def test_a_check_that_is_not_there_asks_again_with_the_rows_of_the_values(self) -> None:
        self.ok(self.post("/actions/rowed/check", {"values": {"rows": ["1", "2"]}}))

        self.assertEqual([["1", "2"]], STATE["asked_rows"])

    def test_a_field_named_rows_is_refused_as_the_extensions_failure(self) -> None:
        error = self.refused(self.get("/actions/wrong", step="rows"), 400)

        self.assertIn("'rows'", error["message"])

    def test_the_rows_travel_in_the_values_of_every_later_call(self) -> None:
        self.ok(self.post("/actions/rowed/run", {"values": {"rows": ["1"]}}))

        self.assertEqual([["1"]], STATE["ran_rows"])

    def test_a_list_declares_the_dialogs_it_adds_with_their_words(self) -> None:
        (found,) = [one for one in self.record.lists() if one["key"] == "opening"]

        self.assertEqual([{"action": "rowed", "label": "Add a note"},
                          {"action": "bare", "label": "Add bare"}], found["add"])

    def test_a_verb_that_opens_a_dialog_says_which_and_whether_it_walks_the_rows(self) -> None:
        (found,) = [one for one in self.record.lists() if one["key"] == "opening"]

        self.assertEqual([("each", "rowed", True), ("all", "rowed", False)],
                         [(one["key"], one["action"], one["each"]) for one in found["verbs"]])
        self.refused(self.post("/lists/opening/verbs/each", {"ids": ["1"]}), 404)

    def test_an_act_that_opens_a_dialog_is_listed_with_it_and_is_not_pressed(self) -> None:
        found = self.ok(self.get("/lists/opening/about"))

        self.assertEqual([{"key": "open", "label": "Open the dialog", "action": "rowed"}],
                         found["acts"])
        self.refused(self.post("/lists/opening/about/acts/open"), 404)


class Dialogs(ServedCase):
    def test_a_dialog_is_asked_for_its_first_step(self) -> None:
        found = self.ok(self.get("/actions/greet"))

        self.assertEqual(found["fields"], [{"key": "name", "type": "string"}])

    def test_a_step_is_asked_for_by_name(self) -> None:
        found = self.ok(self.get("/actions/greet", step="two"))

        self.assertEqual(found["fields"], [{"key": "second", "type": "string"}])

    def test_a_step_with_no_title_gets_the_actions_label_from_the_catalog(self) -> None:
        self.assertEqual(self.ok(self.get("/actions/greet"))["title"], "Say hello")

    def test_a_step_with_no_catalog_entry_gets_the_declared_label_else_the_key(self) -> None:
        self.assertEqual(self.ok(self.get("/actions/wrong", step="good"))["title"], "Wrong")
        self.assertEqual(self.ok(self.get("/actions/tracked"))["title"], "tracked")

    def test_a_title_the_step_gave_is_kept(self) -> None:
        self.assertEqual(self.ok(self.get("/actions/plain"))["title"], "Plain")

    def test_an_unknown_dialog_is_a_404(self) -> None:
        message = t("error.extensions.no_action", display=self.display(), key="nothing")

        self.refused(self.get("/actions/nothing"), 404, message)
        self.refused(self.post("/actions/nothing/check"), 404, message)
        self.refused(self.post("/actions/nothing/act", {"act": "go"}), 404, message)
        self.refused(self.post("/actions/nothing/run"), 404, message)
        self.refused(self.get("/actions/nothing/progress"), 404, message)

    def test_a_check_is_given_the_values_and_the_step_it_names(self) -> None:
        self.ok(self.post("/actions/greet/check", {"values": {"name": "x"}, "step": "two"}))

        self.assertEqual(STATE["steps"], ["two"])

    def test_a_check_that_answers_replaces_the_step(self) -> None:
        found = self.ok(self.post("/actions/greet/check", {"values": {}, "step": "two"}))

        self.assertEqual(found["errors"], {"name": "A name is needed"})
        self.assertEqual(found["title"], "Say hello")

    def test_a_check_that_answers_nothing_leaves_the_step_to_ask(self) -> None:
        found = self.ok(self.post("/actions/greet/check",
                                  {"values": {"name": "x"}, "step": "two"}))

        self.assertEqual(found["fields"], [{"key": "second", "type": "string"}])

    def test_a_check_that_does_not_name_step_is_not_handed_one(self) -> None:
        self.ok(self.post("/actions/plain/check", {"values": {"a": "1"}, "step": "two"}))

        self.assertEqual(STATE["plain_values"], {"a": "1"})

    def test_a_step_with_a_key_the_dialog_does_not_draw_is_refused_by_name(self) -> None:
        found = self.get("/actions/wrong", step="misspelled")

        message = self.refused(found, 400)["message"]
        self.assertIn("'error'", message)
        self.assertIn("errors", message)

    def test_a_dialog_with_no_check_ends_at_its_first_step(self) -> None:
        found = self.ok(self.get("/actions/tracked"))

        self.assertEqual(found["confirm"], found["title"])

    def test_a_dialog_with_a_check_leaves_its_step_open_to_go_on(self) -> None:
        self.assertNotIn("confirm", self.ok(self.get("/actions/greet")))

    def test_a_dialog_with_no_check_asks_again(self) -> None:
        found = self.ok(self.post("/actions/tracked/check", {"values": {"a": "1"}}))

        self.assertEqual(found["fields"], [])

    def test_an_act_answers_the_step_it_returned(self) -> None:
        found = self.ok(self.post("/actions/greet/act", {"act": "clear", "values": {}}))

        self.assertEqual((found["fields"], found["title"]), ([], "Cleared"))

    def test_an_act_that_answers_nothing_asks_again(self) -> None:
        found = self.ok(self.post("/actions/greet/act",
                                  {"act": "other", "values": {}, "step": "two"}))

        self.assertEqual(found["fields"], [{"key": "second", "type": "string"}])

    def test_an_act_that_cannot_reach_its_service_is_a_502(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.post("/actions/greet/act", {"act": "down"})

        self.assertEqual(self.refused(found, 502)["code"], errors.CODE_UPSTREAM_UNAVAILABLE)

    def test_an_act_that_raises_anything_else_is_a_400(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.post("/actions/greet/act", {"act": "refuse"})

        self.assertIn("Cannot do that", self.refused(found, 400)["message"])

    def test_a_dialog_with_no_act_is_a_404(self) -> None:
        self.refused(self.post("/actions/plain/act", {"act": "go"}), 404,
                     t("error.extensions.no_act", display=self.display(), key="go"))

    def test_an_act_needs_its_name(self) -> None:
        self.assertEqual(self.post("/actions/greet/act", {"values": {}}).status_code, 422)

    def test_a_dialog_with_no_run_is_a_404(self) -> None:
        self.refused(self.post("/actions/plain/run"), 404,
                     t("error.extensions.no_run", display=self.display(), key="plain"))

    def test_a_field_the_wizard_does_not_draw_is_the_extensions_failure(self) -> None:
        found = self.get("/actions/wrong", step="unknown")

        error = self.refused(found, 400)
        self.assertIn("'k' as 'rating'", error["message"])
        self.assertIn("Drawn:", error["message"])
        loaded = self.registry.get(NAME)
        assert loaded is not None
        self.assertEqual(loaded.state, host.LOADED)

    def test_a_note_may_link_to_a_web_address(self) -> None:
        notes = self.ok(self.get("/actions/wrong", step="linked"))["notes"]

        self.assertEqual(["Plain", {"text": "The notice", "detail": "Read it",
                                    "to": "https://example.com/notice"}], notes)

    def test_a_note_that_links_to_anything_but_the_web_is_refused_by_name(self) -> None:
        message = self.refused(self.get("/actions/wrong", step="ftp"), 400)["message"]

        self.assertIn("ftp://example.com/notice", message)
        self.assertIn("'to'", message)

    def test_a_note_that_links_with_no_words_to_show_it_as_is_refused(self) -> None:
        self.refused(self.get("/actions/wrong", step="unlabeled"), 400)

    def test_a_note_address_that_does_not_parse_is_the_extensions_failure_not_a_crash(self) -> None:
        message = self.refused(self.get("/actions/wrong", step="unparsed"), 400)["message"]

        self.assertIn("http://[x", message)

    def test_notes_that_are_not_a_list_are_refused(self) -> None:
        self.refused(self.get("/actions/wrong", step="countable"), 400)

    def test_a_linked_note_whose_text_is_not_words_is_refused(self) -> None:
        self.refused(self.get("/actions/wrong", step="numbered"), 400)

    def test_an_error_that_names_a_to_is_refused_by_field(self) -> None:
        message = self.refused(self.get("/actions/wrong", step="linked_error"), 400)["message"]

        self.assertIn("'a'", message)

    def test_an_answer_that_is_not_a_step_is_refused(self) -> None:
        self.refused(self.get("/actions/wrong", step="list"), 400,
                     t("error.extensions.bad_answer", display=self.display(), key="wrong"))

    def test_a_step_json_cannot_carry_is_refused(self) -> None:
        self.refused(self.get("/actions/wrong", step="object"), 400,
                     t("error.extensions.not_data", display=self.display(),
                       at="action wrong"))

    def test_a_step_that_cannot_reach_its_service_is_a_502(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.get("/actions/wrong", step="down")

        self.assertEqual(self.refused(found, 502)["code"], errors.CODE_UPSTREAM_UNAVAILABLE)

    def test_a_step_that_raises_is_a_400_with_its_line(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.get("/actions/wrong", step="other")

        self.assertIn("The service said no", self.refused(found, 400)["message"])


class Running(ServedCase):
    def test_a_run_that_answers_text_answers_it_as_a_message(self) -> None:
        found = self.ok(self.post("/actions/greet/run", {"values": {"text": True}}))

        self.assertEqual(found, {"message": "Done"})

    def test_a_run_that_answers_nothing_answers_nothing(self) -> None:
        self.assertEqual(self.ok(self.post("/actions/greet/run")), {})

    def test_a_run_that_started_no_job_is_a_200_and_not_a_202(self) -> None:
        found = self.post("/actions/greet/run", {"values": {"refuse": True}})

        self.assertEqual(self.ok(found), {"started": False, "message": "Nothing to do"})

    def test_a_run_that_started_a_job_is_a_202_with_its_id(self) -> None:
        found = self.ok(self.post("/actions/worker/run"), 202)

        self.assertTrue(found["started"])
        self.assertIsNotNone(jobs.get(found["job_id"]))

    def test_a_run_is_given_the_step_and_a_tracker_it_names(self) -> None:
        self.ok(self.post("/actions/greet/run", {"values": {}, "step": "two"}))

        self.assertEqual(STATE["run_step"], "two")
        self.assertEqual(STATE["seen"], {"stage": "Working", "message": "", "done": 1,
                                         "total": 3})

    def test_the_tracker_is_cleared_when_the_run_is_over(self) -> None:
        self.ok(self.post("/actions/greet/run"))

        self.assertEqual(self.ok(self.get("/actions/greet/progress")),
                         {"stage": "", "message": "", "done": 0, "total": 0})

    def test_the_tracker_is_cleared_when_the_run_raises(self) -> None:
        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.post("/actions/greet/run", {"values": {"fail": True}})

        self.assertIn("The run failed", self.refused(found, 400)["message"])
        self.assertEqual(self.ok(self.get("/actions/greet/progress"))["stage"], "")

    def test_a_dialog_that_keeps_its_own_progress_answers_with_it(self) -> None:
        STATE["progress"] = {"done": 2, "total": 5}

        self.assertEqual(self.ok(self.get("/actions/tracked/progress")),
                         {"done": 2, "total": 5})

    def test_its_own_progress_that_answers_nothing_is_an_empty_one(self) -> None:
        STATE["progress"] = None

        self.assertEqual(self.ok(self.get("/actions/tracked/progress")), {})

    def test_a_second_run_while_the_job_is_going_is_refused(self) -> None:
        self.ok(self.post("/actions/worker/run"), 202)

        with self.assertLogs(f"vpinfe.ext.{NAME}", "WARNING"):
            found = self.post("/actions/worker/run")

        self.assertEqual(found.status_code, 400, found.text)
        self.assertIn(t("error.jobs.busy"), found.json()["error"]["message"])


class Stopping(ServedCase):
    def start(self, **values: Any) -> str:
        job_id = self.ok(self.post("/actions/worker/run", {"values": values}), 202)["job_id"]
        self.settle(STATE["running"].is_set)
        return job_id

    def job_of(self, job_id: str) -> jobs.Job:
        found = jobs.get(job_id)
        assert found is not None
        return found

    def test_a_job_started_to_be_stopped_says_where_to_ask(self) -> None:
        job = self.job_of(self.start())

        self.assertEqual((job.kind, job.stoppable, job.stop_link),
                         (f"{NAME}.work", True, f"/api/v1/ext/{NAME}/jobs/work/stop"))

    def test_a_job_not_started_to_be_stopped_offers_no_stop(self) -> None:
        job = self.job_of(self.start(stoppable=False))

        self.assertEqual((job.stoppable, job.stop_link), (False, ""))
        self.assertEqual(self.ok(self.post("/jobs/work/stop")), {"stopped": False})
        self.assertFalse(job.halt.is_set())

    def test_a_stoppable_job_ends_early_when_it_is_asked_to(self) -> None:
        job = self.job_of(self.start())

        self.assertEqual(self.ok(self.post("/jobs/work/stop")), {"stopped": True})

        self.settle(lambda: job.state == jobs.DONE)
        self.assertTrue(job.stopped)
        self.assertEqual(job.result, "stopped")

    def test_a_kind_nothing_is_running_is_not_stopped(self) -> None:
        self.assertEqual(self.ok(self.post("/jobs/work/stop")), {"stopped": False})

    def test_a_job_that_has_finished_is_not_stopped(self) -> None:
        job = self.job_of(self.start())
        STATE["release"].set()
        self.settle(lambda: job.state == jobs.DONE)

        self.assertEqual(self.ok(self.post("/jobs/work/stop")), {"stopped": False})

    def test_another_kind_is_left_running(self) -> None:
        job = self.job_of(self.start(kind="other"))

        self.assertEqual(self.ok(self.post("/jobs/work/stop")), {"stopped": False})

        self.assertFalse(job.halt.is_set())

    def test_another_extensions_job_of_that_kind_is_left_running(self) -> None:
        theirs = jobs.submit("bystander.work", lambda job: job.halt.wait(5))

        self.assertEqual(self.ok(self.post("/jobs/work/stop")), {"stopped": False})

        self.assertFalse(theirs.halt.is_set())
        theirs.halt.set()


class Absent(ServedCase):
    def test_an_extension_the_registry_does_not_hold_is_a_404(self) -> None:
        empty = host.Registry(store.ExtensionStore(self.root / "other"))
        client = client_for(self, empty, self.record)

        found = client.get(f"{self.base}/settings")

        self.assertEqual(found.status_code, 404, found.text)
        self.assertEqual(found.json()["error"]["message"],
                         t("error.extensions.not_found", name=NAME))

    def test_an_extension_switched_off_refuses_on_every_route(self) -> None:
        self.registry.switch(NAME, False)
        message = t("error.extensions.not_running", display=self.display(),
                    value=self.record.reason)

        for method, path in (("get", "/settings"), ("put", "/settings"),
                             ("get", "/lists/things"), ("get", "/lists/things/about"),
                             ("post", "/lists/things/about/acts/refresh"),
                             ("get", "/actions/greet"), ("post", "/actions/greet/check"),
                             ("post", "/actions/greet/run"), ("get", "/actions/greet/progress"),
                             ("post", "/jobs/work/stop")):
            with self.subTest(route=f"{method} {path}"):
                found = self.client.request(method.upper(), self.base + path, json={})
                self.refused(found, 404, message)

    def test_the_whole_api_says_501_and_why_for_one_switched_off(self) -> None:
        self.registry.switch(NAME, False)
        api = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        found = api.get(f"{self.base}/settings")

        self.assertEqual(found.status_code, 501, found.text)
        self.assertIn(self.record.reason, found.json()["error"]["message"])
        self.assertIn("Served", found.json()["error"]["message"])

    def test_it_answers_again_once_it_is_switched_back_on(self) -> None:
        self.registry.switch(NAME, False)
        self.registry.switch(NAME, True)

        self.ok(self.get("/settings"))


if __name__ == "__main__":
    unittest.main()
