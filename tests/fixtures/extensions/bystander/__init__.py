"""A second fixture extension, so failure isolation has something to be isolated from.

Its only job is to keep answering while the extension beside it is being taken out.
"""

from __future__ import annotations

from fastapi import APIRouter


def register(ctx) -> None:
    router = APIRouter()

    @router.get("/hello")
    def hello() -> dict:
        return {"name": ctx.name}

    ctx.add_router(router)
