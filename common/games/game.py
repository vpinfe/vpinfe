"""One game folder, as the rest of the app sees it.

These names are ours. The contract 1 payload publishes several of them under camelCase
keys a published theme reads - `game_dir_name` goes out as `gameDirName` - and those keys
are frozen by the parity gate. The key and the attribute are separate things:
`frontend/game_state.py` maps one to the other, and `media_specs` carries both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class GameRecord(Protocol):
    """What the metadata accessors read a game through.

    Three shapes answer: a `Game`, the wire lens a client builds out of an entry it was
    sent, and a resolved entry, which forwards both of these to the game it holds. A
    client has no folder on disk, so it cannot hold a `Game` - but the same filters and
    the same sort keys have to answer there.

    Read-only, deliberately: writing metadata needs the real `Game`, because the write
    goes to the `.info` in its folder.
    """

    @property
    def game_dir_name(self) -> str | None: ...

    @property
    def meta_config(self) -> dict[str, Any] | None: ...


class ScannedGame(GameRecord, Protocol):
    """A game whose tables can be resolved.

    The metadata says which tables a game has. The game file the scan found stands in
    for a folder that has never been through a metadata build, which is the normal
    state of a game somebody just added.
    """

    @property
    def full_path_vpx_file(self) -> str | None: ...


@dataclass
class Game:
    game_dir_name: str | None = None
    full_path_game: str | None = None
    # Which location it was found in. What lets a report say "from the share that is
    # unreachable" rather than "missing".
    location_id: str = ""
    full_path_vpx_file: str | None = None
    creation_time: float | None = None

    pup_pack_exists: bool = False
    alt_color_exists: bool = False
    alt_sound_exists: bool = False
    vni_exists: bool = False
    b2s_exists: bool = False
    ini_exists: bool = False
    music_exists: bool = False
    readme_exists: bool = False

    bg_image_path: str | None = None
    dmd_image_path: str | None = None
    playfield_image_path: str | None = None
    fss_image_path: str | None = None
    wheel_image_path: str | None = None
    cab_image_path: str | None = None
    real_dmd_image_path: str | None = None
    real_dmd_color_image_path: str | None = None
    real_dmd_video_path: str | None = None
    real_dmd_color_video_path: str | None = None
    flyer_image_path: str | None = None

    playfield_video_path: str | None = None
    bg_video_path: str | None = None
    dmd_video_path: str | None = None

    audio_path: str | None = None
    instruction_card_image_path: str | None = None
    topper_path: str | None = None
    loading_video_path: str | None = None
    audio_launch_path: str | None = None
    rule_sheet_path: str | None = None
    logo_image_path: str | None = None

    # Every .vpx the scan found in the folder. Recorded because discovery reconciles it
    # against what the .info describes, and the listing is already in hand here - asking
    # the disk a second time would be the expensive half of that job for nothing.
    table_files: list[str] | None = None

    # The `.info` files in the folder's root, lower-cased, from the same scan: what discovery
    # needs to see that a game with two tables has a table that lacks one. None where the scan
    # did not read the folder.
    info_names: frozenset[str] | None = None

    # The paths the manifest lists for this folder, from the same scan. None means the
    # scan could not read the folder in full, and discovery then leaves the manifest alone.
    file_listing: frozenset[str] | None = None

    # The same resolution, run once per .vpx in the folder: {filename: {kind: path}}.
    # The attributes above answer for the default table only, which is all a contract 1
    # theme can ask about; this is what lets /media/<table id>/<kind> answer for the
    # table it was actually addressed with. Keyed by filename because ids are backfilled
    # after the scan.
    media_by_table: dict[str, dict[str, str]] | None = None

    # The paged kinds with two or more pages, each as its files in page order, page 1
    # first: {kind: [path, ...]}. The default table's answer and, per table, the same as
    # `media_by_table` keyed by filename. A kind with one page is not here: its file is
    # in the attribute and in `media_by_table`.
    media_pages: dict[str, list[str]] | None = None
    media_pages_by_table: dict[str, dict[str, list[str]]] | None = None

    meta_config: dict[str, Any] | None = None

    # Read during the scan, which already has both. info_restorable means a backup this
    # build can read, not merely a backup - after a restore the unusable copies remain.
    info_pending_upgrade: bool = False
    info_restorable: bool = False
    # Newest backup's timestamp: "before the upgrade" means nothing weeks later.
    info_backup_stamp: str = ""
