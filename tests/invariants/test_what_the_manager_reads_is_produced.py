"""Every field the Manager reads off a table is one something produces.

A read of a key nothing writes is not an error: `.get()` answers None, the `or 0` after
it answers zero, and the row drawn from it is a constant that looks like a finding.

The reads checked are `table.get("x")` and `table["x"]` in `manager/`. A key passes when
an API model declares it, or when the Manager builds it into a dict itself, which is
where a grid row's own fields come from.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import unittest

from pydantic import BaseModel

from httpapi import models
from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
NAME = "table"


def _declared() -> set[str]:
    return {field for one in vars(models).values()
            if inspect.isclass(one) and issubclass(one, BaseModel)
            and one.__module__ == models.__name__
            for field in one.model_fields}


def _key(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) \
        else None


def _manager() -> tuple[dict[str, list[str]], set[str]]:
    """Each key read off a table, with where, and every key the Manager writes."""
    read: dict[str, list[str]] = {}
    written: set[str] = set()
    for path in sorted((REPO / "manager").rglob("*.py")):
        where = path.relative_to(REPO)
        for node in ast.walk(trees.tree_for(path)):
            if isinstance(node, ast.Dict):
                written |= {key for one in node.keys if one is not None
                            and (key := _key(one)) is not None}
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and node.args \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == NAME \
                    and (key := _key(node.args[0])) is not None:
                read.setdefault(key, []).append(f"{where}:{node.lineno}")
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                    and node.value.id == NAME and (key := _key(node.slice)) is not None:
                if isinstance(node.ctx, ast.Store):
                    written.add(key)
                else:
                    read.setdefault(key, []).append(f"{where}:{node.lineno}")
    return read, written


class WhatTheManagerReadsIsProducedTests(unittest.TestCase):
    def test_every_table_field_read_is_declared_or_built_here(self) -> None:
        read, written = _manager()
        produced = _declared() | written
        unproduced = {key: sites for key, sites in read.items() if key not in produced}

        self.assertEqual(unproduced, {})

    def test_it_finds_the_reads(self) -> None:
        """A walk that finds nothing passes whatever the Manager reads."""
        read, _written = _manager()

        self.assertIn("launcher_settings_here", read)


if __name__ == "__main__":
    unittest.main()
