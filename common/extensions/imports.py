"""What an extension's source may import, and the compiled files it may not carry.

One rule, held in two places: `tests/extensions/test_import_boundary.py` for the extensions we
write, and `--check-extension` for the ones other people do.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

CONTRACT = "common.extensions.contract"

# Packages of ours. An extension reaches core through the context it is handed, or through
# the contract, and through nothing else.
FIRST_PARTY = frozenset({"apps", "common", "manager", "extensions", "frontend", "httpapi",
                         "tests"})

# What VPinFE ships and the contract names, so an extension may import it and a manifest
# may not list it. One line each, because adding to this is a promise to every extension.
DEPENDENCIES_OF_THE_CONTRACT = {
    "fastapi": "ctx.add_router takes an APIRouter",
    "requests": "the contract's HTTP calls raise requests.RequestException",
}

COMPILED_SUFFIXES = (".so", ".pyd", ".dylib", ".dll")

# Not extension code: its own tests, and what an editor or the interpreter leaves.
_SKIPPED_FOLDERS = frozenset({"tests", "__pycache__"})

FIRST_PARTY_IMPORT = "first_party"
UNLISTED_IMPORT = "unlisted"


@dataclass(frozen=True)
class Refusal:
    """One import the rule does not allow. `kind` is FIRST_PARTY_IMPORT or UNLISTED_IMPORT."""

    file: str
    line: int
    module: str
    kind: str


def normalize(name: str) -> str:
    """A distribution name the way pypi.org compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def imported(tree: ast.AST) -> list[tuple[int, str]]:
    """Every module an import names, dotted and absolute, with its line. A relative import
    resolves inside the extension and carries no module name to check."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def refusals(tree: ast.AST, file: str, dependencies: Collection[str] = (),
             top_levels: Mapping[str, Collection[str]] | None = None) -> list[Refusal]:
    """The imports in `tree` that are not the standard library, the contract, a library the
    contract names or one the manifest lists.

    `dependencies` are the listed distribution names. A listed dependency covers an import whose
    top-level name is its own name, or one of the `top_levels` known for it, because a
    wheel's import name and its distribution name differ often enough (`PIL` for `pillow`).
    """
    listed = {normalize(name) for name in dependencies}
    covered = {normalize(root) for name in listed for root in (top_levels or {}).get(name, ())}
    found: list[Refusal] = []
    for line, module in imported(tree):
        root = module.split(".")[0]
        if root in sys.stdlib_module_names or root in DEPENDENCIES_OF_THE_CONTRACT:
            continue
        if module == CONTRACT or module.startswith(f"{CONTRACT}."):
            continue
        if root in FIRST_PARTY:
            found.append(Refusal(file, line, module, FIRST_PARTY_IMPORT))
        elif normalize(root) not in listed and normalize(root) not in covered:
            found.append(Refusal(file, line, module, UNLISTED_IMPORT))
    return found


def sources(folder: Path) -> list[Path]:
    """Every `.py` file in an extension's folder, following links, less its own tests."""
    found: list[Path] = []
    for here, folders, files in os.walk(folder, followlinks=True):
        folders[:] = sorted(name for name in folders
                            if name not in _SKIPPED_FOLDERS and not name.startswith("."))
        found += [Path(here) / name for name in sorted(files) if name.endswith(".py")]
    return sorted(found)


def compiled_files(folder: Path) -> list[Path]:
    """Every compiled file in an extension's folder, skipping the folders `sources` skips.
    Compiled code reaches a device only as a dependency VPinFE fetched itself."""
    found: list[Path] = []
    for here, folders, files in os.walk(folder, followlinks=True):
        folders[:] = sorted(name for name in folders
                            if name not in _SKIPPED_FOLDERS and not name.startswith("."))
        found += [Path(here) / name for name in sorted(files)
                  if name.lower().endswith(COMPILED_SUFFIXES)]
    return sorted(found)


def folder_refusals(folder: Path, dependencies: Iterable[str] = (),
                    top_levels: Mapping[str, Collection[str]] | None = None) -> list[Refusal]:
    """`refusals` over every source file in an extension's folder."""
    found: list[Refusal] = []
    listed = list(dependencies)
    for path in sources(folder):
        shown = "/".join(path.relative_to(folder).parts)
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        found.extend(refusals(tree, shown, listed, top_levels))
    return found
