"""Every module defers its annotations, so the code runs on the Python users have.

Python 3.14 evaluates annotations lazily. 3.13 evaluates them where they are written, so a
name that does not exist yet - a class annotating its own method, a type imported only for
checking - raises `NameError` when the module is imported.

That difference cost a non-starting app. `collection_store` annotated `mutate()` with its
own class; it imported fine here and raised on 3.13, and every test and type check passed
because they only ever run on one version. A frozen build bundles its own interpreter and
is safe; a source checkout uses whatever the machine has, and the Debian a cabinet runs
ships 3.13.

`from __future__ import annotations` makes both versions behave the way 3.14 does. This
checks it is there rather than trusting that nobody writes a forward reference, because
the failure is invisible on the machine the tests run on.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PACKAGES = ("apps", "common", "manager", "extensions", "frontend", "httpapi")

# A module with nothing in it cannot annotate anything. Listed rather than inferred so a
# file that grows past that has to be looked at.
NOTHING_TO_DEFER = {
    "common/__init__.py",
    "common/games/__init__.py",
    "common/host/__init__.py",
    "common/online/__init__.py",
    "common/uploads/__init__.py",
    "frontend/__init__.py",
}


def _defers(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.ImportFrom)
               and node.module == "__future__"
               and any(alias.name == "annotations" for alias in node.names)
               for node in tree.body)


def _annotates(tree: ast.Module) -> bool:
    """Whether anything here writes an annotation at all."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            return True
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.returns is not None:
                return True
            args = node.args
            if any(a.annotation is not None
                   for a in args.args + args.kwonlyargs + args.posonlyargs):
                return True
    return False


def _modules() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for package in PACKAGES:
        out.extend(p for p in sorted(trees.source_files(REPO / package))
                   if "__pycache__" not in p.parts)
    return out


class DeferredAnnotationTests(unittest.TestCase):
    def test_every_module_that_annotates_defers_its_annotations(self) -> None:
        """Without it, a forward reference runs here and raises on 3.13."""
        missing = []
        for path in _modules():
            relative = path.relative_to(REPO).as_posix()
            if relative in NOTHING_TO_DEFER:
                continue
            tree = trees.tree_for(path)
            if _annotates(tree) and not _defers(tree):
                missing.append(relative)
        self.assertEqual(sorted(missing), [], "\n".join([
            "These annotate and do not defer, so a forward reference in them raises",
            "on 3.13 while passing here. Add `from __future__ import annotations`.",
            *sorted(missing)]))

    def test_the_exempt_list_only_names_files_that_still_have_nothing(self) -> None:
        """A file that grows an annotation stops being exempt."""
        wrong = []
        for relative in sorted(NOTHING_TO_DEFER):
            path = REPO / relative
            if not path.exists():
                wrong.append(f"{relative}: gone")
                continue
            if _annotates(trees.tree_for(path)):
                wrong.append(f"{relative}: annotates something now")
        self.assertEqual(wrong, [])

    def test_the_checker_can_actually_fail(self) -> None:
        """The two halves it decides on."""
        annotating = trees.parse_snippet("def f(x: int) -> str:\n    return str(x)\n")
        self.assertTrue(_annotates(annotating))
        self.assertFalse(_defers(annotating))
        bare = trees.parse_snippet("VERSION = '1.0'\n")
        self.assertFalse(_annotates(bare))
        deferred = trees.parse_snippet(
            "from __future__ import annotations\ndef f(x: int) -> str: ...\n")
        self.assertTrue(_defers(deferred))
