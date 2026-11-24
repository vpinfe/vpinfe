"""scripts/i18n.py, run on a catalog of its own."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests.support.library import TempTree

ROOT = Path(__file__).resolve().parents[2]


class I18nScriptTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts" / "i18n.py", self.root / "scripts" / "i18n.py")
        self.catalogs = self.root / "common" / "i18n" / "catalogs"
        self.catalogs.mkdir(parents=True)
        (self.catalogs / "en.json").write_text(
            json.dumps({"greeting": "Hello {name}", "farewell": "Goodbye"}), encoding="utf-8")

    def _run(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(self.root / "scripts" / "i18n.py"), *flags],
                              capture_output=True, text=True, timeout=60, check=False)

    def _written(self) -> list[str]:
        return sorted(one.name for one in self.catalogs.iterdir())

    def test_record_and_pseudo_both_write(self) -> None:
        ran = self._run("--record", "--pseudo")

        self.assertEqual((ran.returncode, self._written()),
                         (0, ["en.hashes.json", "en.json", "qps.json"]), ran.stderr)
        pseudo = json.loads((self.catalogs / "qps.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(pseudo), ["farewell", "greeting"])
        self.assertIn("{name}", pseudo["greeting"])

    def test_a_report_reads_what_the_writers_just_wrote(self) -> None:
        ran = self._run("--missing", "qps", "--pseudo")

        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("0 of 2 keys have no qps", ran.stdout)

    def test_recording_is_not_run_with_the_report_it_would_clear(self) -> None:
        ran = self._run("--record", "--stale", "fr")

        self.assertEqual((ran.returncode, self._written()), (2, ["en.json"]))
        self.assertIn("not allowed with", ran.stderr)

    def _reworded(self, **english: str) -> None:
        self._run("--record")
        held = json.loads((self.catalogs / "en.json").read_text(encoding="utf-8"))
        held.update(english)
        (self.catalogs / "en.json").write_text(json.dumps(held), encoding="utf-8")

    def test_a_reworded_key_is_not_recorded_until_it_is_answered(self) -> None:
        self._reworded(farewell="See you")
        before = (self.catalogs / "en.hashes.json").read_text(encoding="utf-8")

        ran = self._run("--record")

        self.assertEqual(ran.returncode, 1)
        self.assertIn("farewell\n    now: See you", ran.stdout)
        self.assertEqual((self.catalogs / "en.hashes.json").read_text(encoding="utf-8"), before)

    def test_a_reworded_key_named_as_fitting_is_recorded(self) -> None:
        self._reworded(farewell="See you")

        ran = self._run("--record", "--fits", "farewell")

        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(self._run("--record").returncode, 0)

    def test_a_renamed_key_needs_no_answer(self) -> None:
        self._run("--record")
        (self.catalogs / "en.json").write_text(
            json.dumps({"greeting": "Hello {name}", "parting": "See you"}), encoding="utf-8")

        self.assertEqual(self._run("--record").returncode, 0)

    def test_naming_a_key_that_was_not_reworded_is_refused(self) -> None:
        self._run("--record")

        ran = self._run("--record", "--fits", "farewell")

        self.assertEqual(ran.returncode, 1)
        self.assertIn("Not reworded since the last record: farewell", ran.stderr)
