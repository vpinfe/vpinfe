"""`--check-extension`: an extension loaded the way the host loads it, and a line for each
thing it declared and each read it answered, ending in whether it would pass."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

import cli
from common import extensions, i18n
from common.extensions import check, contract
from common.extensions.contract import Unavailable
from common.extensions.games import offered, withdraw_all
from common.extensions.hardware import HARDWARE
from tests.support.identity import IDENTITY

MODULE = "tests.extensions.test_check_extension"
NAME = "checked"

WORDS = {
    "settings.greeting.label": "Greeting",
    "settings.mode.label": "Mode",
    "list.things.title": "Things",
    "list.things.column.name.header": "Name",
    "list.things.view.all.name": "Everything",
    "list.things.verb.drop.label": "Drop",
    "list.things.verb.drop.confirm": {"one": "Drop {count} row?", "other": "Drop {count} rows?"},
    "list.things.verb.drop.needs": "Only a row that can be dropped",
    "action.greet.label": "Greet",
}

# What the functions below do this time, and what they were asked to do.
HOW: dict[str, Any] = {}
CALLS: list[str] = []


def register(ctx: Any) -> None:
    """Every kind of declaration there is, behaving as `HOW` says."""
    from fastapi import APIRouter

    ctx.ui.settings([
        {"key": "greeting", "type": "string", "default": "hello"},
        {"key": "mode", "type": "select", "choices": [["fast", "Fast"], ["slow", "Slow"]]},
    ])

    def rows() -> list:
        CALLS.append("rows")
        if HOW.get("rows") is not None:
            raise HOW["rows"]
        return [{"name": HOW.get("shows", "a")}, {"name": "b"}]

    def status() -> str:
        CALLS.append("status")
        return "2 things"

    ctx.ui.list("things", rows, columns=[{"field": "name"}], views=[{"key": "all"}],
                status=status, acts={"refresh": lambda: CALLS.append("refresh")},
                verbs=[{"key": "drop", "run": lambda rows: CALLS.append("drop"),
                        "danger": True, "needs": "ok"}], area="community")

    class Greet:
        def ask(self, step: str) -> dict:
            CALLS.append("ask")
            if HOW.get("ask") is not None:
                raise HOW["ask"]
            return {"fields": [{"key": "name", "type": "string"}]}

        def check(self, values: dict) -> None:
            CALLS.append("check")

        def act(self, act: str, values: dict) -> None:
            CALLS.append("act")

        def run(self, values: dict) -> None:
            CALLS.append("run")

    ctx.ui.action("greet", Greet())
    ctx.ui.account(read=lambda player_id: {"status": "Fixed"})
    routes = APIRouter()
    routes.add_api_route("/hello", lambda: {}, methods=["GET"])
    ctx.add_router(routes)


def build(root: Path, body: str | None = None, *, name: str = NAME,
          words: dict | None = None, readme: bool = True, **manifest: Any) -> Path:
    """An extension in `root`; its `register` is the one above unless `body` is given."""
    directory = root / name
    (directory / "i18n").mkdir(parents=True)
    if readme:
        (directory / "README.md").write_text("# Checked\n", encoding="utf-8")
    (directory / "extension.json").write_text(json.dumps({
        "name": name, "display_name": "Checked", "version": "1.2.3",
        "description": "Declares what the check reads.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": ["config:own", "ui:mount"], **manifest}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(
        json.dumps(WORDS if words is None else words), encoding="utf-8")
    (directory / "__init__.py").write_text(
        body if body is not None else f"from {MODULE} import register as register\n",
        encoding="utf-8")
    return directory


class CheckCase(unittest.TestCase):
    # The extensions built here reach this module through a `tests` import so the test can
    # steer them, which the import rule refuses; only a case that tests the rule keeps it.
    checks_the_boundary = False

    def setUp(self) -> None:
        if not self.checks_the_boundary:
            patcher = unittest.mock.patch.object(check, "_boundary", return_value=[])
            patcher.start()
            self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        HOW.clear()
        CALLS.clear()
        self.addCleanup(HOW.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")

    def checked(self, directory: Path | None = None) -> tuple[list[str], bool]:
        return check.check(directory or build(self.root))

    def line(self, lines: list[str], start: str) -> str:
        found = [one for one in lines if one.startswith(start)]
        self.assertEqual(len(found), 1, f"{start!r} in {lines}")
        return found[0]


class AMissingReadme(CheckCase):
    def test_it_warns_and_still_passes(self) -> None:
        lines, passed = self.checked(build(self.root, readme=False))

        self.assertTrue(passed)
        self.assertTrue(self.line(lines, "warns").startswith("warns      no README.md"))
        self.assertEqual(lines[-1], self.line(lines, "warns"))

    def test_a_readme_in_any_case_is_found(self) -> None:
        directory = build(self.root, readme=False)
        (directory / "readme.txt").write_text("Help\n", encoding="utf-8")

        lines, _ = self.checked(directory)

        self.assertFalse([one for one in lines if one.startswith("warns")])


class AGroupOnAPanel(CheckCase):
    BODY = (
        "def register(ctx):\n"
        "    ctx.ui.panel_group('game', lambda who: None, section='play',\n"
        "                       save=lambda who, values: None)\n"
        "    ctx.ui.panel_group('table', lambda who: None)\n")

    def test_it_says_each_group_where_it_goes_and_whether_it_has_fields(self) -> None:
        lines, passed = check.check(build(self.root, self.BODY, scopes=["games:read"],
                                          words={}))

        self.assertIn("group      on a game's panel, at the end of play, with fields", lines)
        self.assertIn("group      on a table's panel, in a section of its own", lines)
        self.assertTrue(passed, lines)

    def test_a_group_without_the_scope_to_read_games_fails_to_load(self) -> None:
        lines, passed = check.check(build(self.root, self.BODY, words={}))

        self.assertFalse(passed)
        self.assertIn("games:read", lines[0])


class AVerbInAMenu(CheckCase):
    BODY = (
        "def register(ctx):\n"
        "    ctx.ui.verb('game', 'ship', lambda who: None)\n"
        "    ctx.ui.verb('table', 'scrub', lambda who: None, danger=True)\n"
        "    ctx.ui.verb('game', 'tidy', lambda who: None, private_ok=True)\n")

    def test_it_says_each_entry_and_whether_it_asks_first(self) -> None:
        lines, passed = check.check(build(self.root, self.BODY, scopes=["games:read"],
                                          words={}))

        self.assertIn("verb       ship in the game menu", lines)
        self.assertIn("verb       scrub in the table menu, asks first", lines)
        self.assertIn("verb       tidy in the game menu, works on Private games", lines)
        self.assertTrue(passed, lines)


class ASection(CheckCase):
    BODY = (
        "def register(ctx):\n"
        "    ctx.ui.section('week', lambda: {'facts': []}, title='Week',\n"
        "                   save=lambda values: None)\n"
        "    ctx.ui.panel(area='system')\n")

    def test_it_says_the_section_and_the_panel_and_reads_the_section(self) -> None:
        lines, passed = check.check(build(self.root, self.BODY, words={}))

        self.assertIn("section    week, with fields", lines)
        self.assertIn("panel      in system", lines)
        self.assertIn("reads      section week: 0 facts, 0 fields, 0 rows", lines)
        self.assertTrue(passed, lines)

    def test_a_section_whose_answer_core_cannot_draw_fails(self) -> None:
        body = self.BODY.replace("{'facts': []}", "{'color': 'red'}")

        lines, passed = check.check(build(self.root, body, words={}))

        self.assertFalse(passed)
        self.assertTrue(any(one.startswith("fails      section week") for one in lines), lines)


class ANeed(CheckCase):
    BODY = (
        "def register(ctx):\n"
        "    ctx.ui.list('plays', lambda: [], columns=[{'field': 'name'}], area='community')\n"
        "    ctx.ui.needs({'down': {'list': 'plays', 'tone': 'danger'}},\n"
        "                 lambda: [{'kind': 'down', 'ids': ['a', 'b']}])\n")

    def test_it_says_each_kind_and_what_the_read_found(self) -> None:
        words = {"need.down.label": "{count} are down", "list.plays.title": "Plays",
                 "list.plays.column.name.header": "Name"}

        lines, passed = check.check(build(self.root, self.BODY, words=words))

        self.assertIn("need       down opens the list plays, danger", lines)
        self.assertIn("reads      needs: down (2)", lines)
        self.assertTrue(passed, lines)

    def test_a_kind_with_no_label_is_missing_and_it_fails(self) -> None:
        words = {"list.plays.title": "Plays", "list.plays.column.name.header": "Name"}

        lines, passed = check.check(build(self.root, self.BODY, words=words))

        self.assertFalse(passed)
        self.assertTrue(any(one.startswith(f"missing    ext.{NAME}.need.down.label")
                            for one in lines), lines)


class WhatTheExtensionImports(CheckCase):
    """Protects: (b) the import rule an author is told about is the one the check applies."""

    checks_the_boundary = True

    def source(self, body: str, **more: str) -> Path:
        directory = build(self.root, body=body)
        for name, text in more.items():
            (directory / name).write_text(text, encoding="utf-8")
        return directory

    def test_what_the_contract_allows_passes(self) -> None:
        lines, passed = self.checked(self.source(
            "import json\nfrom fastapi import APIRouter\nimport requests\n"
            "from common.extensions.contract import words\n\n\ndef register(ctx): pass\n"))

        self.assertFalse([one for one in lines if " imports " in one], lines)

    def test_a_dependency_nobody_listed_fails_with_the_file_and_line(self) -> None:
        lines, passed = self.checked(self.source(
            "import json\nimport paho\n\n\ndef register(ctx): pass\n"))

        self.assertFalse(passed)
        said = self.line(lines, "fails      imports paho")
        self.assertIn("__init__.py, line 2", said)
        self.assertIn("a dependency the manifest lists", said)

    def test_a_listed_dependency_covers_an_import_of_its_name(self) -> None:
        directory = build(self.root, body="import paho_mqtt\n\n\ndef register(ctx): pass\n",
                          dependencies=["paho-mqtt==2.1.0"])

        lines, passed = self.checked(directory)

        self.assertFalse([one for one in lines if " imports " in one], lines)

    def test_an_import_no_listed_name_matches_warns_where_dependencies_are_listed(self) -> None:
        """`paho` is what `paho-mqtt` is imported as, and the check does not fetch the wheel
        that says so, so it cannot call this a failure."""
        directory = build(self.root, body="import paho\n\n\ndef register(ctx): pass\n",
                          dependencies=["paho-mqtt==2.1.0"])

        lines, _ = self.checked(directory)

        self.assertFalse([one for one in lines if one.startswith("fails      imports")], lines)
        self.assertIn("unless a dependency it lists provides it",
                      self.line(lines, "warns      imports paho"))

    def test_a_module_of_ours_fails_and_says_to_use_the_context(self) -> None:
        lines, passed = self.checked(self.source(
            "from game_service import x\nfrom common.config import paths\n\n\n"
            "def register(ctx): pass\n"))

        self.assertFalse(passed)
        said = self.line(lines, "fails      imports common.config")
        self.assertIn("through its context", said)

    def test_each_module_is_said_once_whatever_the_number_of_imports(self) -> None:
        lines, _ = self.checked(self.source(
            "import paho\n\n\ndef register(ctx):\n    import paho.mqtt\n",
            **{"more.py": "import paho\n"}))

        said = [one for one in lines if one.startswith("fails      imports paho ")]
        self.assertEqual(1, len(said))

    def test_the_extensions_own_tests_are_not_read(self) -> None:
        directory = self.source("def register(ctx): pass\n")
        (directory / "tests").mkdir()
        (directory / "tests" / "test_it.py").write_text("import pytest\n", encoding="utf-8")

        lines, _ = self.checked(directory)

        self.assertFalse([one for one in lines if "pytest" in one])

    def test_a_compiled_file_fails_with_its_path(self) -> None:
        directory = self.source("def register(ctx): pass\n")
        (directory / "lib").mkdir()
        (directory / "lib" / "fast.so").write_bytes(b"\x7fELF")

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertIn("compiled file lib/fast.so", self.line(lines, "fails      compiled file"))

    def test_a_file_that_is_not_python_fails_with_its_name(self) -> None:
        directory = self.source("def register(ctx): pass\n", **{"broken.py": "def (:\n"})

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertTrue([one for one in lines if "broken.py" in one], lines)


class AGoodExtension(CheckCase):
    def test_it_passes(self) -> None:
        lines, passed = self.checked()

        self.assertTrue(passed, lines)

    def test_it_says_what_loaded(self) -> None:
        self.assertEqual(self.checked()[0][0], "loads      checked 1.2.3")

    def test_it_says_each_setting_with_its_type_and_default(self) -> None:
        lines, _ = self.checked()

        self.assertIn("settings   greeting (string, default 'hello')", lines)
        self.assertIn("settings   mode (select, default 'fast')", lines)

    def test_it_says_each_list_with_where_it_sits_and_its_shape(self) -> None:
        lines, _ = self.checked()

        self.assertIn("list       things in community: 1 columns, 1 views, 1 verbs", lines)

    def test_it_says_each_dialog_with_the_methods_it_has(self) -> None:
        lines, _ = self.checked()

        self.assertIn("dialog     greet: ask check act run", lines)

    def test_it_says_it_holds_accounts_and_that_it_has_routes(self) -> None:
        lines, _ = self.checked()

        self.assertIn("account    a player can hold one", lines)
        self.assertIn("routes     its own, on ext:checked:library (1 routes)", lines)

    def test_it_says_what_each_read_found(self) -> None:
        lines, _ = self.checked()

        self.assertIn("reads      list things: 2 rows, acts: refresh", lines)
        self.assertIn("reads      settings: 2 fields", lines)
        self.assertIn("reads      dialog greet: first step has 1 fields", lines)

    def test_the_declarations_come_before_the_reads(self) -> None:
        lines, _ = self.checked()
        kinds = [one.split()[0] for one in lines]

        self.assertEqual(kinds, ["loads", "author", "source", "settings", "settings", "list",
                                 "dialog", "account", "routes", "reads", "reads", "reads"])

    def test_it_reads_and_sends_nothing(self) -> None:
        self.checked()

        self.assertEqual(sorted(set(CALLS)), ["ask", "rows", "status"])
        self.assertNotIn("run", CALLS)
        self.assertNotIn("act", CALLS)
        self.assertNotIn("refresh", CALLS)

    def test_it_can_be_given_the_folder_as_text(self) -> None:
        lines, passed = check.check(str(build(self.root)))

        self.assertTrue(passed, lines)

    def test_it_leaves_the_process_as_it_found_it(self) -> None:
        before = extensions.registry()
        self.checked()

        self.assertIs(extensions.registry(), before)
        self.assertIsNone(before.get(NAME))
        self.assertEqual(i18n.first_key(f"ext.{NAME}.list.things.title"), "")

    def test_it_can_be_run_again_on_the_same_extension(self) -> None:
        directory = build(self.root)

        self.assertEqual(check.check(directory), check.check(directory))

    def test_it_logs_nothing_at_error_or_above_while_it_runs(self) -> None:
        HOW["rows"] = ValueError("The service said no")

        with self.assertNoLogs("vpinfe", "WARNING"):
            self.checked()

    def test_it_restores_the_logging_it_found(self) -> None:
        import logging

        before = logging.root.manager.disable
        self.checked()

        self.assertEqual(logging.root.manager.disable, before)


class AWordMissing(CheckCase):
    def test_a_label_left_to_the_catalog_with_no_entry_is_missing_and_it_fails(self) -> None:
        words = {key: one for key, one in WORDS.items() if key != "settings.greeting.label"}

        lines, passed = self.checked(build(self.root, words=words))

        self.assertFalse(passed)
        self.assertIn("missing    ext.checked.settings.greeting.label - a setting's label",
                      lines)

    def test_each_word_a_declaration_leaves_to_the_catalog_is_asked_for(self) -> None:
        lines, passed = self.checked(build(self.root, words={}))

        self.assertFalse(passed)
        self.assertEqual(
            sorted(one.split(" - ")[0].split()[1] for one in lines
                   if one.startswith("missing")),
            ["ext.checked.action.greet.label", "ext.checked.list.things.column.name.header",
             "ext.checked.list.things.title", "ext.checked.list.things.verb.drop.confirm",
             "ext.checked.list.things.verb.drop.label",
             "ext.checked.list.things.verb.drop.needs",
             "ext.checked.list.things.view.all.name",
             "ext.checked.settings.greeting.label", "ext.checked.settings.mode.label"])

    def test_a_missing_word_comes_after_everything_that_was_read(self) -> None:
        lines, _ = self.checked(build(self.root, words={}))

        kinds = [one.split()[0] for one in lines]
        self.assertEqual(kinds[-9:], ["missing"] * 9)
        self.assertNotIn("missing", kinds[:-9])

    def test_a_name_and_description_are_asked_for_when_the_manifest_has_neither(self) -> None:
        directory = build(self.root, display_name="", description="")

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertIn("missing    ext.checked.name - the extension's name", lines)
        self.assertIn("missing    ext.checked.description - the extension's description",
                      lines)

    def test_a_name_given_in_the_catalog_instead_is_not_missing(self) -> None:
        words = {**WORDS, "name": "Checked", "description": "Says it here"}

        lines, passed = self.checked(build(self.root, words=words, display_name="",
                                           description=""))

        self.assertTrue(passed, lines)

    def test_words_given_where_they_are_declared_need_no_entry(self) -> None:
        body = (
            "def register(ctx):\n"
            "    ctx.ui.settings([{'key': 'a'}])\n"
            "    ctx.ui.list('l', lambda: [], title='Mine', columns=[\n"
            "        {'field': 'f', 'header': 'F'}], views=[{'key': 'v', 'name': 'V'}],\n"
            "        area='community')\n"
            "    ctx.ui.action('go', type('D', (), {'ask': lambda self, step: {'fields': []}})(),\n"
            "                  label='Go')\n")

        lines, _ = self.checked(build(self.root, body, words={"settings.a.label": "A"}))

        self.assertEqual([one for one in lines if one.startswith("missing")], [])

    def test_each_state_is_asked_for_its_word_and_its_why_is_optional(self) -> None:
        body = (
            "def register(ctx):\n"
            "    ctx.ui.list('l', lambda: [], title='Mine', columns=[\n"
            "        {'field': 'f', 'header': 'F'},\n"
            "        {'field': 's', 'header': 'S', 'kind': 'state',\n"
            "         'states': {'live': {'tier': 'on'}, 'late': {}}}], area='community')\n")

        lines, passed = self.checked(build(self.root, body, words={
            "list.l.column.s.state.live": "Live"}))

        self.assertFalse(passed)
        self.assertEqual(["ext.checked.list.l.column.s.state.late - a state's word"],
                         [one.split(None, 1)[1] for one in lines
                          if one.startswith("missing")])

    def test_a_read_that_shows_a_key_instead_of_words_names_the_read(self) -> None:
        HOW["shows"] = "ext.checked.nothing.here"

        lines, passed = self.checked()

        self.assertFalse(passed)
        self.assertIn("missing    ext.checked.nothing.here - shown by list things", lines)

    def test_a_key_of_another_extension_is_not_a_stray_of_this_one(self) -> None:
        HOW["shows"] = "ext.another.nothing.here"

        lines, passed = self.checked()

        self.assertTrue(passed, lines)


class AReadThatFails(CheckCase):
    def test_rows_that_raise_fail_the_extension_with_the_line_they_said(self) -> None:
        HOW["rows"] = ValueError("The service said no")

        lines, passed = self.checked()

        self.assertFalse(passed)
        self.assertIn("The service said no", self.line(lines, "fails      list things:"))

    def test_a_failure_comes_after_the_reads_that_went_well(self) -> None:
        HOW["rows"] = ValueError("The service said no")

        lines, _ = self.checked()

        self.assertEqual([one.split()[0] for one in lines][-3:], ["reads", "reads", "fails"])

    def test_a_dialog_whose_first_step_raises_fails_it(self) -> None:
        HOW["ask"] = ValueError("No first step")

        lines, passed = self.checked()

        self.assertFalse(passed)
        self.assertIn("No first step", self.line(lines, "fails      dialog greet:"))

    def test_one_failing_read_does_not_stop_the_others(self) -> None:
        HOW["rows"] = ValueError("The service said no")

        lines, _ = self.checked()

        self.assertIn("reads      settings: 2 fields", lines)
        self.assertIn("reads      dialog greet: first step has 1 fields", lines)

    def test_a_step_with_a_field_the_wizard_does_not_draw_fails_it(self) -> None:
        body = ("def register(ctx):\n"
                "    ctx.ui.action('go', type('D', (), {'ask': lambda self, step: {\n"
                "        'fields': [{'key': 'k', 'type': 'rating'}]}})(), label='Go')\n")

        lines, passed = self.checked(build(self.root, body, words={}))

        self.assertFalse(passed)
        self.assertIn("Drawn:", self.line(lines, "fails      dialog go:"))

    def test_rows_that_are_not_rows_fail_it(self) -> None:
        body = ("def register(ctx):\n"
                "    ctx.ui.list('l', lambda: {'a': 1}, title='L', columns=[\n"
                "        {'field': 'f', 'header': 'F'}], area='community')\n")

        lines, passed = self.checked(build(self.root, body, words={}))

        self.assertFalse(passed)
        self.line(lines, "fails      list l:")


class AServiceThatIsDown(CheckCase):
    def test_rows_that_cannot_reach_their_service_say_so_and_still_pass(self) -> None:
        HOW["rows"] = Unavailable("The service did not answer")

        lines, passed = self.checked()

        self.assertTrue(passed, lines)
        self.assertEqual(self.line(lines, "reads      list things:"),
                         "reads      list things: could not reach its service - "
                         "The service did not answer")
        self.assertEqual([one for one in lines if one.startswith("fails")], [])

    def test_a_dialog_that_cannot_reach_its_service_says_so_and_still_passes(self) -> None:
        HOW["ask"] = Unavailable("The service did not answer")

        lines, passed = self.checked()

        self.assertTrue(passed, lines)
        self.assertIn("reads      dialog greet: could not reach its service - "
                      "The service did not answer", lines)

    def test_a_down_service_does_not_excuse_a_missing_word(self) -> None:
        HOW["rows"] = Unavailable("The service did not answer")
        words = {key: one for key, one in WORDS.items() if key != "list.things.title"}

        lines, passed = self.checked(build(self.root, words=words))

        self.assertFalse(passed)
        self.assertIn("missing    ext.checked.list.things.title - a list's title", lines)


class AnExtensionThatDoesNotLoad(CheckCase):
    def test_a_register_that_raises_fails_with_what_it_raised(self) -> None:
        body = "def register(ctx):\n    raise RuntimeError('boom')\n"

        lines, passed = self.checked(build(self.root, body))

        self.assertFalse(passed)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("fails      "), lines)
        self.assertIn("RuntimeError: boom", lines[0])

    def test_a_package_with_no_register_fails(self) -> None:
        lines, passed = self.checked(build(self.root, "x = 1\n"))

        self.assertFalse(passed)
        self.assertIn("register", lines[0])

    def test_a_declaration_the_context_refuses_fails(self) -> None:
        body = "def register(ctx):\n    ctx.ui.settings([{'key': 'a', 'type': 'nonsense'}])\n"

        lines, passed = self.checked(build(self.root, body))

        self.assertFalse(passed)
        self.assertIn("a settings page does not draw", lines[0])

    def test_a_module_that_does_not_import_fails(self) -> None:
        lines, passed = self.checked(build(self.root, "import nothing_called_this\n"))

        self.assertFalse(passed)
        self.assertIn("nothing_called_this", lines[0])

    def test_a_manifest_that_is_not_json_fails(self) -> None:
        directory = build(self.root)
        (directory / "extension.json").write_text("{not json", encoding="utf-8")

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("fails      "), lines)

    def test_a_folder_with_no_manifest_fails(self) -> None:
        directory = build(self.root)
        (directory / "extension.json").unlink()

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertIn("extension.json", lines[0])

    def test_a_manifest_that_names_another_extension_fails(self) -> None:
        directory = build(self.root)
        manifest = json.loads((directory / "extension.json").read_text(encoding="utf-8"))
        (directory / "extension.json").write_text(
            json.dumps({**manifest, "name": "another"}), encoding="utf-8")

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertTrue(lines[0].startswith("fails      "), lines)

    def test_a_manifest_that_asks_for_a_platform_this_is_not_fails(self) -> None:
        directory = build(self.root, requires_platform=contract.PLATFORM_ABI + 1)

        lines, passed = self.checked(directory)

        self.assertFalse(passed)
        self.assertTrue(lines[0].startswith("fails      "), lines)
        self.assertEqual(len(lines), 1)

    def test_a_folder_that_is_not_there_fails(self) -> None:
        lines, passed = check.check(self.root / "nowhere")

        self.assertFalse(passed)
        self.assertTrue(lines[0].startswith("fails      "), lines)

    def test_an_extension_that_does_not_load_still_leaves_nothing_behind(self) -> None:
        body = "def register(ctx):\n    raise RuntimeError('boom')\n"

        self.checked(build(self.root, body))

        self.assertIsNone(extensions.registry().get(NAME))
        self.assertEqual(i18n.first_key(f"ext.{NAME}.list.things.title"), "")


class TheFlag(CheckCase):
    def setUp(self) -> None:
        super().setUp()
        withdraw_all()
        self.addCleanup(withdraw_all)

    def run_flag(self, folder: Path) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = cli.check_extension(str(folder))
        return status, out.getvalue()

    def test_a_good_extension_prints_its_report_and_exits_0(self) -> None:
        directory = build(self.root)

        status, printed = self.run_flag(directory)

        self.assertEqual(status, 0)
        self.assertEqual(printed.splitlines(), self.checked(directory)[0])

    def test_the_flag_offers_the_calls_core_offers_a_running_vpinfe(self) -> None:
        self.run_flag(build(self.root))

        self.assertIn("list_games", offered())
        self.assertIn("devices", HARDWARE)

    def test_a_failing_one_prints_why_and_exits_1(self) -> None:
        HOW["rows"] = ValueError("The service said no")

        status, printed = self.run_flag(build(self.root))

        self.assertEqual(status, 1)
        self.assertIn("fails      list things:", printed)

    def test_an_unreachable_service_exits_0(self) -> None:
        HOW["rows"] = Unavailable("The service did not answer")

        status, printed = self.run_flag(build(self.root))

        self.assertEqual(status, 0)
        self.assertIn("could not reach its service", printed)

    def test_a_missing_word_exits_1(self) -> None:
        status, printed = self.run_flag(build(self.root, words={}))

        self.assertEqual(status, 1)
        self.assertIn("missing    ext.checked.", printed)

    def test_the_flag_is_wired_to_that_function(self) -> None:
        directory = build(self.root)
        with (unittest.mock.patch("sys.argv", ["vpinfe", "--check-extension", str(directory)]),
              contextlib.redirect_stdout(io.StringIO()) as printed,
              self.assertRaises(SystemExit) as raised):
            cli.parse_args()

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("loads      checked 1.2.3", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
