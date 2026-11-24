"""What we serve over HTTP is snake_case, including the aliases.

`docs/conventions.md` settles the choice: snake_case matches Python, so nothing is
translated at the boundary, and it matches the JSON the app already returned. The models are
clean today and nothing was holding them there - a single `camelCase` field would be a
contract a client has to special-case forever, because a name on the wire cannot be taken
back without breaking whoever read it.

`populate_by_name` means a field can carry an alias, and the alias is what a client
actually sees, so both spellings are checked.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MODELS = REPO / "httpapi" / "models.py"

SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

# Names Python will not take as identifiers, so they exist only as aliases. `self` is the
# one the links objects force - it is a link relation named by the HTTP spec, not by us.
ALLOWED_ALIASES = {"self"}


def _alias_of(node: ast.AnnAssign) -> str | None:
    """The `alias=` a field declares, if it declares one."""
    if not isinstance(node.value, ast.Call):
        return None
    for keyword in node.value.keywords:
        if keyword.arg == "alias" and isinstance(keyword.value, ast.Constant):
            assert isinstance(keyword.value.value, str)
            return keyword.value.value
    return None


def _offenders() -> list[str]:
    out = []
    tree = trees.tree_for(MODELS)
    for klass in tree.body:
        if not isinstance(klass, ast.ClassDef):
            continue
        for node in klass.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            field = node.target.id
            if not SNAKE.match(field):
                out.append(f"{klass.name}.{field}")
            alias = _alias_of(node)
            if alias and not SNAKE.match(alias) and alias not in ALLOWED_ALIASES:
                out.append(f"{klass.name}.{field} is served as {alias!r}")
    return out


class WireNamingTests(unittest.TestCase):
    def test_every_served_field_is_snake_case(self) -> None:
        """A camelCase name on the wire is a special case for every client, forever."""
        self.assertEqual(_offenders(), [])

    def test_there_are_models_to_check(self) -> None:
        """The check reads one file; if it stops finding fields it has stopped working."""
        tree = trees.tree_for(MODELS)
        fields = [
            node for klass in tree.body if isinstance(klass, ast.ClassDef)
            for node in klass.body if isinstance(node, ast.AnnAssign)
        ]
        self.assertGreater(len(fields), 100)

    def test_the_checker_can_actually_fail(self) -> None:
        """Both halves: the field name, and the alias a client actually reads."""
        self.assertFalse(SNAKE.match("gameDirName"))
        self.assertTrue(SNAKE.match("game_dir_name"))
        declared = trees.parse_snippet('x: str = Field(alias="camelCase")').body[0]
        assert isinstance(declared, ast.AnnAssign)
        self.assertEqual(_alias_of(declared), "camelCase")
