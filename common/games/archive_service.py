"""Packing a game into a `.vpxz` so it can be handed to another machine."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from common import service_errors
from common.games import info_files
from common.games.export_bundle import bundle_paths, prune_info
from common.i18n import t


@dataclass(frozen=True)
class VpxzArchive:
    path: str
    temp_dir: str
    filename: str


def create_vpxz_archive(game_dir: Path, *, everything: bool = False,
                        table: str | None = None) -> VpxzArchive:
    """Create a temporary .vpxz archive for a game.

    Default is the standalone bundle for one table - export a game, not a
    folder. `everything=True` archives the whole directory. Either way the
    layout is folder-wrapped, which is what the mobile importers expect, and
    the shipped .info describes only what the archive actually holds.

    Raises NotFoundError for a folder that is not there.
    """
    # A path with no name wraps nothing: `Path("")` is the working directory.
    if not game_dir.name or not game_dir.is_dir():
        raise service_errors.NotFoundError(t("error.games.not_found"))

    tmp_dir = tempfile.mkdtemp()
    vpxz_path = os.path.join(tmp_dir, f"{game_dir.name}.vpxz")

    contents = list(bundle_paths(game_dir, everything=everything, table=table))
    # Forward slashes: an arcname carries the OS separator, an assets key never does.
    bundled_arcnames = {str(arcname).replace(os.sep, "/") for _, arcname in contents}
    info_name = info_files.info_name(game_dir.name)

    with zipfile.ZipFile(vpxz_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in contents:
            member = f"{game_dir.name}/{arcname}"
            if arcname == info_name:
                archive.writestr(member, prune_info(
                    path.read_text(encoding="utf-8", errors="replace"), bundled_arcnames))
            else:
                archive.write(path, member)

    return VpxzArchive(path=vpxz_path, temp_dir=tmp_dir, filename=f"{game_dir.name}.vpxz")


def cleanup_archive(archive: VpxzArchive) -> None:
    shutil.rmtree(archive.temp_dir, ignore_errors=True)


def archive_for(game_id: str, *, everything: bool = False,
                table: str = "") -> VpxzArchive:
    """One game's archive, addressed the way every other game endpoint addresses one."""
    from common.games import game_lens

    game = game_lens.game_or_refuse(game_id)
    return create_vpxz_archive(Path(str(game.full_path_game or "")),
                               everything=everything, table=table or None)
