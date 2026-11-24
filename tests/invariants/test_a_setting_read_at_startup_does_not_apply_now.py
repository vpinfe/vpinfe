"""A setting read only while VPinFE starts does not say a change applies right away.

`docs/conventions.md`, "A setting says when it takes effect". Saving such a setting writes
the file and nothing happens, so `applies="now"` on it is a setting that looks broken.
The analysis is `tests/support/startup_reads.py`.
"""

from __future__ import annotations

import ast
import functools
import unittest
from collections.abc import Callable, Iterable

from common.config import config_schema
from tests.support import trees
from tests.support.startup_reads import Read, run_once, source_trees

READ = 'cfg_bool(store, "dof", "enabled")'


@functools.cache
def _whole_tree() -> tuple[list[Read], Callable[[Read], bool]]:
    return run_once(source_trees())


def read_only_at_startup(reads: Iterable[Read],
                         at_startup: Callable[[Read], bool]) -> dict[tuple[str, str], list[Read]]:
    """Each declared setting with a read, every one of which runs once."""
    by_setting: dict[tuple[str, str], list[Read]] = {}
    for read in reads:
        resolved = config_schema.locate(read.section, read.key)
        if config_schema.option(*resolved) is not None:
            by_setting.setdefault(resolved, []).append(read)
    return {setting: found for setting, found in by_setting.items()
            if all(at_startup(r) for r in found)}


def claims_now(startup_only: dict[tuple[str, str], list[Read]]) -> list[str]:
    """One line for each setting in `startup_only` that declares it applies right away."""
    claimed = []
    for (section, key), found in sorted(startup_only.items()):
        option = config_schema.option(section, key)
        if option is not None and option.applies == config_schema.APPLIES_NOW:
            where = ", ".join(f"{r.file}:{r.line}" for r in found)
            claimed.append(f"{section}.{key} is read only at startup ({where})")
    return claimed


def _startup_only(*files: str) -> set[tuple[str, str]]:
    """The settings the snippets read only at startup; each snippet is a file of its own."""
    parsed: dict[str, ast.Module] = {
        f"snippet{n}.py": trees.parse_snippet(source, filename=f"snippet{n}.py")
        for n, source in enumerate(files)}
    reads, at_startup = run_once(parsed)
    assert reads, "the snippet reads no setting, so the check has nothing to say"
    return set(read_only_at_startup(reads, at_startup))


DOF = ("dof", "enabled")


class StartupSettingTests(unittest.TestCase):
    def test_no_setting_read_only_at_startup_claims_to_apply_now(self) -> None:
        reads, at_startup = _whole_tree()

        self.assertEqual(claims_now(read_only_at_startup(reads, at_startup)), [],
                         "declare when it takes effect (`applies=` restart, windows or "
                         "table), or read it where a change reaches it")

    def test_there_are_startup_reads_to_find(self) -> None:
        """A scan that decides everything is live passes without checking anything."""
        reads, at_startup = _whole_tree()

        found = read_only_at_startup(reads, at_startup)

        self.assertIn(("device", "language"), found)
        self.assertIn(("updates", "refresh_minutes"), found)

    def test_the_check_names_a_setting_that_claims_now(self) -> None:
        reads, at_startup = run_once({"snippet.py": trees.parse_snippet(
            'cfg_get(store, "dof", "enabled", "")\n'
            'cfg_get(store, "network", "http_port", "")\n')})

        claimed = claims_now(read_only_at_startup(reads, at_startup))

        self.assertEqual(len(claimed), 1)
        self.assertIn("dof.enabled", claimed[0])
        self.assertIn("snippet.py:1", claimed[0])


