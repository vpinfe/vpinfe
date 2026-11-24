"""A blank path setting says, in its empty field, the folder it stands for."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from common.config import path_state, paths
from common.settings import config_service
from manager.settings import settings

CHECKS = [{"section": "backups", "key": "folder", "state": "unset", "default": "/cfg/backups"},
          {"section": "tools", "key": "rar_path", "state": "unset", "default": ""}]
SCHEMA = [{"name": "backups", "options": [{"key": "folder", "path": "dir"}]},
          {"name": "tools", "options": [{"key": "rar_path", "path": "exe", "blank": "x"}]}]


class DefaultPathsCase(unittest.TestCase):
    def test_a_path_with_a_default_holds_it_and_says_whose_it_is(self) -> None:
        drawn = settings.with_default_paths(SCHEMA, CHECKS)
        option = drawn[0]["options"][0]
        self.assertEqual(option["default_path"], "/cfg/backups")
        self.assertEqual(option["default_whose"], "VPinFE's default")
        self.assertNotIn("blank", option)

    def test_a_path_without_one_is_left_as_it_was(self) -> None:
        self.assertEqual(settings.with_default_paths(SCHEMA, CHECKS)[1], SCHEMA[1])

    def test_an_older_install_that_names_no_default_changes_nothing(self) -> None:
        older = [{k: v for k, v in c.items() if k != "default"} for c in CHECKS]
        self.assertEqual(settings.with_default_paths(SCHEMA, older), SCHEMA)

    def test_the_install_names_where_each_blank_one_points(self) -> None:
        with patch.object(paths, "DEFAULT_BACKUPS_DIR", Path("/cfg/backups")), \
             patch.object(config_service, "CONFIG_DIR", Path("/cfg")):
            stands_for = config_service.blank_stands_for()
        self.assertEqual(stands_for[("backups", "folder")], str(Path("/cfg/backups")))
        self.assertEqual(stands_for[("media", "manufacturer_logos_dir")],
                         str(Path("/cfg/manufacturer_logos")))

    def test_every_default_names_a_real_path_setting(self) -> None:
        with_paths = {(o.section, o.key) for o in path_state.path_options()}
        self.assertLessEqual(set(config_service.blank_stands_for()), with_paths)


if __name__ == "__main__":
    unittest.main()
