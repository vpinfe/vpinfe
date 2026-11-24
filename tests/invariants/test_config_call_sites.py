"""Every setting the code reads is a setting the schema declares.

`config_schema` says it holds every setting VPinFE has, and the store fills a new file
from it - so a setting the schema does not know about is never written, never offered in
the Manager, and never covered by the naming and alias checks either. Two had already
slipped through that way, `assets_dir` and `wheelset`, both live and both documented.

Read from the source rather than by importing, so a call site behind a branch or an
optional dependency counts the same as one on the happy path.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from common.config import config_schema
from tests.support import trees
from tests.support.project import project_files
from tests.support.startup_reads import READERS, STORE_ACCESSORS, STORES

REPO =Path(__file__).resolve().parent.parent.parent

# Sections whose keys are declared elsewhere and resolved at runtime, so a literal here
# is a name in that vocabulary rather than a schema key.
GENERATED_SECTIONS = {"input"}


def _call_sites():
    """(file, line, section, key) for every reader called with both names literal."""
    for path in project_files("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", "third_party/")):
            continue
        try:
            tree = trees.tree_for(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 3:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in READERS:
                continue
            section, key = node.args[1], node.args[2]
            if not (isinstance(section, ast.Constant) and isinstance(key, ast.Constant)):
                continue
            if isinstance(section.value, str) and isinstance(key.value, str):
                yield rel, node.lineno, section.value, key.value


# `config_store` owns the 2.x migration, so it is the one place that reads a former
# section name off the parser on purpose.
OWNS_THE_OLD_NAMES = {"common/config/config_store.py"}

# Methods only a parser has, plus the ones it shares with a dict and so are counted only
# when called with a section and a key.
PARSER_ONLY = {"getboolean", "getfloat", "has_section", "remove_section"}
SECTION_AND_KEY = {"get", "set", "getint", "has_option", "remove_option"}
PARSERS = {"config", "parser", "cfg", "ini", "iniconfig"}


def _raw_reads():
    """(file, line, section) for every read taken straight off a parser.

    `cfg_get` and its siblings resolve a section that has been renamed and a 2.x
    spelling; a parser resolves neither, so it answers about the one name it is handed
    and reports a setting that is on as off.
    """
    for path in project_files("*.py"):
        rel = path.relative_to(REPO).as_posix()
        # An example extension's `ctx.config` is its own settings, not a parser.
        if rel.startswith(("tests/", "third_party/",
                           "docs/extensions/examples/")) \
                or rel in OWNS_THE_OLD_NAMES:
            continue
        try:
            tree = trees.tree_for(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = getattr(node.func.value, "attr", None) or \
                getattr(node.func.value, "id", "")
            if node.func.attr in PARSER_ONLY:
                pass
            elif (node.func.attr in SECTION_AND_KEY and len(node.args) >= 2
                  and receiver in PARSERS):
                pass
            else:
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield rel, node.lineno, first.value


def _store_reads():
    """(file, line, section, key) for every store accessor called with both names literal."""
    for path in project_files("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", "third_party/")) \
                or rel in OWNS_THE_OLD_NAMES:
            continue
        try:
            tree = trees.tree_for(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in STORE_ACCESSORS or len(node.args) < 2:
                continue
            receiver = getattr(node.func.value, "attr", None) or \
                getattr(node.func.value, "id", "")
            if receiver not in STORES:
                continue
            section, key = node.args[0], node.args[1]
            if not (isinstance(section, ast.Constant) and isinstance(key, ast.Constant)):
                continue
            if isinstance(section.value, str) and isinstance(key.value, str):
                yield rel, node.lineno, section.value, key.value


class CallSiteTests(unittest.TestCase):
    def test_every_setting_read_is_one_the_schema_declares(self) -> None:
        unknown = []
        for rel, line, section, key in _call_sites():
            if config_schema.canonical_section(section) in GENERATED_SECTIONS:
                continue
            resolved = config_schema.locate(section, key)
            if config_schema.option(*resolved) is None:
                unknown.append(f"{rel}:{line} reads {section}.{key}")
        self.assertEqual(sorted(unknown), [],
                         "declare it in config_schema, or it is written to no file and "
                         "offered in no UI")

    def test_no_parser_is_read_by_a_section_name_the_schema_dropped(self) -> None:
        """A rename reaches `cfg_get` and leaves a parser read where it was.

        Nothing raises when it does: a section the parser has never heard of answers the
        fallback, so the setting reads as its default and the feature is simply off.
        """
        stale = [f"{rel}:{line} reads [{section}] off the parser"
                 for rel, line, section in _raw_reads()
                 if section not in {option.section for option in config_schema.CONFIG_OPTIONS}]
        self.assertEqual(sorted(stale), [],
                         "read it with cfg_get or cfg_bool, which resolve a renamed "
                         "section and a 2.x spelling")

    def test_every_setting_the_store_is_asked_for_is_one_the_schema_declares(self) -> None:
        """A pair naming no declared setting reads as None, which a caller cannot tell
        from a setting nobody set."""
        unknown = [f"{rel}:{line} asks the store for {section}.{key}"
                   for rel, line, section, key in _store_reads()
                   if config_schema.option(*config_schema.locate(section, key)) is None]
        self.assertEqual(sorted(unknown), [],
                         "declare it, or read the section the setting moved to")

    def test_there_are_call_sites_to_check(self) -> None:
        """The scan is a regex-shaped thing; an empty result would pass silently."""
        self.assertGreater(len(list(_call_sites())), 20)

    def test_there_are_store_call_sites_to_check(self) -> None:
        """The receiver names are a whitelist, so a store held under a name that is not
        on it empties the scan."""
        self.assertTrue(list(_store_reads()))


if __name__ == "__main__":
    unittest.main()
