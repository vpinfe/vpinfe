"""The shim registry has to describe the shims that exist.

A list of compatibility promises is only worth having if it cannot quietly go stale, so
each check here turns one of the ways it could into a failure:

  - a shim in the registry that no longer matches the code it points at
  - a shim with no ledger entry, which is how the window messages shipped half-done:
    the handoff cited PAR-24 and the ledger stopped at PAR-23
  - a name the docs promise themes that the code does not actually emit, which is the
    launch-event bug exactly - docs/theme.md told themes to match one spelling while
    play_events.py broadcast the other alone

The registry itself is common/deprecations.py.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from common import deprecations
from common.deprecations import SHIMS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = REPO_ROOT / "docs" / "compatibility-3.0.md"
THEME_DOC = REPO_ROOT / "docs" / "theme.md"


def _block(path: Path, start: str, close: str) -> str:
    body = path.read_text(encoding="utf-8").split(start, 1)[1]
    return body[:body.index(close)]


class ShimRegistryTests(unittest.TestCase):

    def test_every_shim_points_at_code_that_is_there(self) -> None:
        missing = []
        for shim in SHIMS:
            path = REPO_ROOT / shim.implemented_in.split(":")[0].split(" ")[0]
            if not path.is_file():
                missing.append(f"{shim.key} -> {path}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_every_shim_has_a_ledger_entry(self) -> None:
        """A shim is a promise to a user, and the ledger is where promises are recorded."""
        ledger = LEDGER.read_text(encoding="utf-8")
        missing = [f"{shim.key} cites {shim.par}" for shim in SHIMS
                   if not shim.par or f"**{shim.par} —" not in ledger]
        self.assertEqual(missing, [], "\n".join(missing))

    def test_the_registry_matches_the_maps_it_describes(self) -> None:
        """Parsed from the source, so the registry cannot drift away from the code."""
        js = REPO_ROOT / "frontend" / "static" / "common" / "vpinfe-core.js"
        actual = {
            "vpin-members": dict(re.findall(
                r"(\w+): '(\w+)'", _block(js, "VPINFE_RENAMED_MEMBERS = {", "}"))),
            "ws-methods": dict(re.findall(
                r"'(\w+)': '(\w+)'",
                _block(REPO_ROOT / "frontend" / "api.py", "_RENAMED_METHODS = {", "}"))),
            "theme-payload-keys": {old: new for new, old in re.findall(
                r'"(\w+)": "(\w+)"',
                _block(REPO_ROOT / "frontend" / "theme_contract.py",
                       "_LEGACY_ROW_KEYS = {", "}"))},
        }
        for key, mapping in actual.items():
            with self.subTest(shim=key):
                declared = dict(next(s for s in SHIMS if s.key == key).names)
                self.assertEqual(declared, mapping)

    def test_the_theme_doc_names_what_the_code_emits(self) -> None:
        """The check the launch-event bug needed.

        docs/theme.md is the only description a theme author has, so a name in it that
        the code does not produce is worse than no documentation at all.
        """
        doc = THEME_DOC.read_text(encoding="utf-8")
        undocumented = []
        for shim in SHIMS:
            if shim.surface not in ("theme JavaScript", "theme payload"):
                continue
            for _old, new in shim.names:
                if new not in doc:
                    undocumented.append(f"{shim.key}: {new} is emitted but not documented")
        self.assertEqual(undocumented, [], "\n".join(undocumented))


class ShimAnnouncementTests(unittest.TestCase):
    """A wired shim has to say it was used, or the registry is the only evidence.

    Knowing what shims exist is not the same as knowing whether any is still needed.
    Without this, retiring one is a guess.
    """

    def setUp(self) -> None:
        deprecations.reset_for_tests()
        self.addCleanup(deprecations.reset_for_tests)

    def test_an_old_ini_key_announces_itself(self) -> None:
        from tempfile import TemporaryDirectory

        from common.config.config_store import ConfigStore

        with TemporaryDirectory() as tmp:
            ini = Path(tmp) / "vpinfe.ini"
            ini.write_text("[Settings]\ntablerootdir = /tmp/x\ncabmode = true\n",
                           encoding="utf-8")
            with self.assertLogs("vpinfe.common.deprecations", level="INFO") as caught:
                ConfigStore(str(ini))

        joined = "\n".join(caught.output)
        self.assertIn("tablerootdir", joined)
        self.assertIn("gamerootdir", joined)
        self.assertIn("Settings.cabmode", joined)

    def test_it_says_so_once_and_then_stays_quiet(self) -> None:
        """The payload projection runs per game per refresh."""
        with self.assertLogs("vpinfe.common.deprecations", level="INFO") as caught:
            deprecations.announce("ws-methods", "get_table_rating")
            deprecations.announce("ws-methods", "get_table_rating")
            deprecations.announce("ws-methods", "set_table_rating")

        self.assertEqual(len(caught.output), 2)

    def test_calling_a_renamed_method_announces_through_the_real_forwarder(self) -> None:
        """Through API.__getattr__, not by calling announce() directly.

        The first version of this file announced by hand and proved only that the
        logger works. The wiring in api.py was missing its import, and the cabinet
        found it: NameError on every legacy call a theme made.
        """
        from frontend.api import API

        api = object.__new__(API)
        with self.assertLogs("vpinfe.common.deprecations", level="INFO") as caught:
            forwarded = api.get_table_rating

        self.assertEqual(forwarded, api.get_game_rating)
        self.assertIn("get_table_rating", "\n".join(caught.output))

    def test_a_contract_1_theme_announces_the_projection(self) -> None:
        from frontend import theme_contract

        with self.assertLogs("vpinfe.common.deprecations", level="INFO") as caught:
            theme_contract.project({"meta": {}}, 1)

        self.assertIn("contract 1", "\n".join(caught.output))


if __name__ == "__main__":
    unittest.main()