class WhatRunsOnceTests(unittest.TestCase):
    def test_a_read_at_module_level_runs_once(self) -> None:
        self.assertEqual(_startup_only(READ), {DOF})

    def test_a_store_read_counts_and_a_write_does_not(self) -> None:
        self.assertEqual(_startup_only('config_store.value("dof", "enabled")'), {DOF})
        reads, _ = run_once({"s.py": trees.parse_snippet(
            'config_store.set_value("dof", "enabled", "1")\n'
            'cfg_set(store, "dof", "enabled", "1")\n')})
        self.assertEqual(reads, [])

    def test_a_function_called_only_from_module_level_runs_once(self) -> None:
        self.assertEqual(_startup_only(f'def start():\n    return {READ}\nstart()\n'), {DOF})

    def test_a_call_chain_from_module_level_runs_once(self) -> None:
        self.assertEqual(_startup_only(
            f'def inner():\n    return {READ}\n'
            'def outer():\n    return inner()\n'
            'outer()\n'), {DOF})

    def test_a_function_also_called_from_live_code_is_live(self) -> None:
        self.assertEqual(_startup_only(
            f'def start():\n    return {READ}\n'
            'def on_save():\n    start()\n'
            'start()\n'
            'events.subscribe("saved", on_save)\n'), set())

    def test_a_function_called_from_a_lambda_is_live(self) -> None:
        self.assertEqual(_startup_only(
            f'def start():\n    return {READ}\n'
            'start()\n'
            'events.subscribe("saved", lambda **_: start())\n'), set())

    def test_a_read_inside_a_lambda_is_live(self) -> None:
        self.assertEqual(_startup_only(
            f'events.subscribe("saved", lambda **_: {READ})\n'), set())

    def test_a_decorated_function_is_live(self) -> None:
        for decorator in ('@route("/dof")', '@handler'):
            with self.subTest(decorator):
                self.assertEqual(_startup_only(
                    f'{decorator}\ndef show():\n    return {READ}\nshow()\n'), set())

    def test_a_class_or_static_method_is_called_from_where_a_plain_one_is(self) -> None:
        for decorator in ("classmethod", "staticmethod"):
            with self.subTest(decorator):
                self.assertEqual(_startup_only(
                    f'class A:\n    @{decorator}\n    def go(*_):\n        return {READ}\n'
                    'A.go()\n'), {DOF})

    def test_a_function_handed_over_as_a_value_is_live(self) -> None:
        self.assertEqual(_startup_only(
            f'def poll():\n    return {READ}\npoll()\n'
            'threading.Thread(target=poll).start()\n'), set())

    def test_a_nested_function_is_live(self) -> None:
        self.assertEqual(_startup_only(
            'def start():\n'
            '    def later():\n'
            f'        return {READ}\n'
            '    later()\n'
            'start()\n'), set())

    def test_a_function_nothing_calls_is_not_known_to_run_once(self) -> None:
        self.assertEqual(_startup_only(f'def start():\n    return {READ}\n'), set())

    def test_a_function_defined_twice_is_trusted_in_neither_form(self) -> None:
        self.assertEqual(_startup_only(
            f'@route("/x")\ndef f():\n    return {READ}\n'
            'def f():\n    return 1\n'
            'f()\n'), set())

    def test_a_loop_makes_what_is_inside_it_live(self) -> None:
        for loop in ("while True:\n    ", "for _ in range(9):\n    ",
                     "async for _ in feed:\n    "):
            with self.subTest(loop):
                self.assertEqual(_startup_only(f'{loop}{READ}\n'), set())
        self.assertEqual(_startup_only(
            f'def main():\n    while True:\n        {READ}\nmain()\n'), set())
        self.assertEqual(_startup_only(
            f'def poll():\n    return {READ}\nwhile True:\n    poll()\n'), set())
        self.assertEqual(_startup_only(f'[{READ} for _ in range(3)]\n'), set())

    def test_a_loop_around_a_definition_does_not_reach_into_its_body(self) -> None:
        self.assertEqual(_startup_only(
            f'def start():\n    return {READ}\n'
            'for _ in range(2):\n    pass\n'
            'start()\n'), {DOF})

    def test_a_function_defined_inside_a_loop_is_called_where_it_is_called(self) -> None:
        self.assertEqual(_startup_only(
            f'for _ in range(2):\n    def start():\n        return {READ}\n'
            'start()\n'), {DOF})

    def test_a_call_through_an_import_alias_is_a_call(self) -> None:
        self.assertEqual(_startup_only(
            f'def f():\n    return {READ}\n'
            'f()\n',
            'from snippet0 import f as g\n'
            '@route("/x")\ndef h():\n    g()\n'), set())

    def test_a_constructor_called_only_at_module_level_runs_once(self) -> None:
        self.assertEqual(_startup_only(
            f'class Service:\n    def __init__(self):\n        self.on = {READ}\n'
            'Service()\n'), {DOF})

    def test_a_constructor_called_from_live_code_is_live(self) -> None:
        self.assertEqual(_startup_only(
            f'class Service:\n    def __init__(self):\n        self.on = {READ}\n'
            '@route("/x")\ndef handler():\n    Service()\n'
            'Service()\n'), set())

    def test_a_class_handed_over_as_a_value_is_live(self) -> None:
        self.assertEqual(_startup_only(
            f'class Service:\n    def __init__(self):\n        self.on = {READ}\n'
            'Service()\n'
            'threading.Thread(target=Service).start()\n'), set())

    def test_a_subclass_handed_over_as_a_value_makes_the_inherited_constructor_live(self) -> None:
        self.assertEqual(_startup_only(
            f'class Base:\n    def __init__(self):\n        self.on = {READ}\n'
            'class Child(Base):\n    pass\n'
            'Base()\n'
            'threading.Thread(target=Child).start()\n'), set())

    def test_a_class_built_by_its_own_classmethod_is_live_where_that_is(self) -> None:
        self.assertEqual(_startup_only(
            'class Service:\n'
            f'    def __init__(self):\n        self.on = {READ}\n'
            '    @classmethod\n    def make(cls):\n        return cls()\n'
            'Service()\n'
            '@route("/x")\ndef handler():\n    Service.make()\n'), set())

    def test_a_method_reached_through_a_subclass_is_reached(self) -> None:
        self.assertEqual(_startup_only(
            f'class Base:\n    @classmethod\n    def load(cls):\n        return {READ}\n'
            'class Child(Base):\n    pass\n'
            'Base.load()\n'
            '@route("/x")\ndef h():\n    Child.load()\n'), set())

    def test_an_inherited_constructor_is_reached_through_the_subclass(self) -> None:
        self.assertEqual(_startup_only(
            f'class Base:\n    def __init__(self):\n        self.on = {READ}\n'
            'class Child(Base):\n    pass\n'
            'Base()\n'
            '@route("/x")\ndef h():\n    Child()\n'), set())

    def test_a_call_through_a_class_does_not_reach_another_class_s_method(self) -> None:
        dmd = READ.replace('"dof"', '"real_dmd"')

        self.assertEqual(_startup_only(
            f'class A:\n    def go(self):\n        return {READ}\n'
            f'class B:\n    def go(self):\n        return {dmd}\n'
            'A.go(None)\n'
            '@route("/x")\ndef h():\n    B.go(None)\n'), {DOF})

    def test_a_setting_read_at_startup_and_again_later_is_not_startup_only(self) -> None:
        self.assertEqual(_startup_only(
            f'{READ}\n'
            f'def later():\n    return {READ}\n'
            'events.subscribe("saved", later)\n'), set())


if __name__ == "__main__":
    unittest.main()
