"""A closed set of named variants is a kind, and `type` is reserved for borrowed words.

`docs/conventions.md` settles this: media kinds, job kinds, asset kinds are **kinds**, in
identifiers and in the JSON we write. `type` survives only where somebody else already chose
the word - Python's own, HTTP's, a library's parameter, or a key already sitting on disk.

The cost of not picking one is on the record. `MEDIA_SPECS` and `MEDIA_TYPES` were the same
closed set under two words, and the second had fallen seven kinds behind before anything
noticed, which cost a KeyError on upload and an empty filename on lookup.

This does not judge whether a name describes a closed set - nothing mechanical can. It
catches a *new* `*_type` identifier, so the question gets asked once, at the point where the
answer is cheap, rather than after two names for one thing have drifted apart.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PACKAGES = ("apps", "common", "manager", "extensions", "frontend", "httpapi")

TYPE_NAME = re.compile(r"^[a-z][a-z0-9_]*_type$")

# Borrowed words, each with whose word it is. Adding to this list is a decision that the
# name is somebody else's rather than ours, not a way to quiet the check.
BORROWED = {
    # Python's own.
    "guess_type",        # mimetypes.guess_type
    "media_type",        # Starlette's keyword on a Response
    "compress_type",     # zipfile.ZipInfo's attribute
    # Holds a class and calls it, which is Python's own sense of the word.
    "field_type",
    # A key already on disk, kept end to end so nobody converts in their head. `Info.Type`
    # is in every .info a user has.
    "game_type",
    "table_type",
    # The user's own answer for that same field, and what it would say without one.
    "alt_type",
    "found_type",
    # A library's own parameter or callback signature.
    "service_type",      # Zeroconf's browser callback
    # A key in somebody else's document, mirrored while reading it.
    "option_type",       # a theme manifest's option
    # Hold or filter on a game_type, and renaming either would say it was something else.
    "current_type",
    "filter_by_type",
    # Reads the `type` field of a websocket frame the bridge defines. The variable follows
    # the wire, and the wire is a contract with every theme.
    "msg_type",
    # An extension's own declared settings field, which is persisted under this key.
    "source_type",
    # The key 2.x wrote a reading's kind under in `User.Score`: read by the migration and
    # written again for a contract 1 theme.
    "score_type",
}


def _identifiers(tree: ast.AST) -> set[str]:
    """Every name this module binds: assignments, arguments, keywords, dict keys."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def _offenders() -> list[str]:
    out = []
    for package in PACKAGES:
        for path in sorted((REPO / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = trees.tree_for(path)
            for name in sorted(_identifiers(tree)):
                if TYPE_NAME.match(name) and name not in BORROWED:
                    out.append(f"{path.relative_to(REPO).as_posix()}: {name}")
    return sorted(set(out))


class KindNotTypeTests(unittest.TestCase):
    def test_nothing_new_calls_a_kind_a_type(self) -> None:
        """A new `*_type` is either a kind wearing the wrong word, or a borrowed word
        that belongs in the list above with whose word it is."""
        self.assertEqual(_offenders(), [])

    def test_the_borrowed_list_is_all_still_used(self) -> None:
        """An entry nothing uses is an exemption waiting to cover something else."""
        seen: set[str] = set()
        for package in PACKAGES:
            for path in (REPO / package).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                seen |= _identifiers(trees.tree_for(path))
        self.assertEqual(sorted(BORROWED - seen), [])

    def test_the_checker_can_actually_fail(self) -> None:
        """The pattern, and the exemption, both do what they say."""
        self.assertTrue(TYPE_NAME.match("asset_type"))
        self.assertFalse(TYPE_NAME.match("asset_kind"))
        self.assertFalse(TYPE_NAME.match("typed_value"))
        found = _identifiers(
            trees.parse_snippet("def f(widget_type):\n    return widget_type\n"))
        self.assertIn("widget_type", found)
