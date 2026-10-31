"""A label takes title case, the way `common/config/labels.py` writes one. A heading and a button
are the same thing; a sentence or a phrase stays in sentence case.

Every owner's labels are held to it - core, the extensions and the apps. A button is held
to it where the source draws it, and the pages drive holds every one it opens."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from common.config.labels import field_label
from tests.support import trees
from tests.support.catalogs import served

ROOT = Path(__file__).resolve().parents[2]
CATALOG = served()

# A label that names a program is written as that program writes itself.
_NAMED_AS_THE_PROGRAM = frozenset({
    f"config.tools.{name}_path.label"
    for name in ("grim", "nvidia_smi", "nvtop", "wf_recorder", "wtype", "ydotool")
})

# The frontend's windows are a part, not a title: the verb is capitalized and the part is not.
_FRONTEND_WINDOWS = frozenset(f"action.frontend.{name}" for name in ("start", "stop", "restart"))

# What the Manager's helpers draw as a button, a menu entry or a link that acts.
_BUTTONS = frozenset({
    "button", "quiet", "aside", "answer", "cancel", "action", "Verb", "menu_item", "menu_link",
    "link", "link_out", "_link", "_again", "tab"})

# A count with its noun is a quantity, which a person reads as a phrase.
_COUNTS = frozenset({"manager.workbench.more_settings"})

# The headings, group titles and links of an import's report, which the pages drive never
# opens, and the titles of dialogs it does not open either.
_HEADINGS = frozenset({"manager.placement.heading"}) | frozenset(
    f"manager.report.{name}" for name in (
        "know", "left_behind", "look", "imported", "failed", "need_rom", "not_matched",
        "no_wheel", "already", "dmd", "title", "add_missing", "add_rom", "add_wheel", "match",
        "open", "see_log", "try_again", "own_tab", "show_all")
) | {"manager.media_fill.title_several", "ext.library_importer.wizard.source.title"}

# The names down the left of the import summary, which sit beside the field labels.
_SUMMARY_ROWS = frozenset(
    {f"ext.library_importer.wizard.summary.{name}" for name in (
        "reads_as", "folder", "roms_missing", "matching", "not_matched", "already_here",
        "skipped", "how", "going_into", "space", "imported", "left_behind", "names",
        "details", "wheel_images")}
    | {f"ext.library_importer.count.{name}" for name in (
        "games", "tables", "media", "companions", "history", "matched")}
    | {f"ext.library_importer.kind.{name}.none" for name in (
        "tables", "media", "roms", "altdata", "music", "pups", "user", "history")}
    | {"ext.library_importer.wizard.media.wheels_as.label_plain"})


def _off_the_rule(keys: list[str]) -> dict[str, tuple[str, str]]:
    return {key: (CATALOG[key], field_label(CATALOG[key])) for key in keys
            if isinstance(CATALOG[key], str) and CATALOG[key] != field_label(CATALOG[key])}


def _keys(node: ast.AST) -> list[str]:
    if isinstance(node, ast.IfExp):
        return _keys(node.body) + _keys(node.orelse)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t" and node.args:
        return _keys(node.args[0])
    return []


def _button_keys(tree: ast.AST) -> list[str]:
    """Every catalog key the source gives a button, a menu entry or a link as its text:
    the first argument or `label=`."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)) \
                not in _BUTTONS:
            continue
        spots = node.args[:1] + [kw.value for kw in node.keywords if kw.arg == "label"]
        found += [key for spot in spots for key in _keys(spot)]
    return found


class LabelsAreTitleCase(unittest.TestCase):

    def test_every_label_is_in_the_case_field_label_writes(self) -> None:
        keys = [key for key in CATALOG
                if key.endswith(".label") and key not in _NAMED_AS_THE_PROGRAM]
        self.assertGreater(len(keys), 300, "the catalog held more labels than this found")
        self.assertEqual(_off_the_rule(keys), {})

    def test_the_headings_and_report_links_are_title_case(self) -> None:
        self.assertEqual(_off_the_rule(sorted(_HEADINGS)), {})

    def test_every_button_the_source_draws_is_title_case(self) -> None:
        drawn: set[str] = set()
        for root in ("manager", "common", "apps", "extensions"):
            for path in sorted((ROOT / root).rglob("*.py")):
                if "__pycache__" not in path.parts:
                    drawn.update(_button_keys(trees.tree_for(path)))
        self.assertGreater(len(drawn), 40, "an empty sweep passes and measures nothing")
        self.assertEqual(
            _off_the_rule(sorted(key for key in drawn if key in CATALOG and key not in _COUNTS
                                 and key not in _FRONTEND_WINDOWS)),
            {})

    def test_every_position_a_button_is_read_from(self) -> None:
        tree = trees.parse_snippet('ui.button(t("a"))\n'
                                   'quiet(label=t("b"))\n'
                                   'panel.answer(t("c") if on else t("d"), go)\n'
                                   'ui.label(t("e"))\n'
                                   'panel.Verb(t("f"), go)\n'
                                   'frame.menu_item(t("g"), go)\n'
                                   'panel.action(t("h" if on else "i"), go)\n'
                                   'ui.tab("name", label=t("j"))\n')
        self.assertEqual(sorted(_button_keys(tree)),
                         ["a", "b", "c", "d", "f", "g", "h", "i", "j", "name"])

    def test_the_names_down_the_import_summary_are_title_case(self) -> None:
        self.assertEqual(_off_the_rule(sorted(_SUMMARY_ROWS)), {})

    def test_a_placeholder_is_left_as_written_and_is_not_a_word_of_the_title(self) -> None:
        self.assertEqual(field_label("your {source} wheel images are"),
                         "Your {source} Wheel Images Are")
        self.assertEqual(field_label("{source}'s wheel"), "{source}'s Wheel")
        self.assertEqual(field_label("keep {source_name} for"), "Keep {source_name} For")


if __name__ == "__main__":
    unittest.main()
