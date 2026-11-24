"""The settings VPinFE names are ones Visual Pinball declares, and the script that checks."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

from apps.vpx.setting_types import REGISTERED, SPANS, TYPES

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "vpx_ini_drift", ROOT / "scripts" / "vpx_ini_drift.py")
    assert spec is not None and spec.loader is not None, "the drift script is gone"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift = _script()


class NamedInTheCodeTests(unittest.TestCase):
    def test_every_setting_the_code_names_is_one_a_build_declares(self) -> None:
        named = drift.named_keys()

        self.assertEqual(drift.missing_from(named, [*TYPES, *REGISTERED]), [],
                         "a setting named in the code that no build we read declares; "
                         "fix the name, or add it to KNOWN_UNDECLARED with where it went")

    def test_every_setting_with_a_span_has_a_type_or_a_registration(self) -> None:
        self.assertEqual([key for key in SPANS if key not in TYPES and key not in REGISTERED],
                         [])

    def test_a_setting_that_is_not_declared_is_found(self) -> None:
        self.assertEqual(
            drift.missing_from(["Player.PlaySound", "Player.PlaySond"], ["Player.PlaySound"]),
            ["Player.PlaySond"])

    def test_a_setting_a_build_dropped_is_not_missing_from_the_newest_one(self) -> None:
        dropped = [key for key, span in SPANS.items() if span.until is not None]
        self.assertTrue(dropped)

        self.assertEqual(drift.missing_from(dropped, drift.accounted([], [])), [])

    def test_a_setting_the_newest_build_lacks_and_no_span_explains_is_missing(self) -> None:
        self.assertEqual(
            drift.missing_from(["Player.NotAKey"], drift.accounted(["Player.PlaySound"], [])),
            ["Player.NotAKey"])

    def test_the_start_of_a_family_is_not_a_missing_setting(self) -> None:
        self.assertEqual(drift.missing_from(["Controller.DOF"], ["Controller.DOFBell"]), [])

    def test_a_string_in_the_code_is_a_setting_only_in_a_section_that_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            where = Path(tmp) / "apps" / "vpx"
            where.mkdir(parents=True)
            (where / "uses.py").write_text(
                'A = "Player.PlaySound"\nB = "VPinballX.ini"\nC = "field.Player.PlaySound.label"\n')

            found = drift.named_keys(Path(tmp), sections={"Player"})

        self.assertEqual(found, {"Player.PlaySound": ["apps/vpx/uses.py"]})


class ChangesTests(unittest.TestCase):
    def test_a_build_s_additions_removals_and_new_types_are_told_apart(self) -> None:
        found = drift.changes({"A.x": "bool", "A.y": "int", "A.z": "bool"},
                              {"A.x": "bool", "A.y": "number", "A.w": "bool"})

        self.assertEqual(found, {"added": ["A.w"], "removed": ["A.z"],
                                 "retyped": ["A.y (int -> number)"]})


if __name__ == "__main__":
    unittest.main()
