"""The online sources, and what the catalogs among them have for a game.

A source declares what it can serve and answers what it holds for one game; this reports
all of them together, the ones that ship and the ones extensions contribute, so a client
picks a picture rather than picking a website.

Which sources are asked is a setting. Which exist is not - a source is always listed, so
an install that turned one off can see that it did.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from common.online import sources

from . import models, scopes
from .auth import requires

logger = logging.getLogger("vpinfe.httpapi.mediasources")

router = APIRouter(prefix="/media-sources", tags=["media-sources"])


def _entry(source: sources.Source) -> dict:
    return {"id": source.id, "name": source.name, "url": source.url,
            "enabled": sources.enabled(source), "kinds": sorted(source.kinds),
            "extension": source.extension, "key": source.key,
            "always_on": source.always_on, "versions": source.versions}


@router.get("", summary="The online sources this install knows",
            dependencies=[requires(scopes.VPS_READ)])
def list_sources() -> models.MediaSourceList:
    """Every source, and whether it is one of the ones being asked.

    Disabled ones are listed rather than hidden, because "why is that catalog not
    coming up" is answered by seeing it sitting there switched off.
    """
    return models.MediaSourceList.model_validate(
        {"sources": [_entry(one) for one in sources.all_sources()]})


@router.get("/changes", summary="Which games' online offers have moved since a version",
            dependencies=[requires(scopes.VPS_READ)])
def get_changes(since: int = Query(0, ge=0)) -> models.MediaSourceChanges:
    """What an open Discovery pane asks about on a timer: an extension's download began,
    finished or needs the person, so the rows it drew for that game are stale."""
    version, moved = sources.changes_since(since)
    return models.MediaSourceChanges(version=version, vps_ids=moved)


@router.get("/offers", summary="What the catalogs have for one game and kind",
            dependencies=[requires(scopes.VPS_READ)])
def get_offers(vps_id: str = Query(...),
               kind: str = Query(...)) -> models.MediaOfferList:
    """Asked per kind, because that is the question a slot has.

    VPS_READ rather than a scope of its own: that scope exists because the call goes
    out to a catalog on the caller's behalf, which is exactly what this is.
    """
    found = sources.offers(kind, vps_id, sources.enabled_ids())
    return models.MediaOfferList.model_validate(
        {"offers": [{"source": offer.source, "name": offer.name, "url": offer.url,
                     "kind": offer.kind, "size": offer.size, "md5": offer.md5}
                    for offer in found]})
