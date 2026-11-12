"""A token in a command a person writes has one syntax, and one module that reads it.

`docs/conventions.md`, "A token in a command a person writes is `{name}`".
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.support import trees

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("apps", "common", "manager", "extensions", "frontend", "httpapi")
HOME = REPO / "common" / "tokens.py"

# A regex that captures a word between braces or brackets: a token parser.
_PARSER = re.compile(r"\\[{\[]\(\??:?\[?(?:A-Za-z|a-z|\\w)")
# A literal token being swapped in by hand.
_LITERAL = re.compile(r"\A(?:\{[A-Za-z_]\w*\}|\[[A-Za-z_]\w*\])\Z")
_RE_CALLS = {"compile", "sub", "subn", "findall", "finditer", "fullmatch", "match", "search"}

# Braces that are not a command's tokens, each with what they are.
ALLOWED: dict[tuple[str, int], str] = {
    ("manager/kit/grid.py", 839): "a JavaScript renderer's source, filled in by code",
}


def _sources() -> list[Path]:
    return [path for root in ROOTS for path in sorted((REPO / root).rglob("*.py"))
            if path != HOME and "__pycache__" not in path.parts]


def _found(path: Path) -> list[tuple[str, int, str]]:
    out = []
    for node in ast.walk(trees.tree_for(path)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        first = node.args[0] if node.args else None
        text = first.value if isinstance(first, ast.Constant) else None
        if not isinstance(text, str):
            continue
        name = node.func.attr
        if name in _RE_CALLS and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "re" and _PARSER.search(text):
            out.append((path.relative_to(REPO).as_posix(), node.lineno, text))
        elif name == "replace" and _LITERAL.match(text):
            out.append((path.relative_to(REPO).as_posix(), node.lineno, text))
    return out


class OneTokenSyntax(unittest.TestCase):
    def test_nothing_but_common_tokens_parses_or_swaps_a_token(self) -> None:
        found = [one for path in _sources() for one in _found(path)
                 if (one[0], one[1]) not in ALLOWED]

        self.assertEqual(found, [], "read and fill tokens through common/tokens.py")

    def test_the_allowed_ones_still_exist(self) -> None:
        seen = {(one[0], one[1]) for path in _sources() for one in _found(path)}

        self.assertEqual(sorted(set(ALLOWED) - seen), [])


if __name__ == "__main__":
    unittest.main()
