"""A name handed to a sentence goes in as the catalog spells it."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("apps", "common", "manager", "extensions", "frontend", "httpapi")
RECASED = {"lower", "upper", "title", "capitalize", "casefold", "swapcase"}


def _calls() -> list[tuple[str, ast.Call]]:
    """(where, call) for every `t()` given a parameter."""
    found = []
    for top in SOURCES:
        for path in sorted(trees.source_files(ROOT / top)):
            for node in ast.walk(trees.tree_for(path)):
                if isinstance(node, ast.Call) and node.keywords and "t" in (
                        getattr(node.func, "id", None), getattr(node.func, "attr", None)):
                    found.append((f"{path.relative_to(ROOT).as_posix()}:{node.lineno}", node))
    return found


class ANameInASentence(unittest.TestCase):
    def test_the_calls_are_found(self) -> None:
        self.assertGreater(len(_calls()), 300)

    def test_no_parameter_is_recased(self) -> None:
        recased = [f"{where} {keyword.arg}={ast.unparse(keyword.value)}"
                   for where, call in _calls() for keyword in call.keywords
                   for sub in ast.walk(keyword.value)
                   if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                   and sub.func.attr in RECASED]
        self.assertEqual([], recased, "pass the name as it is; reword the sentence if it "
                                      "reads wrong capitalized")


if __name__ == "__main__":
    unittest.main()
