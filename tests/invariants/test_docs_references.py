"""Nothing in the tree may name a file that is not in the tree.

A doc that cites `common/tableparser.py` is not merely out of date - it sends a reader
to a path that does not exist, and nothing fails when it happens. The vocabulary rename
made several docs wrong this way at once: modules moved under `common/games/`, a
stylesheet became `games.css`, and the docs kept the old names.

The same rule covers source comments, and there it catches a second thing: a comment
that cites a document only the author has reads as a reference and is a dead end for
everybody else. If the reasoning is worth citing, it belongs in the tree.

Only repo-shaped paths are checked. A doc is full of paths that are not ours - a user's
`~/tables`, an example `<table>/pinmame/roms`, a media file inside a game folder - so the
first segment has to be a real top-level directory before a missing file counts.
"""

from __future__ import annotations

import ast
import functools
import inspect
import json
import re
import subprocess
import textwrap
import typing
import unittest
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tests.support import trees

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Only paths rooted at one of these are ours to verify.
TOP_LEVEL = {"common", "frontend", "httpapi", "managerui", "web", "scripts", "tests",
             "docs", "icon"}

# `common/games/game_parser.py`, `managerui/static/games.css`, `common/host/`
PATH_REF = re.compile(r"`([a-z_]+(?:/[A-Za-z0-9_.-]+)+/?)`")

# A bare `games.css` or `game_parser.py` carries no directory, so it is checked by name
# against the whole tree instead. Without this the stylesheet rename read as clean: the
# doc still said `tables.css` and nothing had a path to disagree with.
BARE_REF = re.compile(r"`([A-Za-z0-9_.-]+\.(?:py|css|js))`")

# `pages/games.py` - relative, the reader supplies the package. Checked by name like a
# bare reference, since only the filename is ours to resolve. Restricted to our own
# package directories so a user's `medias/wheel.png` is not mistaken for source.
PACKAGE_DIRS = {"pages", "services", "static", "host", "online", "games"}
RELATIVE_REF = re.compile(r"`((?:" + "|".join(PACKAGE_DIRS) + r")/[A-Za-z0-9_.-]+\.(?:py|css|js))`")

# `common.games.game_index_service` - an import, not a path. Needs two dots or more,
# which keeps `managerui.py` out: that is a filename that happens to contain one.
DOTTED_REF = re.compile(r"`(?:managerui|common|httpapi|frontend)((?:\.[a-z_]+){2,})`")

# load_page_style("games.css") in a fenced example - no backticks to match on.
QUOTED_FILE = re.compile(r'"([A-Za-z0-9_.-]+\.(?:py|css))"')


# A path cited on purpose by something that is not there, and right to be.
KNOWN_ABSENT = {
    # PAR-20 records that this module was deleted. The ledger is a history, so it names
    # things that no longer exist; that is the entry's whole point.
    "common/info_restore.py",
}

# Bare names that are real files, just not ours to hold.
KNOWN_ABSENT_FILES = {
    "theme.js",                 # a theme author writes this one; we only document it
    "ledcontrol_pull.py",       # ships inside the third_party DOF package
    "libdmdutil_wrapper.py",    # ships inside the bundled libdmdutil package
    "summary.md",               # a file inside a support package's zip
}


def _doc_files() -> list[Path]:
    return sorted(REPO_ROOT.joinpath("docs").glob("*.md")) + [REPO_ROOT / "README.md"]


API_DOC = REPO_ROOT / "docs" / "http-api.md"

PLACEHOLDER = re.compile(r"\{[^}]*\}")

Served = dict[tuple[str, str], str]


def _route_shape(path: str) -> str:
    """`/games/{game_id}/` and `/games/{id}` are one route: `/games/{}`."""
    return PLACEHOLDER.sub("{}", path).rstrip("/")


@functools.cache
def _served_routes() -> tuple[Served, Served]:
    """What the mounted API answers, keyed by method and shape: the routes it declares,
    then the schema and pages FastAPI serves beside them."""
    from starlette.routing import Route

    import httpapi
    from httpapi.auth import iter_api_routes

    app = httpapi.create_api_app()
    declared = {(method, _route_shape(path)): path
                for path, route in iter_api_routes(app) for method in route.methods or ()}
    own = {(method, _route_shape(route.path)): route.path
           for route in app.routes if type(route) is Route for method in route.methods or ()}
    return declared, own


