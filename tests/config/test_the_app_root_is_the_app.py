"""`APP_ROOT` is the folder the app lives in, wherever `paths.py` is filed."""

from __future__ import annotations

import unittest

from common.config.paths import APP_ROOT, bundled


class AppRootTests(unittest.TestCase):
    def test_the_app_root_holds_the_entry_point_and_the_bundled_extensions(self) -> None:
        self.assertTrue((APP_ROOT / "main.py").is_file())
        self.assertTrue(bundled("extensions").is_dir())


if __name__ == "__main__":
    unittest.main()
