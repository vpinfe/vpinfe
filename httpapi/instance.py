"""What this instance is: discovery and health.

Not "meta" - every endpoint is metadata about something. These answer "what am I talking
to", which is the question discovery exists for.

Who this install is and how it announces itself is `common/devices/install_presence.py`; what it
could become is `common/online/app_updater.py`. The discovery document is here, because
links over the wire are this layer's.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Body
from fastapi.responses import JSONResponse

from common.config.paths import get_ini_config
from common.devices import install_identity, install_presence, install_setup
from common.online import app_updater
from common.vpinfe_version import get_version

from . import capabilities, models, scopes
from .auth import requires

logger = logging.getLogger("vpinfe.httpapi.instance")

# Startup wiring, called where the app is built.
announce_on_the_network = install_presence.announce_on_the_network


def mint_identity() -> None:
    """Give this install an id if it has none, and put it in its own registry.

    At startup rather than on a request: discovery only reads, so a GET never writes to
    the config file, and the id is on disk before anything can ask for it.
    """
    try:
        install_identity.ensure_id()
    except Exception as exc:
        logger.warning("Could not mint this install's identity: %s", exc)
        return
    install_presence.record_self()


def discovery_payload(prefix: str, api_version: str) -> dict:
    """The discovery document. Links are relative so they survive a reverse proxy;
    present-but-null means a known link this instance does not offer.

    `setup_pending` is present, and true, only until Setup finishes; a client reads it
    absent as set up."""
    pending = {"setup_pending": True} if install_setup.pending(get_ini_config()) else {}
    return {
        # `name` is the product, byte-identical everywhere; `install_id` is who this is.
        "name": "VPinFE",
        **install_presence.identity(),
        "api_version": api_version,
        "vpinfe_version": get_version(),
        "capabilities": capabilities.declared(),
        "extensions": [],
        "links": {
            "self": prefix,
            "health": f"{prefix}/health",
            "openapi": f"{prefix}/openapi.json",
            "docs": f"{prefix}/docs",
            "collections": f"{prefix}/collections",
            "events": f"{prefix}/events",
            "jobs": f"{prefix}/jobs",
            "manufacturers": f"{prefix}/manufacturers",
            "devices": f"{prefix}/devices",
        },
        **pending,
    }


def build_router(prefix: str, api_version: str) -> APIRouter:
    router = APIRouter(tags=["instance"])

    # Built by hand: `setup_pending` is left out unless true, and every other null stays.
    @router.get("/", summary="API discovery", dependencies=[requires(scopes.INSTANCE_READ)],
                response_model=models.Discovery)
    def discovery() -> JSONResponse:
        document = models.Discovery(**discovery_payload(prefix, api_version))
        body = document.model_dump(mode="json", by_alias=True)
        if not document.setup_pending:
            del body["setup_pending"]
        return JSONResponse(body)

    @router.get("/health", summary="Liveness check", dependencies=[requires(scopes.INSTANCE_READ)])
    def health() -> models.Health:
        return models.Health(status="ok")

    @router.get("/update", summary="Whether a newer build is published",
                dependencies=[requires(scopes.INSTANCE_READ)])
    async def update(refresh: bool = False) -> models.UpdateCheck:
        """Served because a client cannot otherwise ask: 2.x calls the check in-process,
        which any consumer over HTTP has no way to do. Can reach the network, so it runs
        off the loop."""
        from starlette.concurrency import run_in_threadpool

        return models.UpdateCheck.model_validate(
            await run_in_threadpool(app_updater.check_now, refresh))

    @router.post("/update", summary="Take the published build", status_code=202,
                 dependencies=[requires(scopes.PROTECTED)])
    async def perform_update(
            background: BackgroundTasks,
            # Optional so a caller with nothing to say can post an empty body; the
            # defaults are what that means.
            payload: models.UpdateRequest | None = Body(default=None),
    ) -> models.UpdateStarted:
        """Stage the published build, then go down so the staged updater can apply it.

        `support_reason` is returned as the detail rather than a sentence, because the
        sentences a person reads belong to the surface showing them and restating them
        here would be a second copy to keep true.
        """
        from starlette.concurrency import run_in_threadpool

        wanted = payload or models.UpdateRequest()
        started = await run_in_threadpool(app_updater.take_published,
                                          stop_table=wanted.stop_table)
        # After the response: the staged updater waits on this pid, and quitting inside
        # the handler would take the process down before the caller was told anything.
        background.add_task(app_updater.quit_for_update)
        return models.UpdateStarted.model_validate(started)

    return router
