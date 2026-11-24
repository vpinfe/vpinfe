"""Drives the app's HTTP surface and prints the result as JSON.

Run as a subprocess by tests/api/test_api_contract.py, not directly. It needs a
private VPINFE_CONFIG_DIR set before any common/ import, which only a fresh
interpreter can guarantee.
"""

from __future__ import annotations

import json
import sys
import warnings

warnings.filterwarnings("ignore")


def probe() -> dict:
    from nicegui import app as nicegui_app
    from starlette.testclient import TestClient

    import httpapi
    if not any(getattr(r, "path", "") == httpapi.API_PREFIX for r in nicegui_app.routes):
        httpapi.register(nicegui_app)

    client = TestClient(nicegui_app, raise_server_exceptions=False)
    result: dict = {}

    def record(name, response, *, body=True):
        entry = {
            "status": response.status_code,
            "cors": response.headers.get("access-control-allow-origin"),
            "content_type": (response.headers.get("content-type") or "").split(";")[0],
        }
        if body:
            try:
                entry["json"] = response.json()
            except Exception:
                entry["json"] = None
        result[name] = entry

    record("play_state", client.get("/api/v1/play/state",
                                    headers={"Origin": "http://testserver"}))
    record("play_state_same_origin", client.get("/api/v1/play/state"))

    # The Remote Control page writes through the service; the endpoint must report it.
    from common.host import launch_state
    launch_state.set_launching("Medieval Madness (Williams 1997)",
                               source=launch_state.SOURCE_REMOTE, game_id="mm-1")
    record("play_state_launching", client.get("/api/v1/play/state"))
    launch_state.clear()
    record("play_state_cleared", client.get("/api/v1/play/state"))
    record("legacy_remote_launch_gone", client.get("/api/remote-launch"))

    # Games, and the sub-resources that used to be /api/download-table-vpxz.
    listing = client.get("/api/v1/games")
    record("tables_list", listing)
    games = (listing.json() or {}).get("games") or []
    game_id = games[0]["id"] if games else ""

    record("table_get", client.get(f"/api/v1/games/{game_id}"))
    record("table_files", client.get(f"/api/v1/games/{game_id}/tables"))
    archive = client.get(f"/api/v1/games/{game_id}/archive?download_token=abc123")
    result["table_archive"] = {
        "status": archive.status_code,
        "content_type": (archive.headers.get("content-type") or "").split(";")[0],
        "disposition": archive.headers.get("content-disposition"),
        "set_cookie": archive.headers.get("set-cookie"),
        "bytes": len(archive.content),
    }
    def files_for(name):
        hit = [t for t in games if t["name"] == name]
        return client.get(f"/api/v1/games/{hit[0]['id']}/tables") if hit else None

    multi = files_for("Multi File")
    if multi is not None:
        record("multi_file_files", multi)
    mismatch = files_for("Mismatch")
    if mismatch is not None:
        record("mismatch_files", mismatch)

    # Launch, without ever starting anything: the probe's config has no launcher,
    # which is the same refusal a machine without VPX installed would give.
    record("launch_no_launcher", client.post(f"/api/v1/games/{game_id}/launch"))
    record("launch_unknown_file",
           client.post(f"/api/v1/games/{game_id}/launch", json={"file": "nope.vpx"}))
    launch_state.set_launching("Something Else", source=launch_state.SOURCE_FRONTEND)
    record("launch_while_busy", client.post(f"/api/v1/games/{game_id}/launch"))
    launch_state.clear()
    record("launch_unknown_table", client.post("/api/v1/games/no-such-id/launch"))

    record("media_list", client.get(f"/api/v1/games/{game_id}/media"))
    wheel = client.get(f"/api/v1/games/{game_id}/media/wheel")
    result["media_wheel"] = {
        "status": wheel.status_code,
        "content_type": (wheel.headers.get("content-type") or "").split(";")[0],
        "bytes": len(wheel.content),
    }
    record("media_absent", client.get(f"/api/v1/games/{game_id}/media/flyer"))
    record("media_unknown_kind", client.get(f"/api/v1/games/{game_id}/media/poster"))

    record("table_unknown", client.get("/api/v1/games/no-such-id"))
    record("archive_unknown", client.get("/api/v1/games/no-such-id/archive"))
    record("legacy_archive_gone", client.get("/api/download-table-vpxz?name=whatever"))

    # Uploads now live under /api/v1. Walk the same sequence the drag-and-drop
    # client does, so a break in the client's flow shows up here.
    begin = client.post("/api/v1/uploads")
    record("upload_begin", begin)
    upload_id = (begin.json() or {}).get("id", "")

    form = {"relpath": "Example/Example.txt"}
    files = {"file": ("Example.txt", b"hello", "text/plain")}
    record("upload_add_file",
           client.post(f"/api/v1/uploads/{upload_id}/files", data=form, files=files))
    record("upload_summary", client.get(f"/api/v1/uploads/{upload_id}"))
    record("upload_delete", client.delete(f"/api/v1/uploads/{upload_id}"))

    record("upload_unknown_session", client.get("/api/v1/uploads/no-such-session"))
    record("upload_analysis_unknown", client.get("/api/v1/uploads/no-such-session/analysis"))
    record("vps_search", client.get("/api/v1/vps/search?q=&limit=1"))
    record("legacy_upload_gone", client.post("/api/asset-upload/begin"))

    return result


if __name__ == "__main__":
    try:
        print(json.dumps(probe()))
    except Exception as exc:  # surface the real cause to the parent test
        print(json.dumps({"__error__": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