def _is_catalog_key(dotted: str) -> bool:
    """DOTTED_REF captures only the tail, so match on that."""
    return any(key.endswith(dotted) for key in _catalog_keys())


@functools.cache
def _catalog_keys() -> frozenset[str]:
    """Translation keys, which read exactly like a dotted module path and are not one.

    `frontend.theme.no_tables_found` is a catalog entry; without this the check asks for
    a file called `no_tables_found.py`.
    """
    catalog = REPO_ROOT / "common" / "i18n" / "catalogs" / "en.json"
    if not catalog.is_file():
        return frozenset()
    return frozenset(json.loads(catalog.read_text(encoding="utf-8")))


class DocPathReferenceTests(unittest.TestCase):

    def test_every_repo_path_a_doc_cites_exists(self) -> None:
        missing = []
        for doc in _doc_files():
            if not doc.is_file():
                continue
            for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                for ref in PATH_REF.findall(line):
                    if ref.split("/")[0] not in TOP_LEVEL:
                        continue
                    if ref.rstrip("/") in KNOWN_ABSENT:
                        continue
                    # A trailing slash means a directory; both are just paths on disk.
                    if (REPO_ROOT / ref.rstrip("/")).exists():
                        continue
                    missing.append(f"{doc.name}:{number} cites {ref!r}")

        self.assertEqual(missing, [], "\n".join(missing))

    @unittest.skipIf(not (REPO_ROOT / ".git").exists(), "not a git checkout")
    def test_every_bare_source_filename_a_doc_cites_exists(self) -> None:
        # Tracked files only. rglob walks .venv too, where NiceGUI ships its own
        # elements/table.py - which was enough to make a dead `table.py` reference look
        # resolved.
        listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"],
                                capture_output=True, text=True, check=True).stdout.split()
        files = [REPO_ROOT / item for item in listed]
        present = {p.name for p in files}
        # `pages/games.py` has to match on the tail, not the name: `tables.py` is a real
        # file under common/games/, so a name-only check calls `pages/tables.py` fine.
        tails = {p.relative_to(REPO_ROOT).as_posix() for p in files}

        def known(ref: str) -> bool:
            if "/" in ref:
                return any(t == ref or t.endswith("/" + ref) for t in tails)
            return ref in present

        missing = []
        for doc in _doc_files():
            if not doc.is_file():
                continue
            for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                cited = set(BARE_REF.findall(line))
                cited.update(RELATIVE_REF.findall(line))
                cited.update(QUOTED_FILE.findall(line))
                cited.update(d.rsplit(".", 1)[-1] + ".py" for d in DOTTED_REF.findall(line)
                             if not _is_catalog_key(d))

                for ref in sorted(cited):
                    if ref in KNOWN_ABSENT_FILES or known(ref):
                        continue
                    missing.append(f"{doc.name}:{number} cites {ref!r}")

        self.assertEqual(missing, [], "\n".join(missing))


class DocImportExampleTests(unittest.TestCase):
    """An import in a doc example has to be one a reader can actually run.

    The rename moved `apply_table_filters` and `get_tables_path` without the docs
    following, so three examples imported names that are not there - code a reader would
    copy, paste and watch fail. Unlike a path, an import can simply be executed.
    """

    IMPORT = re.compile(r"^from ((?:managerui|common|httpapi|frontend)[\w.]*) import (.+)$")

    def test_every_documented_import_resolves(self) -> None:
        import importlib

        broken = []
        for doc in _doc_files():
            if not doc.is_file():
                continue
            for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                found = self.IMPORT.match(line.strip())
                if not found:
                    continue
                module_name, names = found.group(1), found.group(2)
                try:
                    module = importlib.import_module(module_name)
                except ImportError:
                    broken.append(f"{doc.name}:{number} cannot import {module_name!r}")
                    continue
                for name in (n.strip() for n in names.split(",")):
                    if name and not hasattr(module, name):
                        broken.append(f"{doc.name}:{number} {module_name} has no {name!r}")

        self.assertEqual(broken, [], "\n".join(broken))


