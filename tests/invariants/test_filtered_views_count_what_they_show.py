"""A grid whose built-in views filter counts the rows the filter leaves.

The count sits above the rows, so a total there reads as the size of what is shown.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parents[2] / "manager"
FOLLOWS = ("getDisplayedRowCount", "forEachNodeAfterFilterAndSort")


def _filtering_views(tree: ast.Module) -> bool:
    """Whether any `Preset(...)` here is given a non-empty `filters`."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", getattr(node.func, "id", "")) == "Preset"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "filters" and not (
                    isinstance(keyword.value, ast.Dict) and not keyword.value.keys):
                return True
    return False


def _selected_counts(tree: ast.Module) -> list[tuple[int, int]]:
    """Each `t("….selected", …)` with its line and how many of its numbers are a `len()`:
    one is the selection's own size, and a second is a whole list."""
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "t"
                and node.args and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).endswith(".selected")):
            lens = sum(1 for keyword in node.keywords
                       if isinstance(keyword.value, ast.Call)
                       and getattr(keyword.value.func, "id", "") == "len")
            found.append((node.lineno, lens))
    return found


class FilteredViewsCountWhatTheyShow(unittest.TestCase):
    def _filtering(self) -> list[pathlib.Path]:
        return [path for path in sorted(MANAGER.rglob("*.py"))
                if _filtering_views(trees.tree_for(path))]

    def test_the_count_follows_the_grid(self) -> None:
        stuck = [path.name for path in self._filtering()
                 if not any(call in path.read_text(encoding="utf-8") for call in FOLLOWS)]
        self.assertEqual([], stuck)

    def test_it_found_the_grids(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        self.assertGreaterEqual(len(self._filtering()), 6)

    def _following(self) -> dict[str, list[tuple[int, int]]]:
        return {path.name: _selected_counts(trees.tree_for(path))
                for path in sorted(MANAGER.rglob("*.py"))
                if any(call in path.read_text(encoding="utf-8") for call in FOLLOWS)}

    def test_a_selection_is_counted_against_the_rows_on_screen(self) -> None:
        whole = [f"{name}:{line}" for name, counts in self._following().items()
                 for line, lens in counts if lens > 1]
        self.assertEqual([], whole)

    def test_it_found_the_selection_counts(self) -> None:
        self.assertGreaterEqual(sum(len(counts) for counts in self._following().values()), 4)


if __name__ == "__main__":
    unittest.main()
