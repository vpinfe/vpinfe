"""What the import confirmation says a file becomes."""

import unittest

from common.config import config_schema
from common.i18n import t
from manager.library.import_dialog import STOP_ASKING, _where

PLAN = {"game_dir": "/tables/Game (Maker 1990)"}


def _item(name: str, destination: str, action: str = "copy") -> dict[str, str]:
    return {"name": name, "destination": f"{PLAN['game_dir']}/{destination}",
            "action": action}


class ImportDestinationTests(unittest.TestCase):
    def test_a_file_kept_under_its_name_names_the_folder(self) -> None:
        self.assertEqual(_where(PLAN, _item("rules.pdf", "rules.pdf")),
                         t("manager.import_dialog.game_folder"))

    def test_a_renamed_file_says_its_new_name(self) -> None:
        self.assertEqual(_where(PLAN, _item("Rules.txt", "readme.txt")), "readme.txt")

    def test_a_file_in_a_subfolder_names_the_subfolder(self) -> None:
        self.assertEqual(_where(PLAN, _item("game.cfg", "pinmame/cfg/game.cfg")),
                         "pinmame/cfg/")

    def test_a_renamed_file_in_a_subfolder_says_the_whole_path(self) -> None:
        self.assertEqual(_where(PLAN, _item("b.ini", "pinmame/ini/game.ini")),
                         "pinmame/ini/game.ini")


class StopAskingTests(unittest.TestCase):
    def test_dont_ask_again_writes_a_setting_this_install_has(self) -> None:
        settable = {(option.section, option.key) for option in config_schema.settable()}
        written = {(section, key) for section, values in STOP_ASKING.items()
                   for key in values}

        self.assertEqual(written - settable, set())


if __name__ == "__main__":
    unittest.main()