class DocRouteTests(unittest.TestCase):
    """A route in the docs has to be a route the app serves.

    `GET /api/v1/collections/{name}/tables` was documented after the API had moved to
    `/games`, so the endpoint table sent a reader to a 404. Routes are worth checking
    separately from paths on disk: the app can answer what the filesystem cannot.
    """

    ROUTE = re.compile(r"`(/api/v1/[A-Za-z0-9_{}/.-]*)`")

    def test_every_documented_route_exists(self) -> None:
        declared, own = _served_routes()
        real = set(declared.values()) | set(own.values())
        # Every literal segment the app actually uses. Anything else in a doc URL is a
        # stand-in - `{id}` or a sample id like `tuF3WogthK` - and normalizes to one.
        literals = {seg for route in real for seg in route.split("/") if seg and "{" not in seg}

        def shape(route: str) -> str:
            route = re.sub(r"^/api/v1", "", route).rstrip("/") or "/"
            return "/".join(s if s in literals else "{}" for s in route.split("/"))

        known = {shape(r) for r in real}
        missing = []
        for doc in _doc_files():
            if not doc.is_file():
                continue
            for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
                for ref in self.ROUTE.findall(line):
                    if shape(ref) in known:
                        continue
                    missing.append(f"{doc.name}:{number} documents {ref!r}")

        self.assertEqual(missing, [], "\n".join(missing))


class EndpointTableTests(unittest.TestCase):
    """The endpoint table in docs/http-api.md has a row for every route the API serves,
    by method and path, and no row for one it does not."""

    # | GET | `/api/v1/games/{id}` | ... and | PUT/GET/DELETE | `/api/v1/...?q=` | ...
    ROW = re.compile(r"^\| *([A-Z]+(?:/[A-Z]+)*) *\| *`/api/v1(/[^`?]*)?[^`]*` *\|")

    def _rows(self) -> dict[tuple[str, str], list[str]]:
        rows: dict[tuple[str, str], list[str]] = {}
        for number, line in enumerate(API_DOC.read_text(encoding="utf-8").splitlines(), 1):
            found = self.ROW.match(line)
            if not found:
                continue
            for method in found.group(1).split("/"):
                where = f"{API_DOC.name}:{number}"
                rows.setdefault((method, _route_shape(found.group(2) or "")), []).append(where)
        return rows

    def test_every_route_the_api_serves_has_a_row(self) -> None:
        declared, _ = _served_routes()
        rows = self._rows()
        missing = sorted(f"{method} /api/v1{path}"
                         for (method, shape), path in declared.items()
                         if (method, shape) not in rows)
        self.assertEqual(missing, [], f"{len(missing)} of {len(declared)} routes have no row "
                                      f"in {API_DOC.name}:\n" + "\n".join(missing))

    def test_every_row_names_a_route_the_api_serves(self) -> None:
        declared, own = _served_routes()
        unserved = sorted(f"{where} {method} /api/v1{shape}"
                          for (method, shape), places in self._rows().items()
                          if (method, shape) not in declared and (method, shape) not in own
                          for where in places)
        self.assertEqual(unserved, [], "\n".join(unserved))


class ExtensionRoutesRowTests(unittest.TestCase):
    """The `any` row stands for routes that exist only while an extension runs, so the
    walk of the served routes cannot read it; it is held to what mounts them instead."""

    ROW = re.compile(r"^\| any \| `([^`]*)` \|(.*)$", re.MULTILINE)

    def _row(self) -> tuple[str, str]:
        found = self.ROW.findall(API_DOC.read_text(encoding="utf-8"))
        self.assertEqual(len(found), 1, "one row stands for every extension's routes")
        return found[0]

    def test_the_row_names_the_path_extensions_are_mounted_at(self) -> None:
        import httpapi
        from httpapi import extensions

        path, _ = self._row()

        self.assertEqual(path, f"{httpapi.API_PREFIX}{extensions.PREFIX}/{{name}}/…")

    def test_the_row_names_each_tier_an_extension_can_declare(self) -> None:
        from common.extensions import reach

        _, said = self._row()

        self.assertEqual(set(re.findall(r":(play|library|protected)\b", said)), set(reach.TIERS))


@functools.cache
def _api_routes() -> dict[tuple[str, str], Any]:
    import httpapi
    from httpapi.auth import iter_api_routes

    return {(method, _route_shape(path)): route
            for path, route in iter_api_routes(httpapi.create_api_app())
            for method in route.methods or ()}


BODY = re.compile(r"`(\{[^`]*\})`")
BODY_TOKEN = re.compile(r'"([^"]*)"|([{}\[\]:,])')


