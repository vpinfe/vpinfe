"""Whose play numbers a game's wheel, sort and `user` fields show."""

from __future__ import annotations

import logging
import os
from typing import Any

from common import apps
from common.games import tables
from common.games.game_metadata import default_table_entry, table_play_record
from common.timestamps import epoch_to_iso, iso_to_epoch

logger = logging.getLogger("vpinfe.common.games.play_numbers")

SECTION = "presentation"
KEY = "play_numbers"
GAME = "game"
TABLE = "table"
LAUNCHER = "launcher"
MODES = (GAME, TABLE, LAUNCHER)

COUNTERS = ("last_played", "play_count", "play_time_seconds")


def current_mode() -> str:
    """The saved answer, or the whole game's where the file says something else."""
    from common.config.paths import get_ini_config

    saved = str(get_ini_config().value(SECTION, KEY) or "").strip()
    return saved if saved in MODES else GAME


_built_on: list[str] = []


def changed_since_built() -> bool:
    """True when the saved answer is not the one the views were last built on.

    A change applies when the windows open again, and a view already sorted and stamped
    under the old answer is then derived again. Nothing built yet means nothing is stale.
    """
    return bool(_built_on) and _built_on[0] != current_mode()


class PlayNumbers:
    """One reading of the setting, for the length of one payload or one sort.

    Built once per operation because a sort asks about every game and the setting, the
    launcher list and the folders they point at do not change underneath it.
    """

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode if mode in MODES else current_mode()
        if mode is None:
            _built_on[:] = [self.mode]
        self._launchers: tuple[list[Any], dict[str, str]] | None = None
        self._settings: dict[str, dict[str, Any]] = {}

    @property
    def whole_game(self) -> bool:
        return self.mode == GAME

    def counters(self, meta: Any, game_dir: str, table: dict | None = None
                 ) -> dict[str, Any] | None:
        """The three numbers for a game, or None where they are the whole game's.

        `table` is the entry being shown. None, or an entry with nothing in it, is the
        game as a whole, which is its default table.
        """
        if self.whole_game:
            return None
        entry = table or default_table_entry(meta)
        own = table_play_record(entry)
        if self.mode == TABLE:
            return own
        found = self._launcher_numbers(game_dir, entry)
        return own if found is None else found

    def _launcher_numbers(self, game_dir: str, entry: dict) -> dict[str, Any] | None:
        from common.games import launchers

        app = apps.get(tables.app_of(entry))
        if app is None or app.plays is None:
            return None
        if self._launchers is None:
            store = launchers.get_launcher_store()
            self._launchers = (store.launchers(), store.mappings())
        table_id = str(entry.get(tables.TABLE_ID_KEY, "") or "")
        launcher = launchers.launcher_for_entry(
            app.id, table_id, self._launchers[0], self._launchers[1])
        if launcher is None:
            return None
        if launcher.launcher_id not in self._settings:
            self._settings[launcher.launcher_id] = {
                field.key: launcher.value(field.key) for field in launcher.fields()}
        try:
            record = app.plays.record(
                apps.Entry(entry_id=table_id, game_dir=game_dir,
                           table=_path_of(game_dir, entry), key=tables.entry_key(entry)),
                self._settings[launcher.launcher_id])
        except Exception:
            logger.warning("%s could not report plays for %s", app.id, table_id,
                           exc_info=True)
            return None
        if record is None:
            return None
        # Through an epoch, so the stamp has the one spelling `play_record` gives.
        return {"last_played": epoch_to_iso(iso_to_epoch(record.last)) or None,
                "play_count": record.count,
                "play_time_seconds": record.seconds}

    def user(self, record: dict[str, Any], meta: Any, game_dir: str,
             table: dict | None = None) -> dict[str, Any]:
        """A game's `user` payload with these numbers in place of the whole game's."""
        numbers = self.counters(meta, game_dir, table)
        return record if numbers is None else {**record, **numbers}


def _path_of(game_dir: str, entry: dict) -> str:
    reference = tables.entry_reference(entry)
    if reference:
        return tables.resolved_reference(game_dir, reference)
    filename = tables.entry_filename(entry)
    return os.path.join(game_dir, filename) if filename else ""
