"""A failure's message says what could not be done; the exception goes under it."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any, TypeGuard

from tests.support import trees
from tests.support.catalogs import served

ROOT = Path(__file__).resolve().parents[2]
CODE = ("apps", "common", "manager", "extensions", "frontend", "httpapi", "cli.py", "main.py")

LOOKUPS = {"t", "t_source"}
# Calls that draw their first argument as the message itself.
MESSAGES = {"notify", "notification", "label", "intro", "note", "lede", "markdown"}
# Where the detail goes, which has to have been through why() or a device's own wording.
DETAIL = {"caption", "hint"}
# What an exception holds as its own text, rather than as data it carries.
TEXT = {"args", "msg", "message", "strerror", "reason"}

SLOT = re.compile(r"\{exc(?:[.!:\[][^}]*)?\}")
REPR = re.compile(r"\{[^{}]*![rsa]\}")

LOGGED = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
# Functions that hand on a caught exception's own text as it is, and who reads it there.
HANDED_ON = {
    "apps/vpx/controllers.py": {
        "_run": "the controllers page's status line; an app may not import common.failures"},
    "common/extensions/check.py": {
        "_read": "the report `--check-extension` prints for a developer"},
    "common/extensions/context.py": {
        "contribute": "an extension's author, as the extension registers",
        "offer": "an extension's author, as the extension registers",
        "settings": "an extension's author, as the extension registers"},
    "common/extensions/host.py": {
        "_register": "the developer's `--check-extension` report, which prints what "
                     "register raised"},
    "common/extensions/serving.py": {
        "_titled": "an extension's author: the field a dialog answered with is not drawn"},
    "common/games/revert_3x.py": {"_keep_aside": "a command-line tool",
                                  "_remove": "a command-line tool"},
    "common/host/commands.py": {
        "planned": "the log: a launch refused says so again in the catalog's words",
        "run": "the log: a launch refused says so again in the catalog's words",
        "_one": "the log: a launch refused says so again in the catalog's words"},
    "common/host/dof_service_worker.py": {"handle": "the DOF service, which logs it",
                                          "main": "the DOF service, which logs it"},
    "common/host/graphics/__init__.py": {
        "_listed": "an UnreadableError, whose reason is already the catalog's words",
        "_overlay_nvidia_smi": "an UnreadableError, whose reason is already the catalog's words"},
    "common/host/graphics/linux.py": {
        "_read_others": "an UnreadableError, whose reason is already the catalog's words"},
    "common/host/pinmame_worker.py": {"main": "the PinMAME lookup, which logs it",
                                      "serve": "the PinMAME lookup, which logs it"},
}


def _named(node: ast.expr) -> str:
    return getattr(node, "id", None) or getattr(node, "attr", None) or ""


def _as_text(value: ast.expr, name: str) -> list[ast.Name]:
    """Each use of `name` in `value` that hands on the exception or its words."""
    data = {id(node.value) for node in ast.walk(value)
            if isinstance(node, ast.Attribute) and node.attr not in TEXT}
    return [one for one in ast.walk(value)
            if isinstance(one, ast.Name) and one.id == name and id(one) not in data]


def _given(call: ast.Call) -> list[ast.expr]:
    kind = _named(call.func)
    if kind in LOOKUPS:
        return [*call.args[1:], *(kw.value for kw in call.keywords)]
    if kind in MESSAGES:
        return [*call.args[:1],
                *(kw.value for kw in call.keywords if kw.arg in ("message", "text"))]
    return []


def _detail(call: ast.Call) -> list[ast.expr]:
    return [*(call.args[:1] if _named(call.func) == "tooltip" else []),
            *(kw.value for kw in call.keywords if kw.arg in DETAIL)]


def _unworded(value: ast.expr, name: str) -> list[ast.Name]:
    worded = {id(one) for node in ast.walk(value)
              if isinstance(node, ast.Call) and "why" in _named(node.func)
              for one in ast.walk(node)}
    return [one for one in _as_text(value, name) if id(one) not in worded]


def offenders(tree: ast.Module) -> list[tuple[int, str]]:
    """Each place an exception reaches a message: an `exc=` handed to `t()`, or a caught
    exception's words handed to a lookup or drawn as the message."""
    found: list[tuple[int, str]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _named(node.func) in LOOKUPS:
            for kw in (kw for kw in node.keywords if kw.arg == "exc"):
                found.append((kw.value.lineno, f"exc={ast.unparse(kw.value)}"))
                seen |= {id(one) for one in ast.walk(kw.value)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        # Innermost first, so a `t()` inside `ui.notify()` is named rather than the notify.
        calls = [one for line in node.body for one in ast.walk(line)
                 if isinstance(one, ast.Call)]
        for call in reversed(calls):
            said = [(value, _as_text(value, node.name)) for value in _given(call)]
            said += [(value, _unworded(value, node.name)) for value in _detail(call)]
            for value, names in said:
                for one in names:
                    if id(one) not in seen:
                        seen.add(id(one))
                        found.append((one.lineno, ast.unparse(value)))
    return sorted(found)


def _its_text(node: ast.AST, name: str) -> TypeGuard[ast.expr]:
    """`str(exc)`, `{exc}` in an f-string, `exc.msg`: the exception's own words."""
    def caught(value: ast.AST) -> bool:
        return isinstance(value, ast.Name) and value.id == name

    if isinstance(node, ast.Call) and _named(node.func) in ("str", "repr", "format"):
        return bool(node.args) and caught(node.args[0])
    if isinstance(node, ast.FormattedValue):
        return caught(node.value) or _its_text(node.value, name)
    return isinstance(node, ast.Attribute) and node.attr in TEXT and caught(node.value)


def _not_handed_on(chain: list[ast.AST]) -> bool:
    """Written to the log, worded by why(), a test, or data under `details=`."""
    for child, parent in zip(chain, chain[1:], strict=False):
        if isinstance(parent, ast.Call) and ("why" in _named(parent.func) or (
                isinstance(parent.func, ast.Attribute) and parent.func.attr in LOGGED)):
            return True
        if isinstance(parent, ast.keyword) and parent.arg == "details":
            return True
        if isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert)) \
                and child is parent.test:
            return True
    return False


