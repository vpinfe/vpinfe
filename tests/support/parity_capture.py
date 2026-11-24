"""Captures the user-visible behavior of a VPinFE tree, for the 3.0 parity gate.

Run against master it produces the baseline; run against vpinfe-3.0 the gate
compares the two. Every difference must be named in docs/compatibility-3.0.md
or the gate fails - that is the whole mechanism behind "users notice nothing".

Works on both the 2.x and 3.0 layouts, importing from the tree it is run in
(cwd), so the same file can capture either side:

    python tests/support/parity_capture.py --out baseline.json      # from a checkout's root

Not a test; tests/invariants/test_parity.py drives it in a subprocess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

warnings.filterwarnings("ignore")


def _build_fixture_library(root: Path) -> None:
    """A tiny game library exercising the surfaces themes and tools depend on."""
    game = root / "Example Table (Bally 1990)"
    medias = game / "medias"
    medias.mkdir(parents=True)
    (game / "Example Table (Bally 1990).vpx").write_bytes(b"not really a vpx")
    (game / "Example Table (Bally 1990).directb2s").write_bytes(b"b2s")
    (medias / "wheel.png").write_bytes(b"\x89PNG wheel")
    (medias / "bg.png").write_bytes(b"\x89PNG bg")
    (game / "Example Table (Bally 1990).info").write_text(json.dumps({
        "Info": {"Title": "Example Table", "Manufacturer": "Bally", "Year": "1990",
                 "Type": "SS", "VPSId": "vps-example"},
        "VPXFile": {"filename": "Example Table (Bally 1990).vpx", "rom": "exmpl"},
        "User": {"Rating": 3},
        # The `VPinFE` section, which 2.x owns. Present so the gate can see it: three of
        # its keys stopped reaching contract 1 and nothing noticed, because the fixture
        # had no VPinFE section for the comparison to miss.
        "VPinFE": {"deletedNVRamOnClose": False, "altlauncher": "", "alttitle": "",
                   "pluginprofile": "", "altvpsid": ""},
    }), encoding="utf-8")


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Path -> content hash for everything under the library root."""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return snapshot


def _capture_ws_allowlist() -> list[str]:
    from frontend.api import API_ALLOWED_METHODS
    return sorted(API_ALLOWED_METHODS)


def _capture_theme_payload(games_root: Path) -> dict:
    """The shape a theme receives: games_json keys and which media paths resolve.

    Keys only, plus a few stable values - file paths and scan order are
    environment noise, but a missing key breaks every theme the same way.
    """
    # 2.x names these after the machine, which 3.0 calls a game. Both spellings are
    # tried because this same file has to run in a master worktree to produce the
    # baseline the gate compares against.
    try:
        from common.games.game_parser import GameParser  # 3.0
        from frontend.game_state import games_json
    except ImportError:
        from common.tableparser import TableParser as GameParser  # type: ignore[no-redef]  # 2.x
        from frontend.table_state import tables_json as games_json  # type: ignore[no-redef]

    parser: Any = GameParser(str(games_root))
    # Contract 1: what a theme written before 3.0 receives. Capturing the current
    # contract would compare master against a shape no existing theme asks for.
    games: Any = (parser.get_all_games() if hasattr(parser, "get_all_games")
                  else parser.getAllTables())
    try:
        # 3.0 serves the view as entries and takes a contract; master hands its builder
        # the games directly and has no contract to ask for.
        from tests.support.entries import entries_for
        payload = json.loads(games_json(entries_for(games), contract=1))
    except ImportError:
        payload = json.loads(games_json(games))
    entry = payload[0] if payload else {}

    def deep_keys(value, prefix=""):
        """Every key a theme could reach, not just the ones at the top.

        The gate compared top-level keys only, which is how three meta.VPinFE keys
        stopped existing without anything noticing - meta is exactly where the
        vocabulary work did its renaming.
        """
        found = set()
        if isinstance(value, dict):
            for key, item in value.items():
                found.add(f"{prefix}{key}")
                found |= deep_keys(item, f"{prefix}{key}.")
        return found

    return {
        "count": len(payload),
        "keys": sorted(entry.keys()),
        "deep_keys": sorted(deep_keys(entry)),
        "stable_values": {
            key: entry.get(key) for key in ("TableName", "TableManufacturer",
                                            "TableYear", "TableType", "TableRom")
            if key in entry
        },
        "resolved_media_fields": sorted(
            key for key, value in entry.items()
            if key.endswith(("ImagePath", "VideoPath", "AudioPath")) and value
        ),
    }


def _capture_legacy_endpoints() -> dict:
    """The pre-/api/v1 endpoints. On master they answer; on 3.0 they are gone,
    which is a ledger entry, not an accident."""
    from nicegui import app as nicegui_app
    from starlette.testclient import TestClient

    client = TestClient(nicegui_app, raise_server_exceptions=False)
    result = {}
    for name, method, path in (
        ("remote_launch", "GET", "/api/remote-launch"),
        ("upload_begin", "POST", "/api/asset-upload/begin"),
        ("archive_download", "GET", "/api/download-table-vpxz?name=nope"),
    ):
        response = client.request(method, path)
        entry: dict = {"status": response.status_code}
        try:
            body = response.json()
            entry["keys"] = sorted(body.keys()) if isinstance(body, dict) else None
        except Exception:
            entry["keys"] = None
        result[name] = entry
    return result


def capture() -> dict:
    with TemporaryDirectory() as tmp:
        games_root = Path(tmp) / "games"
        games_root.mkdir()
        _build_fixture_library(games_root)

        config_dir = Path(tmp) / "config"
        config_dir.mkdir()
        (config_dir / "vpinfe.ini").write_text(
            f"[Settings]\ngamerootdir = {games_root}\nvpxbinpath = \n",
            encoding="utf-8")
        os.environ["VPINFE_CONFIG_DIR"] = str(config_dir)

        before = _tree_snapshot(games_root)
        theme_payload = _capture_theme_payload(games_root)
        after = _tree_snapshot(games_root)

        return {
            "ws_allowlist": _capture_ws_allowlist(),
            "theme_payload": theme_payload,
            "legacy_endpoints": _capture_legacy_endpoints(),
            "scan_writes": sorted(
                path for path in set(before) | set(after)
                if before.get(path) != after.get(path)
            ),
        }


def main() -> int:
    argp = argparse.ArgumentParser()
    argp.add_argument("--out", help="write JSON here instead of stdout")
    args = argp.parse_args()

    sys.path.insert(0, os.getcwd())
    try:
        payload = json.dumps(capture(), indent=2, sort_keys=True)
    except Exception as exc:  # surface the cause to the driving test
        payload = json.dumps({"__error__": f"{type(exc).__name__}: {exc}"})
        print(payload)
        return 1
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
