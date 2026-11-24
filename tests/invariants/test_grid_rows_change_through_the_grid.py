"""A Manager grid's rows change through `grid.transact`, in the list the grid was built
from, and nowhere else.

A selection is resolved against the rows the grid was built from. A transaction sent
past `transact`, or applied to some other list, changes the screen and leaves those rows
as they were, so a bulk action acts on a row as it was before the edit.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parents[2]
MANAGER = REPO / "manager"
HELPER = MANAGER / "kit" / "grid.py"
CALL = "applyTransaction"

# Each takes the held rows at this position.
HELD_AT = {("grid", "transact"): 1, ("grid", "replace_rows"): 1,
           ("stars", "rating_handler"): 0, ("stars", "game_rater"): 0}


def _sends(path: pathlib.Path) -> list[str]:
    return [f"{path.relative_to(REPO).as_posix()}:{number}"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if CALL in line]


def _called(node: ast.AST) -> tuple[str, str] | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)):
        return node.func.value.id, node.func.attr
    return None


def _strays(path: pathlib.Path) -> list[str]:
    """Held rows changed in a list other than the one the page's grid was built from."""
    found = []
    tree = trees.tree_for(path)
    for page in tree.body:
        if not isinstance(page, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [node for node in ast.walk(page)
                 if isinstance(node, ast.Call) and _called(node)]
        # Handed in, so it is checked where this is called from.
        handed = {one.arg for one in page.args.args}
        built: dict[str, int] = {}
        for call in calls:
            if _called(call) == ("grid", "build") and len(call.args) > 1:
                rows = call.args[1]
                if isinstance(rows, ast.Name):
                    built[rows.id] = call.lineno
                else:
                    found.append(f"{path.name}:{call.lineno} builds from an unnamed list")
        for call in calls:
            at = HELD_AT.get(_called(call) or ("", ""))
            if at is None or len(call.args) <= at:
                continue
            held = call.args[at]
            name = held.id if isinstance(held, ast.Name) else ""
            if name not in built and not (name in handed and not built):
                found.append(f"{path.name}:{call.lineno} changes rows its grid does not hold")
        for node in ast.walk(page):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign))
                       else [])
            for target in targets:
                assert isinstance(node, ast.stmt)
                if isinstance(target, ast.Name) and node.lineno > built.get(target.id, 1e9):
                    found.append(f"{path.name}:{node.lineno} rebinds {target.id}, which a "
                                 "grid was built from")
    return found


class GridRowsChangeThroughTheGridTests(unittest.TestCase):
    def test_no_other_module_sends_a_transaction(self) -> None:
        offenders = []
        for path in sorted([*MANAGER.rglob("*.py"), *MANAGER.rglob("*.js")]):
            if path != HELPER:
                offenders += _sends(path)
        self.assertEqual(offenders, [], "change a grid's rows with grid.transact")

    def test_the_scan_finds_the_helper_s_own(self) -> None:
        """Or a scan that matches nothing would pass the test above as well."""
        self.assertEqual(len(_sends(HELPER)), 1)

    def test_a_grid_s_rows_change_in_the_list_it_was_built_from(self) -> None:
        offenders = []
        for path in sorted(MANAGER.rglob("*.py")):
            offenders += _strays(path)
        self.assertEqual(offenders, [])

    def test_the_held_list_scan_sees_every_page_that_changes_rows(self) -> None:
        """Every module that changes a grid's rows, so a scan reading nothing fails."""
        changing = set()
        for path in MANAGER.rglob("*.py"):
            tree = trees.tree_for(path)
            if any(_called(node) in HELD_AT for node in ast.walk(tree)):
                changing.add(path.name)
        self.assertGreaterEqual(changing, {"games.py", "collections.py", "devices.py",
                                           "media.py"})


if __name__ == "__main__":
    unittest.main()
