"""Every Tool is a setting a person can reach, and every hint is one they can follow."""

from __future__ import annotations

import re
import unittest
from typing import Any

from common.config import config_schema
from common.host import tools
from tests.support.catalogs import served

# Linux distributions' package managers. Naming one is wrong on every other distribution,
# and VPinOS has none a person is meant to use.
PACKAGE_MANAGERS = re.compile(
    r"\b(?:apt|apt-get|aptitude|dpkg|dnf|yum|rpm|zypper|pacman|yay|paru|apk|emerge|"
    r"xbps-install|eopkg|nix|nix-env|snap|flatpak|pkcon|urpmi|swupd|slackpkg)\b",
    re.IGNORECASE)


def naming_a_package_manager(catalog: dict[str, Any], keys: set[str]) -> list[str]:
    return sorted(key for key in keys if PACKAGE_MANAGERS.search(str(catalog.get(key, ""))))


def _hint_keys() -> set[str]:
    return {key for tool in tools.TOOLS for key in tool.hint.values()}


class EveryToolIsASetting(unittest.TestCase):
    def test_every_tools_option_has_a_tool_and_every_tool_an_option(self) -> None:
        options = {f"{option.section}.{option.key}" for option in config_schema.settable()
                   if option.section == "tools"}

        self.assertEqual(options, {tool.option for tool in tools.TOOLS})

    def test_every_tools_option_names_a_program(self) -> None:
        for tool in tools.TOOLS:
            with self.subTest(tool=tool.id):
                option = config_schema.option(tool.section, tool.key)
                self.assertIsNotNone(option)
                assert option is not None
                self.assertEqual(option.path, "exe")

    def test_ids_are_unique(self) -> None:
        ids = [tool.id for tool in tools.TOOLS]
        self.assertEqual(len(ids), len(set(ids)))


class EveryHintCanBeFollowed(unittest.TestCase):
    def test_every_platform_a_tool_runs_on_has_a_hint(self) -> None:
        missing = [f"{tool.id} on {where}" for tool in tools.TOOLS
                   for where in tool.names if not tool.hint.get(where)]
        self.assertEqual(missing, [])

    def test_every_hint_and_reason_is_in_the_catalog(self) -> None:
        catalog = served()
        reasons = {tools.TIMED_OUT, tools.DID_NOT_START, tools.FAILED, tools.NO_SNAPSHOT}
        self.assertEqual(sorted((_hint_keys() | reasons) - set(catalog)), [])

    def test_no_hint_names_a_linux_package_manager(self) -> None:
        self.assertEqual(naming_a_package_manager(served(), _hint_keys()), [])

    def test_the_check_can_fail(self) -> None:
        catalog = {"distro": "Install grim with sudo apt install grim",
                   "arch": "Run pacman -S grim",
                   "mac": "Install unar (brew install unar)",
                   "person": "Install grim from your package manager"}

        self.assertEqual(naming_a_package_manager(catalog, set(catalog)), ["arch", "distro"])


if __name__ == "__main__":
    unittest.main()
