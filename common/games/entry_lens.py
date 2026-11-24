"""The play lens: one resolved entry as a frontend reads it.

The forward half of the wire boundary. `wire_entry` rebuilds a game-like object from one
of these on a machine that does not hold the library; this is what it is rebuilt from.
"""

from __future__ import annotations

from common.extensions import contributions
from common.games import derived_tags, game_identity
from common.games.collection_resolver import Entry, visible_entries
from common.games.game_metadata import (
    contract_1_tutorial,
    game_ipdb_id,
    game_manufacturer,
    game_rating,
    game_themes,
    game_title,
    game_type,
    game_year,
    guides_on_wire,
    play_record,
    table_descriptor,
)
from common.games.media_lookup import page_counts, resolved_kinds
from common.games.play_numbers import PlayNumbers
from common.media.manufacturer_logos import manufacturer_logo_url
from common.timestamps import epoch_to_iso


def entry_resource(entry: Entry, group: str | None = None,
                   plays: PlayNumbers | None = None) -> dict:
    """One entry as REST serves it.

    `default` is computed, never read off the entry: it is the game's own choice and
    lives in the vpinfe section, not on the table. visible_entries puts it first.

    A caller building a list passes one `plays` for all of it, so the setting is read once.
    """
    plays = plays or PlayNumbers()
    game_ident = game_identity.game_id(entry.game)
    offered = visible_entries(entry.game)
    default_id = offered[0].get("id", "") if offered else ""
    meta = entry.game.meta_config or {}
    info = meta.get("Info") or {}
    vpinfe = meta.get("vpinfe") or {}
    prefix = f"/api/v1/games/{game_ident}"
    maker = game_manufacturer(entry.game)
    return {
        "game": {
            "id": game_ident,
            "vps_id": str(info.get("VPSId", "") or ""),
            "name": game_title(entry.game),
            "manufacturer": maker,
            "year": game_year(entry.game),
            "type": game_type(entry.game),
            "themes": game_themes(entry.game),
            "dir_name": str(entry.game.game_dir_name or ""),
            "manufacturer_logo": manufacturer_logo_url(maker),
            "created_at": epoch_to_iso(getattr(entry.game, "creation_time", None)) or None,
            "rating": game_rating(entry.game),
            "user": plays.user(play_record(meta), meta,
                               str(entry.game.full_path_game or ""), entry.table),
            "derived_tags": derived_tags.game_tags(entry.game),
            "ipdb_id": game_ipdb_id(entry.game),
            "tutorial": contract_1_tutorial(entry.game.meta_config),
            "guides": guides_on_wire(entry.game.meta_config),
            "overrides": {
                "alt_title": str(vpinfe.get("alt_title", "") or ""),
                "alt_vps_id": str(vpinfe.get("alt_vpsid", "") or ""),
                "frontend_dof_event": str(vpinfe.get("frontend_dof_event", "") or ""),
            },
        },
        "table": table_descriptor(entry.table, default_id=default_id),
        "siblings": entry.siblings,
        "assets": {
            "pup_pack": bool(entry.game.pup_pack_exists),
            "alt_color": bool(entry.game.alt_color_exists),
            "alt_sound": bool(entry.game.alt_sound_exists),
        },
        "media": resolved_kinds(entry.game, entry.table_id),
        "pages": page_counts(entry.game, entry.table_id),
        # None when the order has no groups; `group_by` on the list says which.
        "group": group,
        # What extensions have contributed. Here as well as in the theme payload
        # because this lens exists to be what a frontend on another machine is built
        # from, and one missing this renders a wheel with a badge short.
        "ext": contributions.held(game_ident),
        "links": {"game": prefix, "launch": f"{prefix}/launch",
                  "media": f"{prefix}/media"},
    }
