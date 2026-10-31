"""One control grammar, and what is allowed to sit outside it.

The Manager had two answers to "what control does this type want": `settings.control_for`
for a config option, and a copy of it in the themes page for a theme's own options. Two
answers drift the moment either gains a type, which is what these pin.

The budget below is the other half. A raw `ui.*` control is not wrong - a toolbar search
box has no label column and sets nothing, so it is not a fact row and forcing it through
`panel` would be worse. What is wrong is a *new* one appearing without anybody deciding
that, so each module says how many it has and why.
"""

from __future__ import annotations

import asyncio
import re
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from manager.frontend import themes
from manager.kit import panel
from manager.settings import binding_table, settings

MANAGER = Path(__file__).resolve().parent.parent.parent / "manager"

# Not `select`: a dropdown is always `dropdown.Select`, which
# `test_every_dropdown_is_one.py` holds to.
CONTROLS = re.compile(r"ui\.(switch|checkbox|input|number|textarea)\(")

# What each module draws by hand, and why it is not a fact row. `panel.py` is the home of
# the grammar and is not counted. Raising a number is a decision: say what the control is
# for, or use `panel`.
BUDGET = {
    "collection.py": (5, "a collection's rule rows and order bar, which are rows of several "
                         "controls rather than a label and a value, with the find box over "
                         "its members"),
    "game_table.py": (1, "a field with bespoke wiring - chips, debounce"),
    "match_group.py": (1, "a tick per detail a re-match would change: a multi-select would "
                          "hide the old and new values, which are the whole of what is "
                          "being decided"),
    "all_settings.py": (1, "the changed-only tick in All Settings' filter bar, which asks "
                           "what to show rather than holds a value"),
    "logs.py": (1, "the find box in the control bar above the viewport"),
    "jobs.py": (1, "the find box in the control bar above the grid"),
    "wizard_lists.py": (1, "the per-row tick in a checklist step, a choice over a list "
                           "like the import confirmation's"),
    "games.py": (1, "a tick per column in the column picker, a menu rather than a fact "
                    "row. The view picker is `panel.DescribedSelect`, which is where a "
                    "picker whose options explain themselves lives"),
    "move_picker.py": (1, "the tick per table in Choose Tables, which can be neither on "
                          "nor off while a table is to have only some of the files: a "
                          "choice over a list, not a fact row"),
    "launchers.py": (2, "checkboxes in the copy-to-device dialog"),
    "clipboard.py": (1, "the textarea a browser that will not copy falls back to"),
    "token_panel.py": (1, "the read-only box a new token is shown in, once, so it can "
                         "be selected and copied"),
    "authenticator_panel.py": (1, "the read-only box the setup key is shown in, so it can be "
                                  "selected and copied when the QR cannot be scanned"),
    "placement_panel.py": (1, "the textarea that shows the one line to copy into a "
                              "compositor config, read-only"),
    "backups.py": (2, "the name and the optional note typed into the Back up now dialog, "
                      "which describe the copy being made rather than holding a setting"),
    "import_dialog.py": (2, "the per-item tick in the import confirmation and the "
                            "destination picker's do-not-ask-again - a choice over a list "
                            "and a one-off promise, neither of them a fact row"),
    "drop_sheet.py": (2, "the tick on a file with no place yet, disabled until it has one, "
                         "and the one on a refused file, disabled for good: the same "
                         "per-file choice as the confirmation's rows, which a fact row "
                         "would draw as a switch"),
    "import_swap.py": (1, "the tick on each file the old table has under its own name, in "
                          "the replace confirmation - a choice among files, which a fact "
                          "row would draw as a switch beside every one"),
    "dialog.py": (2, "the one-line and the several-line field every dialog asks with - "
                     "the panel's own, in the frame dialogs share, answering nothing until "
                     "the dialog does"),
    "remote.py": (1, "the find field above the game list, a question about what to show "
                     "rather than a fact row. A library is longer than a screen, and a "
                     "list longer than a screen is typed into rather than scrolled"),
    "vps_match.py": (1, "the query the catalog is searched with, which starts as the "
                        "game's name and is meant to be edited. Not a fact row: it holds "
                        "what is being asked, never what the game is"),
    "media_fill.py": (1, "a tick per kind in Get missing media, a choice over a list like the "
                       "import confirmation's per-item tick, not a fact row"),
    "record.py": (1, "a tick per kind in Record Media, a choice over a list as Get missing "
                     "art's is, with what the kind's slot holds beside it"),
    "folder_picker.py": (3, "the filter above the folder list, a toolbar box like a "
                           "grid's search; the Show hidden tick beside it, which asks "
                           "what to show as All Settings' ticks do; and the name typed "
                           "into the row a new folder takes in that list. None is a fact "
                           "row and none sets anything on the folder chosen"),
    "setup_vpx.py": (1, "the long and short sides of the playfield screen with the cm / in "
                        "toggle, as the Screen Size row in vpx_displays draws them"),
    "vpx_displays.py": (1, "the cm / in toggle beside the Screen Size numbers, which sets "
                           "the unit those two are written in - the unit of a row, not a "
                           "fact row of its own"),
}


