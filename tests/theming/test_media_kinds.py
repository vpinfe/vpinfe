"""The media vocabulary, and the two places outside Python that restate it.

`common/media/media_specs.py` is the list. core.js needs its own copy to tell a theme that
`preload.kinds` names something nobody serves, and that copy is the kind of thing that
drifts silently - a lookup for a kind that does not exist returns nothing rather than
complaining, which is exactly the bug the check exists to catch.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from common.media import media_specs

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CORE_JS_PATH = REPO_ROOT / "frontend" / "static" / "common" / "vpinfe-core.js"
CORE_JS = _CORE_JS_PATH.read_text(encoding="utf-8")
SPECS_PY = (REPO_ROOT / "common" / "media" / "media_specs.py").read_text(encoding="utf-8")


def _python_kinds() -> list[str]:
    # The kind is on the line after `MediaSpec(`, one field per line, so this spans the
    # newline. The count guard below is what catches it if that layout changes again.
    return re.findall(r'MediaSpec\(\s*"([a-z_]+)"', SPECS_PY)


def _js_kinds() -> list[str]:
    block = re.search(r"const MEDIA_KINDS = \[(.*?)\];", CORE_JS, re.S)
    assert block, "MEDIA_KINDS moved; this test needs updating"
    return re.findall(r'"([a-z_]+)"', block.group(1))


class VocabularyTests(unittest.TestCase):
    def test_the_javascript_copy_matches_the_python_list(self) -> None:
        self.assertEqual(_js_kinds(), _python_kinds())

    def test_the_list_is_the_one_the_specs_actually_declare(self) -> None:
        """Guards the regex above: a rewrite of media_specs must not read as zero kinds."""
        kinds = _python_kinds()
        self.assertGreater(len(kinds), 15)
        self.assertEqual(len(kinds), len(set(kinds)), "a kind is declared twice")

    def test_every_window_media_kind_exists(self) -> None:
        """Core shows a display window the media named for it, so those names must be
        kinds - otherwise the default renders nothing and says nothing."""
        for window in ("playfield", "backglass", "scoreview", "topper"):
            self.assertIn(window, _python_kinds())

    def test_the_contract_1_spellings_all_alias_to_a_real_kind(self) -> None:
        block = re.search(r"const MEDIA_KIND_ALIASES = \{(.*?)\n\};", CORE_JS, re.S)
        self.assertIsNotNone(block, "the alias map moved")
        assert block is not None
        kinds = set(_python_kinds())
        for old, new in re.findall(r'"?([\w-]+)"?:\s*"(\w+)"', block.group(1)):
            self.assertIn(new, kinds, f"{old} aliases to {new}, which is not a kind")


class PreloadDefaultTests(unittest.TestCase):
    def test_the_default_preload_set_is_canonical(self) -> None:
        """It named `bg`, which only a contract 1 theme has translated for it - so at
        contract 2 the backglass was never preloaded at all."""
        block = re.search(r"this\._preloadKinds = \[(.*?)\];", CORE_JS, re.S)
        self.assertIsNotNone(block, "the default preload list moved")
        assert block is not None
        kinds = re.findall(r'"(\w+)"', block.group(1))
        self.assertTrue(kinds)
        for kind in kinds:
            self.assertIn(kind, _python_kinds(), f"{kind} is not a media kind")


class SpecAccessorTests(unittest.TestCase):
    def test_the_module_agrees_with_its_own_source(self) -> None:
        """Reading the file with a regex is only safe while it matches the import."""
        self.assertEqual([spec.kind for spec in media_specs.MEDIA_SPECS], _python_kinds())

    def test_pythons_own_aliases_all_name_a_real_kind(self) -> None:
        """The JS map is checked the same way. They are deliberately not identical -
        core collapses both real DMD spellings onto `real_dmd` because one resolver
        handles the color split - so each is checked against the kinds, not the other."""
        kinds = {spec.kind for spec in media_specs.MEDIA_SPECS}
        for old, new in media_specs.MEDIA_KIND_ALIASES.items():
            self.assertIn(new, kinds, f"{old} aliases to {new}, which is not a kind")


if __name__ == "__main__":
    unittest.main()