def handed_on(tree: ast.Module) -> list[tuple[int, str, str]]:
    """(line, function, text) for each caught exception's own text a handler hands on."""
    parent: dict[int, ast.AST] = {id(child): node for node in ast.walk(tree)
                                  for child in ast.iter_child_nodes(node)}
    within: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            within.update({id(node): function.name for node in ast.walk(function)
                           if isinstance(node, ast.ExceptHandler)})
    found: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for node in (one for line in handler.body for one in ast.walk(line)):
            if id(node) in seen or not _its_text(node, handler.name):
                continue
            chain: list[ast.AST] = [node]
            while chain[-1] is not handler:
                chain.append(parent[id(chain[-1])])
            seen |= {id(one) for one in ast.walk(node)}
            if not _not_handed_on(chain):
                shown = chain[1] if isinstance(node, ast.FormattedValue) else node
                found.append((node.lineno, within.get(id(handler), ""),
                              ast.unparse(shown)))
    return sorted(found)


def _sources() -> list[Path]:
    paths: list[Path] = []
    for name in CODE:
        path = ROOT / name
        paths += [path] if path.is_file() else sorted(trees.source_files(path))
    return [path for path in paths if "__pycache__" not in path.parts]


def _slots(value: Any) -> bool:
    forms = value.values() if isinstance(value, dict) else [value]
    return any(SLOT.search(str(form)) for form in forms)


