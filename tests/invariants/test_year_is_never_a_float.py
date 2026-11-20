"""No year this project hands out is a float.

Nothing is allowed to make one a float. Whether a
given year is a number or text is a per-surface answer and not settled here - the library
hands out text, a catalog's own year comes back as a number - but a float is neither of
those and is nobody's answer.

It arrives from a coercion rather than from data: a year is parsed to compare or sort it,
`float()` is the coercion that never raises, and the value goes on to be serialized. That
happened once already, in the connector that fetches a rating, where JavaScript's single
number type had hidden it.
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parents[2]
SCANNED = ("common", "frontend", "httpapi", "manager", "extensions")
SKIP = {"__pycache__", ".venv", "third_party"}


def _sources():
    for area in SCANNED:
        for path in sorted(trees.source_files(REPO / area)):
            if not any(part in SKIP for part in path.parts):
                yield path


def _float_calls_near_year(tree: ast.AST) -> list[str]:
    """`float(...)` whose argument names a year.

    Named rather than inferred: a float built from something called `year` is the shape
    this exists to catch, and nothing else in the tree needs to be guessed at.
    """
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "float" and node.args):
            continue
        text = ast.dump(node.args[0]).lower()
        if "year" in text:
            found.append(f"line {node.lineno}")
    return found


class SourceTests(unittest.TestCase):
    def test_nothing_coerces_a_year_with_float(self) -> None:
        offenders = []
        for path in _sources():
            tree = trees.tree_for(path)
            for where in _float_calls_near_year(tree):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{where}")

        self.assertEqual(offenders, [], "a year coerced with float():\n  "
                                        + "\n  ".join(offenders))


class PayloadTests(unittest.TestCase):
    """The values themselves, not the code that builds them."""

    def _years(self, payload, path=""):
        """Every value under a key called year, however deep."""
        if isinstance(payload, dict):
            for key, value in payload.items():
                if str(key).lower() == "year":
                    yield f"{path}.{key}", value
                yield from self._years(value, f"{path}.{key}")
        elif isinstance(payload, list):
            for index, value in enumerate(payload):
                yield from self._years(value, f"{path}[{index}]")

    def test_no_year_in_the_theme_payload_is_a_float(self) -> None:
        """Both contracts, from the committed capture of what a theme is handed."""
        payload = json.loads(
            (REPO / "tests" / "fixtures" / "theme_payload.json")
            .read_text(encoding="utf-8"))

        floats = [where for where, value in self._years(payload)
                  if isinstance(value, float)]

        self.assertEqual(floats, [])

    def test_a_contributed_year_survives_every_shape_it_arrives_in(self) -> None:
        """The connector is where this went wrong, so it is asked directly."""
        from common.extensions import host

        loaded = host.Registry()
        self.addCleanup(loaded.clear)
        loaded.load(host.BUNDLED_DIR / "vpinplay")
        from vpinfe_ext_vpinplay import client

        raws: tuple[object, ...] = (1995, 1995.0, "1995", "1995.0", "", None, "unknown", True, [])
        for raw in raws:
            with self.subTest(raw=raw):
                found = client.normalize("v", {"vpsdb": {"year": raw}})

                self.assertNotIsInstance(found["vpsdb"]["year"], float)


if __name__ == "__main__":
    unittest.main()
