"""A log line says where it came from, and costs nothing when nobody is listening.

`docs/conventions.md` asks for two things this checks. A logger is named for where the code
lives, because the name is how "which thing did this?" stays answerable from the Logs page.
And arguments are passed to the logger rather than formatted into it, so the work is skipped
when the level is off.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# Package to the area name that appears in a logger. Extensions log under `ext` because an
# extension never logs into a core namespace - the namespace is how "which extension did
# this?" stays answerable.
AREAS = {
    "apps": "apps",
    "common": "common",
    "manager": "manager",
    "extensions": "ext",
    "frontend": "frontend",
    "httpapi": "httpapi",
}

LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _modules() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for package in AREAS:
        out.extend(
            path for path in sorted(trees.source_files(REPO / package))
            if "__pycache__" not in path.parts
        )
    return out


def _expected_name(path: pathlib.Path) -> str:
    """`vpinfe.<area>.<module path>`, with a package's `__init__` naming the package."""
    parts = path.relative_to(REPO).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["vpinfe", AREAS[parts[0]], *parts[1:]])


def _declared_names(tree: ast.AST) -> list[str]:
    """Every literal name handed to `getLogger`. A computed one is somebody's own scheme."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr == "getLogger"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            found.append(node.args[0].value)
    return found


def _formatted_into_logger(tree: ast.AST) -> list[str]:
    """Calls that build the message before the logger decides whether it is wanted."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr in LOG_METHODS):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.JoinedStr):
            found.append(f"line {node.lineno}: f-string")
        elif isinstance(first, ast.Call) and isinstance(first.func, ast.Attribute) \
                and first.func.attr == "format":
            found.append(f"line {node.lineno}: .format()")
        elif isinstance(first, ast.BinOp) and isinstance(first.op, ast.Mod):
            found.append(f"line {node.lineno}: % formatting")
    return found


class LoggerNameTests(unittest.TestCase):
    def test_every_logger_is_named_for_where_it_lives(self) -> None:
        """A service that moved and kept its old name reports the wrong area forever."""
        wrong = []
        for path in _modules():
            expected = _expected_name(path)
            tree = trees.tree_for(path)
            for name in _declared_names(tree):
                if name != expected:
                    relative = path.relative_to(REPO).as_posix()
                    wrong.append(f"{relative}: {name} (expected {expected})")
        self.assertEqual(wrong, [])

    def test_there_are_loggers_to_check(self) -> None:
        """If it stops finding any, it has stopped checking rather than started passing."""
        total = sum(
            len(_declared_names(trees.tree_for(path)))
            for path in _modules()
        )
        self.assertGreater(total, 100)


class LoggerCallTests(unittest.TestCase):
    def test_nothing_is_formatted_into_a_logger(self) -> None:
        """`logger.debug(f"...")` builds the string whether or not DEBUG is on."""
        offenders: list[str] = []
        for path in _modules():
            tree = trees.tree_for(path)
            offenders.extend(
                f"{path.relative_to(REPO).as_posix()} {where}"
                for where in _formatted_into_logger(tree)
            )
        self.assertEqual(offenders, [])


class CheckerTests(unittest.TestCase):
    def test_the_checker_can_actually_fail(self) -> None:
        """Each half, against source written to break it."""
        with self.subTest("an f-string is caught"):
            found = _formatted_into_logger(trees.parse_snippet('logger.info(f"x {y}")'))
            self.assertEqual(len(found), 1)
        with self.subTest("percent and .format are caught"):
            self.assertEqual(
                len(_formatted_into_logger(trees.parse_snippet('logger.info("x %s" % y)'))), 1)
            formatted = _formatted_into_logger(trees.parse_snippet('logger.info("{}".format(y))'))
            self.assertEqual(len(formatted), 1)
        with self.subTest("passing arguments is what we want, and passes"):
            self.assertEqual(
                _formatted_into_logger(trees.parse_snippet('logger.info("x %s", y)')), [])
        with self.subTest("a comment is not a call"):
            self.assertEqual(
                _formatted_into_logger(trees.parse_snippet('# logger.info(f"x {y}")')), [])
        with self.subTest("the expected name follows the path"):
            self.assertEqual(
                _expected_name(REPO / "common" / "games" / "game_identity.py"),
                "vpinfe.common.games.game_identity",
            )
            self.assertEqual(
                _expected_name(REPO / "extensions" / "vpinplay" / "__init__.py"),
                "vpinfe.ext.vpinplay",
            )
