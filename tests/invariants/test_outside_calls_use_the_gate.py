"""A call to another machine goes through `http_client`, or asks it first.

The gate is where VPINFE_OFFLINE refuses a host outside this machine, where a host that
said wait is left alone, and where a timeout is always set. A call that goes around it
does none of the three, so the suite can reach the network and an offline machine waits
out a timeout for every one.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
CODE = ("apps", "common", "manager", "extensions", "frontend", "httpapi",
        "cli.py", "main.py")
GATE = "common/security/http_client.py"
ASKS_FIRST = "require_reachable"

LIBRARIES = {"requests", "httpx", "aiohttp"}
VERBS = {"get", "post", "put", "delete", "head", "patch", "request", "Session",
         "Client", "AsyncClient", "ClientSession"}
CONNECTIONS = {"urlopen", "urlretrieve", "HTTPConnection", "HTTPSConnection",
               "create_connection"}

# A transport the gate cannot wrap - a connection kept open for a whole transfer, a
# session that signs its own requests. Each asks `require_reachable` before it connects.
GUARDED = {
    "common/games/mobile_transfer.py": {
        "_ask": "a retried request to a mobile device on the network"},
    "manager/kit/api.py": {
        "_WordedSession": "the Manager's requests to this install and to a paired one, "
                          "signed and worded by the session itself"},
}
# Reached only through the gate, through a caller that asks first, or on this machine.
BEHIND = {
    "common/settings/port_checks.py": {
        "listening": "connects to 127.0.0.1 and no other address"},
    "common/security/pinned_certificates.py": {
        "_session": "`sending()` builds it for a verb the gate or a guarded session passes",
        "shown_by": "reached only through `trust_now`, which asks first",
        "vouched_for": "reached only through `trust_now`, which asks first"},
}
# Names that must ask before they connect without being a transport themselves.
ASKERS = {"common/security/pinned_certificates.py": ("trust_now",)}


def _files() -> list[Path]:
    found: list[Path] = []
    for name in CODE:
        base = ROOT / name
        found += [base] if base.is_file() else sorted(trees.source_files(base))
    return [one for one in found if one.relative_to(ROOT).as_posix() != GATE]


def _is_transport(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in LIBRARIES and node.attr in VERBS:
            return True
    if isinstance(node, ast.ImportFrom):
        return node.module in ("http.client", "urllib.request") \
            and any(one.name in CONNECTIONS for one in node.names)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr in CONNECTIONS
        # A bare name is only ours to flag where `from urllib.request import` brought it:
        # Starlette has an HTTPConnection of its own, for a request that came in.
        return isinstance(func, ast.Name) and func.id in ("urlopen", "urlretrieve",
                                                          "create_connection")
    return False


def _uses(tree: ast.Module) -> dict[str, list[int]]:
    """The line of each raw transport use, by the innermost function or class around it."""
    found: dict[str, list[int]] = {}

    def walk(node: ast.AST, inside: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = inside
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = child.name
            elif isinstance(child, ast.ClassDef):
                if any(_is_transport(base) for base in child.bases):
                    found.setdefault(child.name, []).append(child.lineno)
                here = child.name
            if _is_transport(child):
                assert isinstance(child, ast.expr | ast.stmt)
                found.setdefault(inside or "<module>", []).append(child.lineno)
            walk(child, here)

    walk(tree, "")
    return found


def _asks_first(tree: ast.Module, name: str) -> bool:
    """Whether `name` calls require_reachable, and does so before its first transport."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == name:
            body = [inner for statement in node.body for inner in ast.walk(statement)]
            asks = [one.lineno for one in body
                    if isinstance(one, ast.Call)
                    and getattr(one.func, "attr", getattr(one.func, "id", "")) == ASKS_FIRST]
            uses = [one.lineno for one in body
                    if isinstance(one, ast.expr | ast.stmt) and _is_transport(one)]
            return bool(asks) and all(min(asks) < line for line in uses)
    return False


class OutsideCallsUseTheGateTest(unittest.TestCase):
    def test_no_call_goes_around_it(self) -> None:
        offenders: list[str] = []
        for path in _files():
            rel = path.relative_to(ROOT).as_posix()
            allowed = {*GUARDED.get(rel, {}), *BEHIND.get(rel, {})}
            for where, lines in _uses(trees.tree_for(path)).items():
                if where not in allowed:
                    offenders += [f"{rel}:{line} in {where}" for line in lines]
        self.assertEqual(offenders, [], "ask through common.security.http_client, or add the "
                         "function to GUARDED with a call to require_reachable")

    def test_a_guarded_transport_asks_before_it_connects(self) -> None:
        silent = [f"{rel} {name}"
                  for rel, names in [*((rel, tuple(one)) for rel, one in GUARDED.items()),
                                     *ASKERS.items()]
                  for name in names
                  if not _asks_first(trees.tree_for(ROOT / rel), name)]
        self.assertEqual(silent, [])

    def test_an_excuse_names_a_function_that_still_goes_around(self) -> None:
        stale: list[str] = []
        for table in (GUARDED, BEHIND):
            for rel, names in table.items():
                used = _uses(trees.tree_for(ROOT / rel))
                for name, reason in names.items():
                    if name not in used or not reason:
                        stale.append(f"{rel} {name}")
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
