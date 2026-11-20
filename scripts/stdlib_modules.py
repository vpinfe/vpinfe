"""The standard library modules a build names, less what no extension can use on a cabinet."""

from __future__ import annotations

import importlib.util
import pkgutil
import sys
from collections.abc import Iterator

# Top-level modules left out: a toolkit needing a display server's widgets, the turtle
# demos, the editor, the test suites and the installer for pip.
LEFT_OUT = frozenset({
    "tkinter", "_tkinter", "turtle", "turtledemo", "idlelib", "idle", "test",
    "ensurepip", "lib2to3",
})

# A part of a package left out wherever it sits: the package's own tests, and the `python -m`
# entry points, which nothing imports.
TEST_PARTS = frozenset({"test", "tests", "__main__"})


def _left_out(name: str) -> bool:
    parts = name.split(".")
    return (parts[0] in LEFT_OUT or parts[0].startswith("_test")
            or any(part in TEST_PARTS for part in parts[1:]))


def _members(package: str, locations: list[str]) -> Iterator[str]:
    for found in pkgutil.iter_modules(locations, prefix=f"{package}."):
        if _left_out(found.name):
            continue
        yield found.name
        if found.ispkg:
            spec = importlib.util.find_spec(found.name)
            if spec is not None and spec.submodule_search_locations:
                yield from _members(found.name, list(spec.submodule_search_locations))


def names() -> list[str]:
    """Every standard library module this interpreter has, packages with their parts, by
    name. A module another platform has and this one does not is not in it."""
    found: set[str] = set()
    for name in sys.stdlib_module_names:
        if _left_out(name):
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue
        found.add(name)
        if spec.submodule_search_locations:
            found.update(_members(name, list(spec.submodule_search_locations)))
    return sorted(found)