def body_keys(text: str) -> list[tuple[tuple[str, ...], str]]:
    """Each key an example body names, after the keys of the objects it sits in.

    `{"slots": [{"game_id", "kind"}]}` names `slots`, then `game_id` and `kind` in it.
    A quoted string after a colon is a value, and a bare word is a stand-in.
    """
    found = []
    opened: list[tuple[str, str | None]] = []
    key: str | None = None
    before = ""
    for string, mark in BODY_TOKEN.findall(text):
        if mark in ("{", "["):
            opened.append((mark, key if before == ":" else None))
            key = None
        elif mark in ("}", "]"):
            if opened:
                opened.pop()
        elif not mark and opened and opened[-1][0] == "{" and before in ("{", ","):
            key = string
            found.append((tuple(under for _, under in opened if under), string))
        before = mark or '"'
    return found


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The model a body or field holds, through `| None` and `list[...]`."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if typing.get_origin(annotation) is dict:
        return None
    return next(filter(None, map(_model_of, typing.get_args(annotation))), None)


def _fields(annotation: Any) -> dict[str, Any] | None:
    model = _model_of(annotation)
    if model is None:
        return None
    return {field.alias or name: field.annotation for name, field in model.model_fields.items()}


def _read_keys(function: Any, name: str, hops: int = 1) -> set[str]:
    """The keys `function` reads off its argument `name`, and those its callees read off
    it, `hops` calls deep."""
    def is_it(node: ast.AST, called: str) -> bool:
        return isinstance(node, ast.Name) and node.id == called

    read = set()
    for node in ast.walk(trees.parse_snippet(textwrap.dedent(inspect.getsource(function)))):
        if isinstance(node, ast.Subscript) and is_it(node.value, name) and isinstance(
                node.slice, ast.Constant):
            read.add(str(node.slice.value))
        if not isinstance(node, ast.Call):
            continue
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and is_it(node.func.value, name) and node.args
                and isinstance(node.args[0], ast.Constant)):
            read.add(str(node.args[0].value))
        scope = function.__globals__
        callee = (scope.get(node.func.id) if isinstance(node.func, ast.Name)
                  else getattr(scope.get(node.func.value.id), node.func.attr, None)
                  if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                  else None)
        if not hops or not inspect.isfunction(callee):
            continue
        params = list(inspect.signature(callee).parameters)
        handed = [params[at] for at, arg in enumerate(node.args)
                  if is_it(arg, name) and at < len(params)]
        handed += [kw.arg for kw in node.keywords if kw.arg and is_it(kw.value, name)]
        for inner in handed:
            read |= _read_keys(callee, inner, hops - 1)
    return read


def _body_fields(route: Any) -> dict[str, Any]:
    """The keys a route's request body can hold, each with what it holds."""
    params = route.dependant.body_params
    if not params:
        return {}
    if len(params) > 1 or getattr(params[0].field_info, "embed", False):
        return {param.alias: param.field_info.annotation for param in params}
    held = _fields(params[0].field_info.annotation)
    if held is not None:
        return held
    return dict.fromkeys(_read_keys(route.endpoint, params[0].name), Any)


def unread_body_keys(lines: list[str]) -> list[str]:
    """Each body key an endpoint row names that its route does not read."""
    routes = _api_routes()
    unread = []
    for number, line in enumerate(lines, 1):
        found = EndpointTableTests.ROW.match(line)
        if not found:
            continue
        asked = line.split("→", 1)[0]
        for method in found.group(1).split("/"):
            route = routes.get((method, _route_shape(found.group(2) or "")))
            if method == "GET" or route is None:
                continue
            for body in BODY.findall(asked):
                for path, key in body_keys(body):
                    fields: dict[str, Any] | None = _body_fields(route)
                    for step in path:
                        fields = _fields(fields[step]) if fields and step in fields else None
                    if fields is not None and key not in fields:
                        unread.append(f"{API_DOC.name}:{number} {method} "
                                      f"/api/v1{found.group(2)}: {'.'.join((*path, key))}")
    return unread


def _required_body_keys(route: Any) -> set[str]:
    params = route.dependant.body_params
    if not params:
        return set()
    if len(params) > 1 or getattr(params[0].field_info, "embed", False):
        return {param.alias for param in params if param.field_info.is_required()}
    model = _model_of(params[0].field_info.annotation)
    if model is None:
        return set()
    return {field.alias or name for name, field in model.model_fields.items()
            if field.is_required()}


