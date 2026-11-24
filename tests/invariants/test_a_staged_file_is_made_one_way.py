"""A file written beside its target and moved over it is staged by `common/atomic_write.py`.

A file staged anywhere else is named when it is refused, and is left behind when the write
fails part way.

Found by how it is made - a temporary file in a named folder - or by a suffix a staged file
has carried. A staged name built by hand under any other suffix is not found.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PACKAGES = ("apps", "common", "manager", "extensions", "frontend", "httpapi")
HELPER = REPO / "common" / "atomic_write.py"
MAKERS = {"mkstemp", "NamedTemporaryFile"}
SUFFIXES = (".part", ".uploading", ".tmp")


def _called(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _offenders(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called(node) in MAKERS and any(
                keyword.arg == "dir" for keyword in node.keywords):
            lines.append(node.lineno)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and node.value.endswith(SUFFIXES)):
            lines.append(node.lineno)
    return lines


class AStagedFileIsMadeOneWayTests(unittest.TestCase):
    def test_nothing_else_stages_a_file(self) -> None:
        found = []
        for package in PACKAGES:
            for path in sorted(trees.source_files(REPO / package)):
                if path != HELPER:
                    found += [f"{path.relative_to(REPO).as_posix()}:{line}"
                              for line in _offenders(trees.tree_for(path))]
        self.assertEqual(found, [], "stage it with common.atomic_write.staged_for")

    def test_the_scan_finds_each_way_a_file_was_staged(self) -> None:
        """Or a scan that matches nothing would pass the test above as well."""
        for staged in ('tempfile.mkstemp(dir=folder, suffix=".x")',
                       "NamedTemporaryFile(dir=folder, delete=False)",
                       'target.with_name(f"{target.name}.part")',
                       'target.with_name(f".{target.name}.uploading")',
                       'mkstemp(prefix=".vpinfe_write_", suffix=".tmp")'):
            with self.subTest(staged=staged):
                self.assertEqual(_offenders(trees.parse_snippet(staged)), [1])

    def test_a_scratch_file_in_the_system_temp_folder_is_not_staged(self) -> None:
        self.assertEqual(
            _offenders(trees.parse_snippet('NamedTemporaryFile(delete=False, suffix=".dif")')),
            [])


if __name__ == "__main__":
    unittest.main()
