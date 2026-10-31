"""Manufacturer logos a source keeps with its media, offered to the manufacturers here.

PinballX and PinballY keep `Company Logos/<Manufacturer>.png`, and for a maker that
changed its mark, one file per era: `Gottlieb (1971-1980).png`. VPinFE keeps one logo
per maker in a folder every theme shares, so an era is dropped from the name and one
file is chosen for the maker.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common.extensions.contract import ContractError

from . import finding

FOLDER = "Company Logos"
IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".apng")
# "(1971-1980)" and "(1971-)" and "(1971)" at the end of a name.
ERA = re.compile(r"\s*\(\s*(\d{4})\s*(-)?\s*(\d{4})?\s*\)\s*$")


def _era(stem: str) -> tuple[str, int]:
    """The maker a file stands for and how late its era runs: 0 for a name with no era,
    which is the file that is not one of several, and 9999 for an era with no end."""
    found = ERA.search(stem)
    if not found:
        return stem.strip(), 0
    first, dash, last = found.groups()
    if last:
        return stem[:found.start()].strip(), int(last)
    return stem[:found.start()].strip(), 9999 if dash else int(first)


def candidates(media: str) -> list[tuple[str, Path]]:
    """One (maker, image) for each maker the folder has a logo for.

    A file with no era wins over the files with one, and among those the latest era does:
    the mark a maker is known by now. Anything that is not an image stays where it is.
    """
    folder = finding.child(media, FOLDER) if media else None
    if folder is None or not folder.is_dir():
        return []
    best: dict[str, tuple[int, Path, str]] = {}
    for one in finding.files(folder):
        if one.suffix.lower() not in IMAGES:
            continue
        maker, last = _era(one.stem)
        if not maker:
            continue
        rank = 10000 if last == 0 else last
        held = best.get(maker.casefold())
        if held is None or rank > held[0]:
            best[maker.casefold()] = (rank, one, maker)
    ranked = sorted(best.values(), key=lambda one: (-one[0], one[2].casefold()))
    return [(maker, path) for _rank, path, maker in ranked]


def bring(ctx: Any, media: str, lost: list[dict[str, str]] | None = None) -> dict[str, int]:
    """Add to the logos folder the images that serve a maker among the games here.

    Every game in the library counts, not only those this run made: a second import of
    logos for the makers a first one imported is a way to fill a gap, and what is
    already served is never replaced. Nothing is asked of core when the source keeps no
    logos, or when core has no folder to put them in. A refusal is named in `lost`.
    """
    found = candidates(media)
    if not found:
        return {"placed": 0, "held": 0}
    makers = {str(one.get("manufacturer") or "").strip()
              for one in ctx.games.list_games().get("games", [])}
    makers.discard("")
    try:
        done = ctx.games.put_manufacturer_logos(sorted(makers), found)
    except (OSError, ContractError) as exc:
        ctx.logger.warning("The manufacturer logos could not be added: %s", exc)
        if lost is not None:
            lost.append({"what": ctx.t("lost.logos"), "detail": ctx.why(exc)})
        return {"placed": 0, "held": 0}
    return {"placed": len(done["placed"]), "held": len(done["held"])}
