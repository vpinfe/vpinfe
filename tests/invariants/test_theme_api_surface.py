"""The theme surface says the same thing in all three places it is written down.

`frontend/api.py` splits what the bridge dispatches into what a theme may call and what
only the overlays VPinFE ships may. That split is worth nothing unless three things
agree: the published set, the API table in docs/theme.md a theme author builds against,
and the set `vpin.call` refuses in the browser. Only this holds them together.

It also checks the other direction, that every name core's own JavaScript calls is a name
the bridge answers. Nothing else can: a dead-code sweep over Python cannot see a caller
living in JavaScript on the far side of the bridge, which is how trigger_audio_play was
deleted in March 2026 and kept two call sites for five releases.

The reverse of the first check - every published name is documented - is not asserted.
Some published names exist for vpinfe-core.js's own bootstrap and have never been in the
table, and sorting the rest of them into the two sets is a separate decision.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from frontend.api import (
    _RENAMED_METHODS,
    API,
    API_ALLOWED_METHODS,
    API_INTERNAL_METHODS,
    API_PUBLISHED_METHODS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
THEME_DOC = REPO_ROOT / "docs" / "theme.md"
STATIC_ROOT = REPO_ROOT / "frontend" / "static"
CORE_JS = STATIC_ROOT / "common" / "vpinfe-core.js"

TABLE_HEADER = "| Method | Args | Returns | Description |"
METHOD_ROW = re.compile(r"\| `([a-z_]+)` \|")

# `vpin.call("x")`, `window.parent.vpin.call('x')`, core's own `this.callInternal("x")`.
# A call whose name is a variable is invisible here, and there are three of those.
JS_CALL = re.compile(r"""\.call(?:Internal)?\(\s*['"](\w+)['"]""")


def _documented_methods() -> set[str]:
    """The names listed under `call(method, ...args)` in docs/theme.md.

    Only rows of a table with that exact header count. The document is full of other
    tables, and one of them lists lifecycle scopes that read like method names.
    """
    body = THEME_DOC.read_text(encoding="utf-8")
    body = body.split("#### call(method, ...args)", 1)[1].split("#### getImageURL", 1)[0]

    found, in_table = set(), False
    for line in body.splitlines():
        if line.startswith(TABLE_HEADER):
            in_table = True
        elif in_table and not line.startswith("|"):
            in_table = False
        elif in_table:
            row = METHOD_ROW.match(line)
            if row:
                found.add(row.group(1))
    return found


def _called_from_javascript() -> dict[str, set[str]]:
    """Every bridge method name the shipped JavaScript names, by the file naming it."""
    found: dict[str, set[str]] = {}
    for path in sorted(STATIC_ROOT.rglob("*")):
        if path.suffix not in (".js", ".html"):
            continue
        for name in JS_CALL.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(path.relative_to(REPO_ROOT).as_posix())
    return found


def _refused_in_the_browser() -> set[str]:
    source = CORE_JS.read_text(encoding="utf-8")
    block = source.split("const INTERNAL_METHODS = new Set([", 1)[1].split("]", 1)[0]
    return set(re.findall(r'"(\w+)"', block))


class ThemeSurfaceTests(unittest.TestCase):

    def test_the_doc_has_an_api_table_to_check(self) -> None:
        """A parse that quietly finds nothing would pass everything below."""
        self.assertGreater(len(_documented_methods()), 20)

    def test_the_doc_promises_nothing_the_bridge_would_refuse(self) -> None:
        undocumentable = sorted(_documented_methods() - API_PUBLISHED_METHODS)
        self.assertEqual(undocumentable, [],
                         "docs/theme.md documents a method a theme cannot call")

    def test_an_internal_method_is_not_documented(self) -> None:
        """Unpublishing means leaving the table too, or the doc invites the refusal."""
        self.assertEqual(sorted(_documented_methods() & API_INTERNAL_METHODS), [])

    def test_a_method_is_published_or_internal_and_not_both(self) -> None:
        self.assertEqual(sorted(API_PUBLISHED_METHODS & API_INTERNAL_METHODS), [])
        self.assertEqual(API_ALLOWED_METHODS, API_PUBLISHED_METHODS | API_INTERNAL_METHODS)

    def test_every_internal_name_is_a_method_that_exists(self) -> None:
        """The overlays still call these, so a typo here is a broken collection menu."""
        missing = sorted(name for name in API_INTERNAL_METHODS
                         if not callable(getattr(API, name, None)))
        self.assertEqual(missing, [])

    def test_the_browser_refuses_exactly_what_python_calls_internal(self) -> None:
        """Two hand-written lists, one in each language. This is what pairs them."""
        self.assertEqual(_refused_in_the_browser(), API_INTERNAL_METHODS)

    def test_there_are_javascript_call_sites_to_check(self) -> None:
        """A regex that quietly matches nothing would pass the two checks below."""
        self.assertGreater(len(_called_from_javascript()), 30)

    def test_the_javascript_calls_nothing_the_bridge_cannot_answer(self) -> None:
        called = _called_from_javascript()
        unanswerable = {name: sorted(files) for name, files in called.items()
                        if name not in API_ALLOWED_METHODS}
        self.assertEqual(unanswerable, {},
                         "shipped JavaScript calls a method the bridge does not have")

    def test_core_does_not_call_its_own_deprecated_names(self) -> None:
        """The deprecation log is the evidence for retiring an alias. Core calling one
        puts core in a count that is only worth reading as a count of themes.
        """
        called = _called_from_javascript()
        stale = {name: sorted(files) for name, files in called.items()
                 if name in _RENAMED_METHODS}
        self.assertEqual(stale, {})


if __name__ == "__main__":
    unittest.main()
