"""A stepped flow that can go back lives in one place.

`manager/kit/wizard.py` is the one control for asking something in stages with a way back
(its `Walk`). A file that draws both the forward verb and the back verb is drawing that
shape again beside it.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"
OWN = MANAGER / "kit" / "wizard.py"

NEXT_WORD = "word.next"
BACK_WORD = "word.back"


def _mentions(tree: ast.Module, word: str, verb: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == word:
            return True
        if isinstance(node, ast.Attribute) and node.attr == verb \
                and isinstance(node.value, ast.Name) and node.value.id == "verbs":
            return True
    return False


def _goes_forward_and_back(tree: ast.Module) -> bool:
    return _mentions(tree, NEXT_WORD, "NEXT") and _mentions(tree, BACK_WORD, "BACK")


class OnlyWizardStepsAndGoesBackTests(unittest.TestCase):
    def test_no_other_file_draws_both_next_and_back(self) -> None:
        offenders = [path.relative_to(REPO) for path in sorted(MANAGER.rglob("*.py"))
                    if path != OWN and _goes_forward_and_back(trees.tree_for(path))]

        self.assertEqual(offenders, [],
                         "a stepped, going-back flow outside manager/kit/wizard.py - move it "
                         "onto the shared control instead of drawing Back beside Next again")

    def test_the_check_flags_a_file_with_both_and_not_one_alone(self) -> None:
        both = trees.parse_snippet(
            'frame.quiet(t("word.back"), on, icon=verbs.BACK)\n'
            'frame.answer(t("word.next"), on, icon=verbs.NEXT)\n')
        next_only = trees.parse_snippet('frame.answer(t("word.next"), on, icon=verbs.NEXT)\n')
        back_only = trees.parse_snippet('frame.quiet(t("word.back"), on, icon=verbs.BACK)\n')

        self.assertTrue(_goes_forward_and_back(both))
        self.assertFalse(_goes_forward_and_back(next_only))
        self.assertFalse(_goes_forward_and_back(back_only))


if __name__ == "__main__":
    unittest.main()
