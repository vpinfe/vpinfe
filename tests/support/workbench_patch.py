"""Patching a name in the workbench the way a test of the single module did.

Each module of `manager.workbench` binds the names it imports from its siblings, so a
patch on the module that defines a name is not seen by the ones that imported it.
`patch_workbench` patches the name in every module that holds it.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from contextlib import ExitStack
from typing import Any
from unittest.mock import DEFAULT, patch

import manager.workbench


def _modules() -> list[Any]:
    for found in pkgutil.iter_modules(manager.workbench.__path__):
        importlib.import_module(f"manager.workbench.{found.name}")
    return [
        module
        for key, module in sorted(sys.modules.items())
        if key.startswith("manager.workbench.") and module is not None
    ]


class patch_workbench:  # noqa: N801 - used like `patch.object`
    """A patch of `name` in every workbench module that holds it. Enter it with `with`, or
    call `start` and `stop` as a `patch` does; the replacement is what `start` returns."""

    def __init__(self, name: str, new: Any = DEFAULT, **kwargs: Any) -> None:
        self._name = name
        self._new = new
        self._kwargs = kwargs
        self._stack: ExitStack | None = None

    def start(self) -> Any:
        holders = [module for module in _modules() if self._name in vars(module)]
        if not holders:
            raise AttributeError(f"no module of manager.workbench holds {self._name!r}")
        stack = ExitStack()
        try:
            replacement = stack.enter_context(
                patch.object(holders[0], self._name, self._new, **self._kwargs))
            for module in holders[1:]:
                stack.enter_context(patch.object(module, self._name, new=replacement))
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        return replacement

    def stop(self) -> None:
        if self._stack is not None:
            stack, self._stack = self._stack, None
            stack.close()

    def __enter__(self) -> Any:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
