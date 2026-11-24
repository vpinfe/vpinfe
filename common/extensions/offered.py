"""What core lets an extension call by name, narrowed by what the manifest declared.

Core fills a registry in with its own functions, `name -> (scope, callable)`, and an
extension reaches them as `ctx.<namespace>.<name>(...)`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .contract import ContractError

Registry = dict[str, tuple[str, Callable[..., Any]]]


class Offered:
    """A namespace on `ctx` whose members are looked up in a registry core filled in."""

    def __init__(self, name: str, scopes: Iterable[str], registry: Registry) -> None:
        self._name = name
        self._scopes = frozenset(scopes)
        self._registry = registry

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Anything core offered that this does not wrap itself."""
        if name.startswith("_") or name not in self._registry:
            raise AttributeError(
                f"core offers nothing called {name!r} to an extension"
                + (f"; it offers {', '.join(sorted(self._registry))}"
                   if self._registry else ""))
        scope, run = self._registry[name]
        self._needs(scope)

        def call(*args: Any, **kwargs: Any) -> Any:
            return run(*args, **kwargs)

        call.__name__ = name
        return call

    def reaches(self) -> tuple[str, ...]:
        """What this extension may actually call, which is what core offers narrowed to
        what its manifest declared."""
        return tuple(name for name, (scope, _run) in sorted(self._registry.items())
                     if scope in self._scopes)

    def _needs(self, scope: str) -> None:
        if scope not in self._scopes:
            raise ContractError(
                f"{self._name} asks core to do something needing {scope}, which its "
                "manifest does not declare")
