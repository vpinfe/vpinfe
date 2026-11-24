"""The secrets file: made once, kept private, and never in the config a restore reaches."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.security import machine_secrets
from tests.support.skips import needs_posix_permissions


class Secrets(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.config = Path(holder.name) / "config"
        self.file = self.config / "secrets.json"
        for name, value in (("CONFIG_DIR", self.config), ("SECRETS_PATH", self.file)):
            patched = mock.patch.object(paths, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def test_a_secret_is_made_on_first_ask_and_the_same_after(self) -> None:
        first = machine_secrets.get("thing")

        self.assertEqual(machine_secrets.get("thing"), first)
        self.assertEqual(json.loads(self.file.read_text())["thing"], first)

    def test_two_secrets_are_different(self) -> None:
        self.assertNotEqual(machine_secrets.get("a"), machine_secrets.get("b"))

    def test_the_session_secret_is_not_the_old_fixed_one(self) -> None:
        self.assertNotEqual(machine_secrets.session_secret(), "verysecret")
        self.assertGreaterEqual(len(machine_secrets.session_secret()), 32)

    def test_a_secret_can_be_replaced_and_dropped(self) -> None:
        machine_secrets.put("thing", "one")
        machine_secrets.put("thing", "two")
        self.assertEqual(machine_secrets.get("thing"), "two")

        machine_secrets.drop("thing")
        self.assertNotIn("thing", machine_secrets.read())

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_the_file_and_its_folder_are_for_this_user_alone(self) -> None:
        machine_secrets.get("thing")

        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o700)

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_a_file_that_was_readable_is_made_private_again(self) -> None:
        machine_secrets.get("thing")
        os.chmod(self.file, 0o644)

        machine_secrets.tighten(self.file)

        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)

    def test_a_file_that_is_not_json_is_kept_aside_and_not_overwritten(self) -> None:
        self.config.mkdir(parents=True)
        self.file.write_text("not json")

        self.assertEqual(machine_secrets.read(), {})
        machine_secrets.get("thing")

        self.assertEqual(self.file.with_name("secrets.json.unreadable").read_text(), "not json")

    @needs_posix_permissions
    def test_a_file_made_readable_by_others_is_tightened_on_the_next_read(self) -> None:
        machine_secrets.put("thing", "one")
        os.chmod(self.file, 0o644)

        machine_secrets.read()

        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
