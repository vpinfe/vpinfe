"""Manufacturers: the logo lookup made visible.

One row per distinct manufacturer string - every name VPSdb knows plus any the
library carries - with the slug it computes, the alias currently redirecting
it, the logo that resolves, and how many library tables wear it. `logo: null`
rows are the to-do list for anyone building a logo pack or migrating their
own; `aliased_to` is the only way to see a pack alias bypassing a user file.
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter

from common.config.paths import VPSDB_PATH
from common.games import game_repository
from common.games.game_repository import game_to_row
from common.media.manufacturer_logos import manufacturer_report, vps_manufacturer_names

from . import models, scopes
from .auth import requires

router = APIRouter(prefix="/manufacturers", tags=["manufacturers"])


def _vps_names() -> list[str]:
    return vps_manufacturer_names(VPSDB_PATH)


def _library_counts() -> Counter:
    return Counter(str(game_to_row(game).get("manufacturer", "") or "").strip()
                   for game in game_repository.catalog().values())


@router.get("", summary="Manufacturers, their slugs and logo coverage",
            dependencies=[requires(scopes.GAMES_READ)])
def list_manufacturers() -> models.ManufacturerList:
    counts = _library_counts()
    names = set(_vps_names()) | (set(counts) - {""})
    rows = [{**entry, "games": counts.get(entry["name"], 0)}
            for entry in manufacturer_report(names)]
    return models.ManufacturerList.model_validate({"manufacturers": rows})
