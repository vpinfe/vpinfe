from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.host.vpx_log import (
    delete_vpinball_log_on_start_if_configured,
    resolve_vpinball_log_path,
)


class VpxLogTests(unittest.TestCase):
    def test_resolve_vpinball_log_path_uses_vpx_ini_parent(self) -> None:
        self.assertEqual(
            resolve_vpinball_log_path("~/VisualPinball/VPinballX.ini"),
            Path.home() / "VisualPinball" / "vpinball.log",
        )

    def test_delete_vpinball_log_on_start_if_configured_removes_log(self) -> None:
        with TemporaryDirectory() as tmp:
            vpx_ini = Path(tmp) / "VPinballX.ini"
            vpx_ini.write_text("[Player]\n", encoding="utf-8")
            log_path = Path(tmp) / "vpinball.log"
            log_path.write_text("old log", encoding="utf-8")

            result = delete_vpinball_log_on_start_if_configured(
                True, str(str(vpx_ini)))

            self.assertEqual(result, log_path)
            self.assertFalse(log_path.exists())

    def test_delete_vpinball_log_on_start_if_configured_respects_disabled_setting(self) -> None:
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "vpinball.log"
            log_path.write_text("old log", encoding="utf-8")

            result = delete_vpinball_log_on_start_if_configured(
                False, str(str(Path(tmp) / "VPinballX.ini")))

            self.assertIsNone(result)
            self.assertTrue(log_path.exists())

    def test_delete_vpinball_log_on_start_if_configured_ignores_uppercase_log(self) -> None:
        with TemporaryDirectory() as tmp:
            vpx_ini = Path(tmp) / "VPinballX.ini"
            vpx_ini.write_text("[Player]\n", encoding="utf-8")
            uppercase_log = Path(tmp) / "VPinballX.log"
            uppercase_log.write_text("old log", encoding="utf-8")

            result = delete_vpinball_log_on_start_if_configured(
                True, str(str(vpx_ini)))

            self.assertEqual(result, Path(tmp) / "vpinball.log")
            self.assertTrue(uppercase_log.exists())


if __name__ == "__main__":
    unittest.main()
