"""The committed theme payload fixture still matches what the builder produces.

The JavaScript tests in tests/js/ read that fixture. Nothing else connects the two
languages, so without this check the payload could change while the JS tests kept
asserting the old shape and passing - which is the drift that let a media key lookup
reach the cabinet broken.

When this fails, regenerate and re-run the JS tests:

    python tests/support/theme_fixture_capture.py
    npm test --prefix tests/js
"""

from __future__ import annotations

import json
import unittest

from tests.support.theme_fixture_capture import FIXTURE, STABLE_ROOT, _stabilize, capture


class ThemeFixtureTests(unittest.TestCase):
    def test_a_windows_capture_stabilizes_to_the_committed_shape(self) -> None:
        """The fixture is committed once and checked on every platform.

        Rewriting only the root leaves the separators after it alone, so a Windows
        run produced /library\\Game\\... against the committed /library/Game/... and
        failed for a reason that had nothing to do with the payload.
        """
        captured = {"WheelImagePath":
                    r"C:\Users\RUNNER~1\AppData\Local\Temp\tmp1\Congo\medias\wheel.png",
                    "Missing": None}

        self.assertEqual(
            _stabilize(captured, r"C:\Users\RUNNER~1\AppData\Local\Temp\tmp1"),
            {"WheelImagePath": f"{STABLE_ROOT}/Congo/medias/wheel.png",
             "Missing": None})

    def test_stabilizing_leaves_strings_that_are_not_paths_alone(self) -> None:
        untouched = {"title": r"Back\Slash (Bally 1980)"}
        self.assertEqual(_stabilize(untouched, "/tmp/tmp1"), untouched)

    def test_the_committed_fixture_is_what_the_builder_produces(self) -> None:
        self.assertTrue(FIXTURE.exists(),
                        "run: python tests/support/theme_fixture_capture.py")
        committed = json.loads(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(committed, capture(),
                         "the theme payload changed; regenerate the fixture and re-run "
                         "the JS tests so they are checked against the new shape")

    def test_it_covers_the_cases_the_js_tests_rely_on(self) -> None:
        """A fixture that lost its awkward games would still pass above, silently."""
        rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["contract1"]
        by_name = {row["tableDirName"]: row for row in rows}

        self.assertIn("Attack from Mars (Bally 1995)", by_name)
        afm = by_name["Attack from Mars (Bally 1995)"]
        self.assertTrue(afm["TableImagePath"] and afm["TableVideoPath"],
                        "one game must offer both an image and a video for the same "
                        "kind, or the priority choice is untested")

        congo = by_name["Congo (Williams 1995)"]
        self.assertNotIn("/medias/", congo["WheelImagePath"],
                         "one game must resolve media from the folder root")

        mm = by_name["Medieval Madness (Williams 1997)"]
        self.assertIn("/medias/wheels/", mm["WheelImagePath"],
                      "one game must resolve a wheel set, which is the case the URL "
                      "builder needed its own branch for")

        bare = by_name["Bare Table (Gottlieb 1980)"]
        self.assertIsNone(bare["WheelImagePath"],
                          "one game must have no media at all")


if __name__ == "__main__":
    unittest.main()
