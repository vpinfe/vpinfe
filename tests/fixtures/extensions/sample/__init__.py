"""A fixture extension that uses the whole contract, and does nothing real with it.

Committed rather than written into a temporary directory by the test that loads it, so
the worked example of an extension is something an author can read - the same reason
`apps/generic` is a file rather than a stub. It reaches VPinFE only through the context
it is handed.

Its account answers its secrets with their values on purpose: core takes them out on the
way to a client, and the tests hold it to that.
"""

from __future__ import annotations

import base64
import struct
import zlib

from fastapi import APIRouter, HTTPException, Response

# The core event it listens to. A name, not an import: the event vocabulary is part of
# the platform ABI the manifest names, and an extension holds no reference into core.
GAME_SELECTED = "game.selected"

# The one kind of card it reads, and the version of it it writes.
CARD = "sample_card"
CARD_VERSION = 1

# What a player's `picture` value names, and so what its service answers with: a PNG, and
# a JPEG when the value ends in `-jpg`. An extension may not import an imaging library it
# does not list, so the PNG is written out here and the JPEGs are held as they are.
COLORS = {"red": (200, 30, 30), "blue": (30, 30, 200)}
JPEGS = {
    "red": base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n"
        "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAUACgDASIAAhEBAxEB/8QAHwAAAQUBAQEB"
        "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
        "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
        "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
        "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
        "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
        "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0"
        "tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDkKKKK8U/T"
        "AooooAKKKKACiiigAooooAKKKKAP/9k="
    ),
    "blue": base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n"
        "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAUACgDASIAAhEBAxEB/8QAHwAAAQUBAQEB"
        "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
        "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
        "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
        "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
        "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
        "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0"
        "tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzCiiiv0w4"
        "wooooAKKKKACiiigAooooAKKKKAP/9k="
    ),
}


def _png(color: tuple[int, int, int], width: int = 40, height: int = 20) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(color) * width
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height))
            + chunk(b"IEND", b""))


def register(ctx) -> None:
    ctx.logger.info("Registering")
    seen: list[dict] = []
    router = APIRouter()
    writing = APIRouter()

    @router.get("/hello")
    def hello() -> dict:
        return {"name": ctx.name, "greeting": ctx.config.get("greeting")}

    @router.get("/seen")
    def how_many() -> dict:
        return {"seen": len(seen)}

    @writing.post("/forget")
    def forget() -> dict:
        seen.clear()
        return {"seen": 0}

    @router.get("/boom")
    def boom() -> dict:
        raise RuntimeError("the fixture was asked to fail")

    @router.get("/accounts/{player_id}/picture")
    def picture(player_id: str) -> Response:
        named = ctx.players.account(player_id).get("picture", "")
        if named == "denied":
            raise HTTPException(403, detail="The service refused")
        if named == "text":
            return Response(b"not an image", media_type="text/plain")
        kind = named.removesuffix("-jpg")
        if kind not in COLORS:
            raise HTTPException(404, detail="No picture yet")
        if named.endswith("-jpg"):
            return Response(JPEGS[kind], media_type="image/jpg")
        return Response(_png(COLORS[kind]), media_type="image/png")

    def read_account(player_id: str) -> dict:
        held = ctx.players.account(player_id)
        return {"fields": [
                    {"key": "handle", "label": "Handle", "type": "string",
                     "value": held.get("handle", "")},
                    {"key": "token", "label": "Token", "type": "secret",
                     "value": held.get("token", "")}],
                "status": "Ready" if held.get("handle") else "No handle",
                "acts": [{"key": "ping", "label": "Ping"}],
                "card": bool(held.get("handle") and held.get("token")),
                "picture": held.get("picture") in COLORS}

    def write_account(player_id: str, values: dict) -> None:
        ctx.players.set_account(player_id, {**ctx.players.account(player_id), **values})

    def ping(player_id: str) -> dict:
        return {"message": f"Pinged {ctx.players.account(player_id).get('handle', '')}"}

    def card(player_id: str) -> dict | None:
        held = ctx.players.account(player_id)
        player = ctx.players.get(player_id) or {}
        if not (held.get("handle") and held.get("token")):
            return None
        return {"card": {"type": CARD, "version": CARD_VERSION, "handle": held["handle"],
                         "token": held["token"], "initials": player.get("initials", "")},
                "filename": f"sample-{held['handle']}"}

    def read_card(found: dict) -> dict:
        if (found or {}).get("version") != CARD_VERSION:
            raise ValueError("Not a card this reads")
        return {"name": "", "initials": str(found.get("initials") or ""),
                "values": {"handle": str(found.get("handle") or ""),
                           "token": str(found.get("token") or "")}}

    def on_selected(**payload) -> None:
        if payload.get("game_id") == "fail":
            raise RuntimeError("the fixture was asked to fail")
        seen.append(payload)
        ctx.events.publish("noticed", game_id=payload.get("game_id"))

    ctx.events.subscribe(GAME_SELECTED, on_selected)
    ctx.add_router(router)
    ctx.add_router(writing)
    ctx.ui.settings([{"key": "greeting", "type": "string", "default": "hello"},
                     {"key": "token", "type": "secret"}])
    ctx.ui.account(read=read_account, write=write_account, acts={"ping": ping}, card=card,
                   read_card=read_card, label="Sample", cards=(CARD,))
