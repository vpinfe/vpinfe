"""Importing the Manager page is what registers the wizard's field drawers: a wizard field
of a kind nothing registered raises when it is drawn, so the page has to register them."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROBE = """
import manager.page
from manager.kit import wizard
print(sorted(wizard.FIELD_DRAWERS))
"""


class PageRegistersWizardDrawers(unittest.TestCase):
    def test_a_fresh_page_import_registers_the_settle_field(self) -> None:
        run = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, capture_output=True,
                             text=True, check=True)

        self.assertIn("settle", run.stdout)


if __name__ == "__main__":
    unittest.main()
