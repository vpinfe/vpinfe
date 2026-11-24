"""A wire entry wearing the shape the metadata accessors read.

Filtering and sorting read `meta_config` off a `Game`. A client holding a copy of the
library has no `meta_config` - it has entries, whose fields were resolved over there.
Rebuilding the sections those fields came from lets the same axis registry and the same
sort keys answer on both sides.

`name` arrives already resolved, including any `alt_title` and any leading article the
wire moved. Putting it back under `Info.Title` is safe only because moving an article in
a title that has had one moved is a no-op.
"""

from __future__ import annotations

from typing import Any

from common.games.tables import TABLE_FILENAME_KEY, TABLE_ID_KEY
from common.media.media_specs import MEDIA_SPECS
from common.timestamps import iso_to_epoch


class WireGame:
    """One entry's game half, readable by everything that reads a `Game`.

    Only the fields the filter axes and sort keys ask for: a lens for matching and
    ordering, not a stand-in for the game.
    """


    def __init__(self, game: dict[str, Any], entry: dict[str, Any] | None = None) -> None:
        user = game.get("user") or {}
        entry = entry or {}
        assets = entry.get("assets") or {}
        self.game_dir_name = game.get("dir_name") or ""
        self.derived_tags = list(game.get("derived_tags") or [])
        self.creation_time = iso_to_epoch(game.get("created_at"))
        # Empty, not missing: they name that machine's disk, so an install holding them would
        # hold an address it cannot reach - but a reader still expects the attribute.
        self.full_path_game = ""
        self.full_path_vpx_file = ""
        self.pup_pack_exists = bool(assets.get("pup_pack"))
        self.alt_color_exists = bool(assets.get("alt_color"))
        self.alt_sound_exists = bool(assets.get("alt_sound"))
        # `resolved_kinds` reports a kind when its attribute is non-empty and never reads
        # the value, so the kind's own name stands in for the path that was not sent.
        present = set(entry.get("media") or [])
        for spec in MEDIA_SPECS:
            setattr(self, spec.attr, spec.kind if spec.kind in present else "")
        self.meta_config = {
            "Info": {
                "Title": game.get("name") or "",
                "Manufacturer": game.get("manufacturer") or "",
                "Year": str(game.get("year") or ""),
                "Type": game.get("type") or "",
                "Themes": list(game.get("themes") or []),
                "VPSId": game.get("vps_id") or "",
            },
            # The flat `rating` is the same value; `user` is where it moved to, so it is
            # what gets read here.
            "User": {
                "Rating": user.get("rating", game.get("rating", 0)) or 0,
                # A filter collection on either resolves on a player as well as on the
                # library, which is the whole point of this lens - so an axis reading the
                # play record has to find it here too.
                "Favorite": bool(user.get("favorite")),
                "Tags": list(user.get("tags") or []),
                "StartCount": user.get("play_count", 0) or 0,
                # Back to the epoch integer the key is specced as, because that is what
                # the sort reads it as.
                "LastRun": iso_to_epoch(user.get("last_played")) or 0,
            },
            "vpinfe": {
                # The id is how everything addresses a game - media urls, launches, the
                # last-played record. Without it a device renders a wheel it cannot point
                # at anything in.
                "game_id": game.get("id") or "",
                # Seconds, which is what the sort uses - ordering on `User.RunTime`'s
                # minutes ties every game with under a minute on it.
                "run_time_seconds": user.get("play_time_seconds", 0) or 0,
            },
        }


def table_of(entry: dict[str, Any]) -> dict[str, Any]:
    """A wire entry's table in the shape stored tables are read in.

    The two name the same fields differently - `detects.ssf` against `detect_ssf`, a play
    record under `last_played` against `last_run` - and handing the wire dict to code that
    reads storage keys does not fail, it answers false for every flag.
    """
    table = entry.get("table") or {}
    user = table.get("user") or {}
    detects = table.get("detects") or {}
    restored = {
        TABLE_ID_KEY: table.get("id") or "",
        TABLE_FILENAME_KEY: table.get("filename") or "",
        "version": table.get("version") or "",
        "rom": table.get("rom") or "",
        "file_hash": table.get("file_hash") or "",
        "authors": list(table.get("authors") or []),
        "hidden": table.get("hidden") is True,
        "default": table.get("default") is True,
        "derived_tags": list(table.get("derived_tags") or []),
        "user": {
            # Stored beside the counters, which is where `set_table_rating` writes it.
            "rating": int(table.get("rating", 0) or 0),
            "last_run": user.get("last_played"),
            "start_count": user.get("play_count", 0) or 0,
            "run_time_seconds": user.get("play_time_seconds", 0) or 0,
            "tags": list(user.get("tags") or []),
        },
    }
    for key in ("release_date", "save_date", "save_rev"):
        # Null means never parsed, and storing "" for it would say parsed-as-blank.
        if table.get(key) is not None:
            restored[key] = table[key]
    if table.get("vbs_hash"):
        restored["vbs_hash"] = table["vbs_hash"]
    overrides = table.get("overrides") or {}
    for key in ("alt_launcher", "plugin_profile"):
        if overrides.get(key):
            restored[key] = overrides[key]
    restored.update({f"detect_{name}": bool(value) for name, value in detects.items()})
    return restored


def game_of(entry: dict[str, Any]) -> WireGame:
    """A wire entry's game, as something the accessors can read. The whole entry is
    passed because assets and media are resolved per entry, not per game."""
    return WireGame(entry.get("game") or {}, entry)
