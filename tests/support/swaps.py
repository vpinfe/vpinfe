"""A library that holds some games, for a test that swaps a table through the real engine.

The swap resolves its game by id and asks the library to read it again afterwards, so a
test over a bare temporary folder has to say which games the library holds. Everything
else the engine reaches for is quiet: no backglass order, no running table, and Backups
in a folder of the test's own.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.backups import folder as backup_folder
from common.games import backglass_order, game_repository, table_ops, table_swap
from common.games.game import Game
from common.host import launch_state


def library_of(case: unittest.TestCase, games: dict[str, Game] | None = None, *,
               parsed: dict | None = None) -> Path:
    """Make the library hold `games`, by id, until the test ends. `parsed` is what reading
    any arriving table says about it. Answers the folder Backups are kept in."""
    held = dict(games or {})
    backups = Path(tempfile.mkdtemp(prefix="backups-"))
    case.addCleanup(shutil.rmtree, backups, True)
    state = launch_state.LaunchState()
    for patcher in (
            patch.object(game_repository, "catalog", side_effect=lambda: dict(held)),
            patch.object(game_repository, "all_games",
                         side_effect=lambda **_: list(held.values())),
            patch.object(table_swap, "reread_game", side_effect=lambda game: game),
            patch.object(table_swap, "read_one", return_value=parsed),
            patch.object(table_ops, "row_or_refuse",
                         side_effect=lambda game, table_id: {"id": table_id}),
            patch.object(backglass_order, "settle", return_value=[]),
            patch.object(backup_folder, "current", return_value=backups),
            patch.object(launch_state, "current", side_effect=lambda: state)):
        patcher.start()
        case.addCleanup(patcher.stop)
    return backups
