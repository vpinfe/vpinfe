"""A game is the folder and the machine. A table is the .vpx - what a launcher is handed.

Two sweeps have already got this backwards, and the second one did it while trying to fix
the first. It stated its rule as a shape - anything naming the game folder, or a path
under it, becomes `game_*` - and a .vpx is a path under the folder, so `vpx_table_path`
became `vpx_game_path`. Right by the rule, wrong by meaning, and review passed it because
the rule was what got checked.

So this checks meaning instead: the argument that ends up after `-play` is named for a
table, the arguments holding a folder are named for a game, and the retired spellings are
gone. A sweep that re-inverts any of it fails here rather than passing.
"""

from __future__ import annotations

import inspect
import re
import unittest
from dataclasses import fields
from pathlib import Path

from apps.vpx.launch import VPXLaunch
from common.apps.contract import Entry
from common.games import (
    archive_service,
    game_index_service,
    game_repository,
    game_service,
    media_service,
)
from tests.support.project import project_files

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Old spelling -> what it is now.
RETIRED = {
    "game_path": "game_dir",
    "table_path": "game_dir",
    "vpx_game_path": "vpx_path",
}

SKIP_PARTS = {"build", "third_party", "chromium", "tests"}
SKIP_PREFIXES = ("frontend/static/themes/",)


def _source_files():
    for pattern in ("*.py", "*.js"):
        for path in project_files(pattern):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(part in SKIP_PARTS for part in rel.split("/")):
                continue
            if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
                continue
            yield path, rel


def _parameters(func) -> list[str]:
    return list(inspect.signature(func).parameters)


class TheLaunchableArtifactIsATable(unittest.TestCase):
    def test_an_entry_calls_its_file_a_table(self) -> None:
        self.assertIn("table", {f.name for f in fields(Entry)})

    def test_that_field_is_the_one_the_app_is_told_to_play(self) -> None:
        """The name is only worth pinning if it is the launchable artifact it names."""
        command = VPXLaunch().command(Entry(table="/games/Example/Example.vpx"),
                                      {"bin_path": "/usr/bin/VPinballX"})

        self.assertEqual(command[-2:], ["-play", "/games/Example/Example.vpx"])

    def test_nothing_on_the_launch_path_calls_that_file_a_game(self) -> None:
        """`game_dir` is the folder and is meant to say game. Anything else beginning
        that way would be naming the file after its container."""
        named_game = [f.name for f in fields(Entry)
                      if f.name.startswith("game") and f.name != "game_dir"]
        self.assertEqual(named_game, [])


class TheFolderIsAGame(unittest.TestCase):
    def test_the_services_taking_a_folder_take_a_game_dir(self) -> None:
        for func in (game_service.replace_table, game_service.extract_vbs,
                     game_service.update_info_section, game_repository.refresh_game,
                     media_service.source_media_path, game_index_service.find_by_path,
                     archive_service.create_vpxz_archive):
            with self.subTest(func=func.__name__):
                self.assertIn("game_dir", _parameters(func))

    def test_the_services_taking_the_folders_name_say_name(self) -> None:
        for func in (media_service.thumb_file_path, media_service.media_url_from_path,
                     media_service.ensure_thumb):
            with self.subTest(func=func.__name__):
                self.assertIn("game_dir_name", _parameters(func))

    def test_the_one_signature_taking_both_keeps_them_apart(self) -> None:
        """Proof inside a single signature that the folder and its name differ."""
        self.assertEqual(_parameters(media_service.replace_media_file)[:2],
                         ["game_dir", "game_dir_name"])


class RetiredSpellings(unittest.TestCase):
    def test_no_module_still_speaks_one(self) -> None:
        offenders = []
        for path, rel in _source_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for old, new in RETIRED.items():
                for match in re.finditer(rf"\b{re.escape(old)}\b", text):
                    line_no = text[:match.start()].count("\n") + 1
                    offenders.append(f"{rel}:{line_no} says {old!r}, which is now {new!r}")

        self.assertEqual(offenders, [], "retired names still spoken:\n  "
                                        + "\n  ".join(offenders))

    def test_the_checker_can_actually_fail(self) -> None:
        """The regex is the whole test; a rewrite matching nothing would pass."""
        self.assertTrue(re.search(r"\bgame_path\b", "def f(game_path): pass"))
        self.assertIsNone(re.search(r"\bgame_path\b", "games_path = get_games_path()"))


if __name__ == "__main__":
    unittest.main()