class ThemeOptionsUseTheSharedGrammar(unittest.TestCase):
    """A theme's settings are settings, drawn the way every other setting is."""

    def test_every_declared_type_maps_onto_a_control(self) -> None:
        cases = {
            "boolean": "bool",
            "number": "number",
            "select": "choice",
            "textarea": "text",
            "json": "text",
            "": "text",
            "something-new": "text",
        }
        for declared, expected in cases.items():
            with self.subTest(declared=declared):
                found = themes._as_option({"key": "k", "type": declared,
                                           "options": ["a", "b"]})
                self.assertEqual(found["type"], expected)

    def test_a_number_keeps_its_bounds_and_its_fraction(self) -> None:
        """`int` was the nearest existing type and it is the wrong one: a theme declares
        scale factors, and formatting one as a whole number shows a value that is not
        the one stored."""
        found = themes._as_option({"key": "k", "type": "number", "min": 0.5, "max": 2,
                                   "step": 0.1})

        self.assertEqual(found["type"], "number")
        self.assertEqual((found["min"], found["max"], found["step"]), (0.5, 2, 0.1))

    def test_choices_reach_the_control_as_a_mapping(self) -> None:
        """A theme names its choices `{value: label}`. Flattened to a list, the stored
        value goes on screen where the label belongs."""
        found = themes._as_option({
            "key": "k", "type": "select",
            "options": [{"value": "hi", "label": "High"}, {"value": "lo", "label": "Low"}]})

        self.assertEqual(found["choices"], {"hi": "High", "lo": "Low"})


class WhatTheDialogHolds(unittest.TestCase):
    """Nothing is written until Save, so editing a control changes the dialog's own
    pending values.

    Unit-level because it is the wiring rather than the rendering. The round trip through
    a real browser is a separate thing and it does work - a Quasar toggle clicked in
    headless Chromium reaches the server and the value lands on disk.
    """

    def setUp(self) -> None:
        self.captured: dict = {}
        self.real = panel.switch

        def fake(value, on_change, **kw):
            self.captured["value"] = value
            self.captured["on_change"] = on_change
            return lambda: None

        settings.panel.switch = fake

    def tearDown(self) -> None:
        settings.panel.switch = self.real

    @staticmethod
    def _run(coroutine):
        # A loop of its own: `asyncio.run` unsets the thread's loop on the way out, and
        # the modules tested after this one build NiceGUI elements that need it.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coroutine)
        finally:
            loop.close()

    def _keep(self, wanted: dict) -> tuple[list, Callable[[], Any]]:
        written: list = []

        async def keep() -> bool:
            written.append(dict(wanted))
            return True

        return written, keep

    def test_changing_a_control_writes_the_value(self) -> None:
        wanted = {"k": False}
        written, keep = self._keep(wanted)

        rows = themes._rows([{"key": "k", "name": "Start", "type": "boolean"}], wanted, keep)

        self.assertEqual(rows[0][0], "Start")
        self.assertIs(self.captured["value"], False)
        self._run(self.captured["on_change"](SimpleNamespace(value=True)))
        self.assertEqual([{"k": True}], written)

    def test_json_is_parsed_before_it_is_written(self) -> None:
        wanted: dict = {"k": None}
        written, keep = self._keep(wanted)
        save = themes._saver({"key": "k", "type": "json"}, wanted, keep)

        self.assertTrue(self._run(save('{"a": 1}')))
        self.assertEqual(wanted["k"], {"a": 1})
        with patch.object(themes.ui, "notify", lambda *a, **k: None):
            self.assertFalse(self._run(save("{not json")))
        self.assertEqual(wanted["k"], {"a": 1}, "a refused value must not be stored")
        self.assertEqual(1, len(written), "nor written")
        self.assertTrue(self._run(save("  ")))
        self.assertIsNone(wanted["k"])

    def test_a_json_value_reaches_the_field_as_json(self) -> None:
        """A field handed an object renders Python's idea of it - single quotes and
        all - which is not what the theme would read back."""
        shown = themes._shown({"key": "k", "type": "json"}, {"k": {"a": 1}})

        self.assertIn('"a"', shown)