def left_out_body_keys(lines: list[str]) -> list[str]:
    """Each required body key a row that gives a body example never names in it."""
    routes = _api_routes()
    left_out = []
    for number, line in enumerate(lines, 1):
        found = EndpointTableTests.ROW.match(line)
        if not found:
            continue
        named = {key for body in BODY.findall(line.split("→", 1)[0])
                 for path, key in body_keys(body) if not path}
        if not named:
            continue
        for method in found.group(1).split("/"):
            route = routes.get((method, _route_shape(found.group(2) or "")))
            if method == "GET" or route is None:
                continue
            left_out += [f"{API_DOC.name}:{number} {method} /api/v1{found.group(2)}: {key}"
                         for key in sorted(_required_body_keys(route) - named)]
    return left_out


class EndpointBodyTests(unittest.TestCase):
    """A request body key the endpoint table names is one the route reads."""

    def test_every_required_key_a_row_with_a_body_leaves_out_is_found(self) -> None:
        left_out = left_out_body_keys(API_DOC.read_text(encoding="utf-8").splitlines())

        self.assertEqual(left_out, [], "name the key inside the braces: " + "\n".join(left_out))

    def test_a_required_key_left_out_of_an_example_is_found(self) -> None:
        doctored = [
            '| POST | `/api/v1/library/tags/merge` | Fold `{"sources": [...]}` into `into` |',
            '| POST | `/api/v1/library/tags/merge` | Fold `{"sources": [...], "into": "x"}` |',
        ]

        self.assertEqual([one.rsplit(": ", 1)[1] for one in left_out_body_keys(doctored)], ["into"])

    def test_every_body_key_a_row_names_is_one_its_route_reads(self) -> None:
        unread = unread_body_keys(API_DOC.read_text(encoding="utf-8").splitlines())

        self.assertEqual(unread, [], "\n".join(unread))

    def test_the_check_can_fail(self) -> None:
        doctored = [
            '| PUT | `/api/v1/games/{id}/rating` | Rate a game, `{"stars": 3}` |',
            '| POST | `/api/v1/library/media/fill` | `{"slots": [{"game_id", "size"}]}` |',
            '| POST | `/api/v1/locations/{id}/shadowed/adopt` | `{"path", "label"}` |',
            '| GET | `/api/v1/uploads/{id}` | Session summary → `{"file_count"}` |',
            '| POST | `/api/v1/uploads` | Begin one → `{"id": ...}` |',
        ]

        self.assertEqual([one.rsplit(": ", 1)[1] for one in unread_body_keys(doctored)],
                         ["stars", "slots.size", "label"])


class CitedMarkdownTests(unittest.TestCase):
    """Every `.md` a tracked file names has to be one a reader can open.

    In the tree, or on a line that carries the link. What this catches is the third
    case: a bare name that resolves nowhere for anyone but the person who wrote it.
    """

    MARKDOWN_REF = re.compile(r"(?<![*\\])\b([A-Za-z0-9_-][A-Za-z0-9_.-]*\.md)\b")
    SCANNED = {".py", ".js", ".md", ".html", ".css", ".yml", ".yaml"}
    # Real files, just not ours to hold: Visual Pinball publishes the first, and the
    # second is a name inside a fixture archive, picked because nothing claims it, and
    # the third is a file inside a support package's zip.
    KNOWN_ABSENT = {"FileLayout.md", "notes.md", "summary.md"}

    @unittest.skipIf(not (REPO_ROOT / ".git").exists(), "not a git checkout")
    def test_every_markdown_file_a_tracked_file_names_is_in_the_tree(self) -> None:
        listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"],
                                capture_output=True, text=True, check=True).stdout.split()
        paths = [REPO_ROOT / item for item in listed]
        present = {p.name for p in paths}

        dangling = []
        for path in paths:
            if path.suffix not in self.SCANNED or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if "http" in line:
                    continue
                for ref in self.MARKDOWN_REF.findall(line):
                    if ref in present or ref in self.KNOWN_ABSENT:
                        continue
                    cited = path.relative_to(REPO_ROOT).as_posix()
                    dangling.append(f"{cited}:{number} cites {ref!r}")

        self.assertEqual(dangling, [], "\n".join(dangling))


if __name__ == "__main__":
    unittest.main()
