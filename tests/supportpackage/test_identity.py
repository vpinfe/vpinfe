"""Who is private on an install is read from the install's own stores, and a store that will
not open never stops a package."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.support import identity

DEVICE = SimpleNamespace(display_name="Zed Display Wall", address="zed.example.net",
                         device_id="zed-device-0123456789abcdef")


class Guarding(unittest.TestCase):
    def test_blanks_and_repeats_are_dropped_and_order_is_kept(self) -> None:
        self.assertEqual(identity._guard(lambda: [" a ", "", "b", "a", "  "]), ("a", "b"))

    def test_a_reader_that_raises_is_nothing(self) -> None:
        def fail() -> list[str]:
            raise OSError("closed")

        self.assertEqual(identity._guard(fail), ())


class Reading(unittest.TestCase):
    def build(self):
        with patch("common.support.identity.getpass.getuser", return_value="patex"), \
                patch("common.support.identity.Path.home",
                      return_value=Path("/home/someone")), \
                patch("common.support.identity.socket.gethostname",
                      return_value="cabinet-zed.lan"), \
                patch.object(identity, "_install_name", return_value="Zed Cabinet"), \
                patch.object(identity, "_computer_name", return_value=""), \
                patch.dict("os.environ", {"COMPUTERNAME": "", "USERDOMAIN": "ZEDGROUP"}), \
                patch.object(identity, "_registry_devices", return_value=[DEVICE]), \
                patch.object(identity, "_account_values", return_value=["patex-handle"]), \
                patch("common.players.players.get_roster") as roster, \
                patch("common.devices.install_identity.install_id",
                      return_value="zed-install-0123456789abcdef"), \
                patch("common.games.library_folders.configured",
                      return_value=[SimpleNamespace(path="/home/someone/zedtables")]), \
                patch("common.games.game_index_service.get_rows",
                      return_value=[{"name": "Zedmoon Classic",
                                     "game_dir": "/x/Zedmoon Classic (1990)",
                                     "filename": "Zedmoon Classic v2.vpx"}]):
            roster.return_value.players.return_value = [
                SimpleNamespace(name="Pat Example", initials="PEX")]
            return identity.build_identity()

    def test_a_table_is_named_by_its_row_its_folder_and_its_file(self) -> None:
        found = self.build()
        for name in ("Zedmoon Classic", "Zedmoon Classic (1990)", "Zedmoon Classic v2.vpx",
                     "Zedmoon Classic v2"):
            with self.subTest(name):
                self.assertIn(name, found.tables)

    def test_a_blank_machine_name_adds_no_suffix_alone(self) -> None:
        found = self.build()
        self.assertNotIn("", found.hosts)
        self.assertNotIn(".local", found.hosts)
        self.assertNotIn(".lan", found.hosts)
        self.assertIn("ZEDGROUP", found.hosts)

    def test_each_store_contributes_its_own_kind(self) -> None:
        found = self.build()
        self.assertIn("/home/someone", found.homes)
        self.assertIn("patex", found.logins)
        self.assertIn("cabinet-zed", found.hosts)
        self.assertIn("Zed Cabinet", found.hosts)
        self.assertEqual(found.devices, ("Zed Display Wall",))
        self.assertEqual(found.domains, ("zed.example.net",))
        self.assertIn("Pat Example", found.players)
        self.assertIn("patex-handle", found.players)
        self.assertIn("zed-install-0123456789abcdef", found.identifiers)
        self.assertIn("zed-device-0123456789abcdef", found.identifiers)
        self.assertEqual(found.libraries, ("/home/someone/zedtables",))
        self.assertIn("Zedmoon Classic", found.tables)
        self.assertIn("Zedmoon Classic (1990)", found.tables)

    def test_every_store_failing_still_gives_an_identity(self) -> None:
        def fail(*_args, **_kwargs):
            raise RuntimeError("closed")

        with patch.object(identity, "_install_name", fail), \
                patch.object(identity, "_registry_devices", fail), \
                patch.object(identity, "_account_values", fail), \
                patch("common.players.players.get_roster", fail), \
                patch("common.devices.install_identity.install_id", fail), \
                patch("common.games.library_folders.configured", fail), \
                patch("common.games.game_index_service.get_rows", fail):
            found = identity.build_identity()
        self.assertEqual(
            (found.devices, found.domains, found.players, found.identifiers, found.libraries,
             found.tables), ((), (), (), (), (), ()))


if __name__ == "__main__":
    unittest.main()