class ADeclaredEditorIsRouted(unittest.TestCase):
    """A setting can ask for a tool where a control cannot do the job, and it is routed
    the way `type` is - so declaring one is a line in the schema rather than a branch in
    the renderer."""

    def test_the_input_actions_ask_for_the_binding_editor(self) -> None:
        from common.config import config_schema

        asked = {one.editor for one in config_schema.options()
                 if one.section == "input"}

        self.assertEqual(asked, {config_schema.EDITOR_BINDING})

    def test_every_declared_editor_is_one_the_manager_serves(self) -> None:
        from common.config import config_schema

        declared = {one.editor for one in config_schema.options() if one.editor}
        # Drawn as one table over the whole section by its page, which the API's
        # consumers read as "bindings" without the Manager's routing.
        drawn_by_a_page = {config_schema.EDITOR_BINDING}

        self.assertTrue(declared - drawn_by_a_page <= set(settings.EDITORS),
                        "a setting asks for an editor nothing draws")
        self.assertTrue(declared <= set(config_schema.EDITORS),
                        "an editor name outside the closed set is a typo, not a tool")


class BindingsAreNotTypedIn(unittest.TestCase):
    """`pad:0/button:3` is written for a parser. The table shows what was pressed."""

    def test_a_default_arrives_as_a_string_and_is_not_read_letter_by_letter(self) -> None:
        """A stored value is a list; a schema default is the comma-joined string the
        config file would hold. `list()` of the second is one chip per character."""
        self.assertEqual(
            binding_table.selectors("key:ArrowLeft,key:ShiftLeft"),
            ["key:ArrowLeft", "key:ShiftLeft"])
        self.assertEqual(
            binding_table.selectors(["key:Enter"]), ["key:Enter"])
        self.assertEqual(binding_table.selectors(None), [])

    def test_a_chord_is_found_whichever_order_it_was_written(self) -> None:
        """A chord is a set. Looked up as raw text, one of the two rows holding it is
        marked and the other stays silent."""
        bound = {"back": ["chord(key:KeyA+key:KeyB)"], "exit": ["chord(key:KeyB+key:KeyA)"]}

        self.assertEqual(binding_table.holders_of(bound, "next", "chord(key:KeyB+key:KeyA)"),
                         ["back", "exit"])

    def test_it_names_the_other_rows_that_already_hold_a_binding(self) -> None:
        bound = {"back": ["key:Escape"], "exit": ["key:Escape"], "next": ["key:KeyM"]}

        self.assertEqual(binding_table.holders_of(bound, "back", "key:Escape"), ["exit"])
        self.assertEqual(binding_table.holders_of(bound, "exit", "key:Escape"), ["back"])
        self.assertEqual(binding_table.holders_of(bound, "back", "key:KeyZ"), [])


class AHoldCanBeSetRatherThanPerformed(unittest.TestCase):
    """You can hold a button for about a second and a half. You cannot hold it for
    exactly 1,500ms, and you may want one on a binding you already captured without."""

    def test_a_hold_is_added_to_a_binding_that_has_none(self) -> None:
        from common.config import input_registry

        self.assertEqual(input_registry.with_hold("chord(key:a+key:b)", 1500),
                         "chord(key:a+key:b)@hold:1500")

    def test_a_duration_is_changed_rather_than_stacked(self) -> None:
        from common.config import input_registry

        once = input_registry.with_hold("key:Escape", 1000)

        self.assertEqual(input_registry.with_hold(once, 2500), "key:Escape@hold:2500")
        self.assertEqual(input_registry.hold_ms(once), 1000)

    def test_zero_takes_the_hold_off_again(self) -> None:
        from common.config import input_registry

        self.assertEqual(
            input_registry.with_hold("chord(key:a+key:b)@hold:1500", 0),
            "chord(key:a+key:b)")

    def test_a_binding_with_no_hold_says_nothing_rather_than_zero(self) -> None:
        from common.config import input_registry

        self.assertEqual(input_registry.hold_ms("key:a"), 0)


class RawControlsAreDeclared(unittest.TestCase):
    def test_no_module_grows_one_unnoticed(self) -> None:
        found = {}
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name == "panel.py":
                continue
            count = len(CONTROLS.findall(path.read_text(encoding="utf-8")))
            if count:
                found[path.name] = count

        expected = {name: count for name, (count, _why) in BUDGET.items()}
        self.assertEqual(found, expected,
                         "a control outside panel.py is a decision - either draw it "
                         "with panel, or add it to BUDGET with what it is for")


if __name__ == "__main__":
    unittest.main()