class TheExceptionGoesUnderTheWords(unittest.TestCase):

    def test_no_message_carries_an_exception(self) -> None:
        found = [f"{path.relative_to(ROOT).as_posix()}:{line} {said}"
                 for path in _sources()
                 for line, said in offenders(trees.tree_for(path))]
        self.assertEqual(found, [], "the lead-in is the message; why() goes in the caption")

    def test_no_entry_has_a_slot_for_one(self) -> None:
        self.assertEqual(sorted(key for key, value in served().items() if _slots(value)), [])

    def test_no_entry_quotes_a_value_the_python_way(self) -> None:
        self.assertEqual(sorted(key for key, value in served().items()
                                if any(REPR.search(str(form)) for form in
                                       (value.values() if isinstance(value, dict) else [value]))),
                         [], "“{value}” reads as a name; {value!r} reads as code")

    def test_each_way_is_read(self) -> None:
        source = ("def save(client):\n"
                  "    try:\n"
                  "        client.save()\n"
                  "    except KeyError as exc:\n"
                  "        ui.notify(t('said.save_failed', exc=(exc)))\n"
                  "    except ValueError as exc:\n"
                  "        ui.notify(str(exc))\n"
                  "    except OSError as exc:\n"
                  "        ui.notify(t('x.not_json', msg=exc.msg))\n"
                  "        ui.notify(t('x.refused', keys=', '.join(exc.keys)))\n"
                  "        panel.intro(t('x.could_not', reason=_why(exc)))\n"
                  "        ui.notify(t('x.could_not'), caption=why(exc))\n"
                  "        panel.intro(t('x.could_not'), hint=why(exc))\n"
                  "        ui.label(t('x.could_not')).tooltip(_why(exc))\n"
                  "        ui.notify(t('x.could_not'), caption=str(exc))\n"
                  "        ui.label(t('x.could_not')).tooltip(exc.args[0])\n"
                  "    except Exception as error:\n"
                  "        row['error'] = ctx.t('x.game_file', error=f'{error}')\n"
                  "    return lambda exc: t('x.could_not', exc=exc)\n")

        said = [f"{line} {text}" for line, text in offenders(trees.parse_snippet(source))]

        self.assertEqual(said, ["5 exc=exc", "7 str(exc)", "9 exc.msg",
                                "11 _why(exc)", "15 str(exc)", "16 exc.args[0]",
                                "18 f'{error}'", "19 exc=exc"])

    def test_a_slot_is_found_however_it_is_written(self) -> None:
        self.assertEqual([_slots(one) for one in
                          ("Could not: {exc}", "{exc!r}", {"one": "{exc.args}"},
                           "{excuse}", "{count}")],
                         [True, True, True, False, False])


class ACaughtExceptionIsSaidThroughWhy(unittest.TestCase):

    def test_nothing_hands_on_its_text_as_it_is(self) -> None:
        found = []
        for path in _sources():
            name = path.relative_to(ROOT).as_posix()
            excused = HANDED_ON.get(name, {})
            found += [f"{name}:{line} {said}"
                      for line, function, said in handed_on(trees.tree_for(path))
                      if function not in excused]
        self.assertEqual(found, [], "why(exc) says it in words where it can")

    def test_each_way_is_read(self) -> None:
        source = ("def plan(said, url):\n"
                  "    try:\n"
                  "        go()\n"
                  "    except ValueError as exc:\n"
                  "        raise Refused(str(exc)) from exc\n"
                  "    except KeyError as exc:\n"
                  "        raise Refused(f'{said}: {exc}') from exc\n"
                  "    except OSError as exc:\n"
                  "        return Result(said, False, exc.strerror or why(exc))\n"
                  "    except TimeoutError as exc:\n"
                  "        logger.warning('Could not: %s', str(exc))\n"
                  "        if 'busy' in str(exc) and exc.args:\n"
                  "            raise Refused(why(exc, url), details={'path': str(exc)})\n"
                  "        raise Refused(f'line {exc.lineno}: {exc.msg}') from exc\n")

        said = [f"{line} {function} {text}"
               for line, function, text in handed_on(trees.parse_snippet(source))]

        self.assertEqual(said, ["5 plan str(exc)", "7 plan f'{said}: {exc}'",
                                "9 plan exc.strerror", "14 plan f'line {exc.lineno}: {exc.msg}'"])

    def test_each_function_excused_is_there_with_its_reason(self) -> None:
        for name, excused in HANDED_ON.items():
            with self.subTest(name):
                tree = trees.tree_for(ROOT / name)
                defined = {node.name for node in ast.walk(tree)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                self.assertEqual(excused.keys() - defined, set())
                self.assertTrue(all(reason.strip() for reason in excused.values()))


if __name__ == "__main__":
    unittest.main()
