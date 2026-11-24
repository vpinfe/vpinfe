"""Finding and importing a module that ships outside the package."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.host.third_party import find_named_path, import_module_from_path


class ThirdPartyLoaderTests(unittest.TestCase):
    def test_third_party_helpers_find_and_import_module(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            module_path = nested / "service_wrapper.py"
            module_path.write_text(
                "class DemoController:\n"
                "    value = 42\n",
                encoding="utf-8",
            )

            self.assertEqual(find_named_path(root, ("service_wrapper.py",)), module_path)
            module = import_module_from_path(module_path, module_prefix="_test")

            self.assertEqual(module.DemoController.value, 42)


class DisplayEnumerationTests(unittest.TestCase):
    """A machine that cannot list its displays still starts.

    Enumeration raises where no session is attached - a headless server, a container, a
    session that has gone away. It used to raise out of `get_display_monitors`, and the
    theme asks for that list while starting up, so the frontend connected, served
    contract 2, and never finished: a blank screen with everything reporting healthy.
    """

    def setUp(self) -> None:
        from common.host import display_service
        self.service = display_service
        self._real = display_service._query_monitors
        display_service._monitors_cache = None
        self.addCleanup(setattr, display_service, "_query_monitors", self._real)
        self.addCleanup(setattr, display_service, "_monitors_cache", None)

    def _explode(self):
        raise RuntimeError("No enumerators available")

    def test_no_display_reads_as_no_monitors(self) -> None:
        self.service._query_monitors = self._explode
        with self.assertLogs("vpinfe.common.host.display_service", "WARNING"):
            self.assertEqual(self.service.get_display_monitors(), [])

    def test_the_api_shape_survives_it(self) -> None:
        """What the browser awaits during startup, so it must answer rather than throw."""
        self.service._query_monitors = self._explode
        with self.assertLogs("vpinfe.common.host.display_service", "WARNING"):
            self.assertEqual(self.service.monitors_as_dicts(), [])


if __name__ == "__main__":
    unittest.main()
