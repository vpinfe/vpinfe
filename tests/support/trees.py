"""One parsed tree per source file, shared by every checker that reads it.

A checker asks `tree_for` for a path's tree instead of parsing it directly. A checker's
own literal snippet - never a real file on disk - goes through `parse_snippet` instead,
and is not cached. A tree handed out is never mutated; a checker that needs a changed
tree builds its own copy.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Literal, overload

_cache: dict[Path, ast.Module] = {}


def tree_for(path: Path | str) -> ast.Module:
    """The parsed tree for `path`, parsed once and shared with every later caller."""
    resolved = Path(path).resolve()
    tree = _cache.get(resolved)
    if tree is None:
        source = resolved.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(resolved))
        _cache[resolved] = tree
    return tree


@overload
def parse_snippet(source: str, *, filename: str = ...,
                  mode: Literal["exec"] = "exec") -> ast.Module: ...
@overload
def parse_snippet(source: str, *, filename: str = ..., mode: Literal["eval"]) -> ast.Expression: ...
@overload
def parse_snippet(source: str, *, filename: str = ...,
                  mode: Literal["single"]) -> ast.Interactive: ...


def parse_snippet(source: str, *, filename: str = "<snippet>", mode: str = "exec") -> ast.AST:
    """Parse a checker's own literal fixture. Not a real file, and never cached."""
    return ast.parse(source, filename=filename, mode=mode)


def cached() -> dict[Path, ast.Module]:
    """Every path cached so far, keyed by its resolved path."""
    return dict(_cache)


def reparsed(path: Path) -> ast.Module:
    """A fresh parse of `path`, independent of the cache.

    Used only by the test that confirms a cached tree was not mutated in place - it
    would defeat that check to hand back the same cached object.
    """
    resolved = Path(path).resolve()
    source = resolved.read_text(encoding="utf-8", errors="ignore")
    return ast.parse(source, filename=str(resolved))


def source_files(top: Path) -> list[Path]:
    """Every `.py` file under `top`, in order, the way a scan of a package wants them.

    `Path.rglob` does not follow a linked folder, and each folder of `extensions/` is a link
    to a clone on a development machine, so a scan built on it finds nothing there and
    passes. This follows them. The tests an extension carries are left out of a scan of
    `extensions/`: they are checked by the rules for tests.
    """
    found: list[Path] = []
    for here, folders, files in os.walk(top, followlinks=True):
        at = Path(here)
        folders[:] = sorted(
            name for name in folders
            if name != "__pycache__" and not name.startswith(".")
            and not (name == "tests" and at.parent == top and top.name == "extensions"))
        found += [at / name for name in sorted(files) if name.endswith(".py")]
    return sorted(found)


_MANAGER = Path(__file__).resolve().parents[2] / "manager"


def manager_imports(node: ast.ImportFrom) -> list[tuple[str, str, str | None]]:
    """What a `from manager... import` binds, as (bound name, module stem, imported name).

    The imported name is None where the import binds the module itself. A package on
    the way is not a module, so `from manager.kit import grid` and `from manager import
    grid` both name the module `grid`, and module stems are unique across the tree.
    """
    parts = (node.module or "").split(".")
    if node.level or parts[0] != "manager":
        return []
    folder, rest = _MANAGER, parts[1:]
    while rest and (folder / rest[0] / "__init__.py").is_file():
        folder, rest = folder / rest[0], rest[1:]
    found: list[tuple[str, str, str | None]] = []
    for alias in node.names:
        bound = alias.asname or alias.name
        if not rest:
            if (folder / f"{alias.name}.py").is_file():
                found.append((bound, alias.name, None))
        elif len(rest) == 1:
            found.append((bound, rest[0], alias.name))
    return found
