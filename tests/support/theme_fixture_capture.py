"""Captures the theme payload as JSON, so the JavaScript tests read what Python built.

The JS harness cannot import Python, and hand-written JS fixtures drift from the builder
silently - which is how a media key lookup shipped broken. So the payload is captured
here and committed; `tests/theming/test_theme_fixtures.py` fails when the committed copy stops
matching, and regenerating it makes the JS tests run against the new shape.

Not a test. Regenerate deliberately:

    python tests/support/theme_fixture_capture.py

Absolute paths are rewritten to a fixed root, because a temp directory would change the
fixture on every run and the URL builder only reads the last few segments anyway.
"""

from __future__ import annotations

import json
import sys
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from common.games.info_file import INFO_SCHEMA
from tests.support.library import write_game

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "theme_payload.json"

# What the captured paths pretend to live under. The JS side asserts against this.
STABLE_ROOT = "/library"

# `created_at` is a stat of the folder the capture just made, so it is a different value
# every run - the same problem the root has, in time rather than in place. Frozen here so
# the fixture stays a witness to the payload's shape rather than to when it was captured.
STABLE_CREATED_AT = "2026-01-01T00:00:00Z"


def _game(root: Path, folder: str, *, info: dict,
          medias: dict[str, bytes] | None = None,
          root_files: dict[str, bytes] | None = None) -> None:
    write_game(root, folder, info=info, medias=medias, files=root_files)


def build_library(root: Path) -> None:
    """Four games, each covering a case the theme surface has to get right."""
    # 1. The ordinary case: media under medias/, image and video for the same kind so
    #    the image-vs-video priority choice has something to choose between.
    _game(root, "Attack from Mars (Bally 1995)",
          info={"Info": {"Title": "Attack from Mars", "Manufacturer": "Bally",
                         "Year": "1995", "Type": "SS", "VPSId": "vps-afm"},
                "User": {"Rating": 4},
                "vpinfe": {"game_id": "afm0000001", "schema": INFO_SCHEMA,
                           "default_table": "afmtable01"},
                "tables": {"afmtable01": {"id": "afmtable01",
                                          "filename": "Attack from Mars (Bally 1995).vpx",
                                          "rom": "afm_113b"}}},
          medias={"wheel.png": b"\x89PNG wheel", "bg.png": b"\x89PNG bg",
                  "table.png": b"\x89PNG playfield", "table.mp4": b"\x00\x00mp4",
                  "dmd.png": b"\x89PNG dmd", "realdmd.png": b"\x89PNG realdmd",
                  "realdmd-color.png": b"\x89PNG realdmd color",
                  "audio.mp3": b"ID3audio"})

    # 2. Media at the folder root rather than under medias/ - the fallback tier, and a
    #    different branch of the path-to-URL builder.
    _game(root, "Congo (Williams 1995)",
          info={"Info": {"Title": "Congo", "Manufacturer": "Williams",
                         "Year": "1995", "Type": "SS", "VPSId": "vps-congo"},
                "User": {"Rating": 0},
                "vpinfe": {"game_id": "cng0000001", "schema": INFO_SCHEMA,
                           "default_table": "cngtable01"},
                "tables": {"cngtable01": {"id": "cngtable01",
                                          "filename": "Congo (Williams 1995).vpx"}}},
          root_files={"wheel.png": b"\x89PNG wheel at root"})

    # 3. A wheel set: media one level deeper than medias/, which is the case the URL
    #    builder needed a special branch for.
    _game(root, "Medieval Madness (Williams 1997)",
          info={"Info": {"Title": "Medieval Madness", "Manufacturer": "Williams",
                         "Year": "1997", "Type": "SS", "VPSId": "vps-mm"},
                "User": {"Rating": 5},
                "vpinfe": {"game_id": "mm00000001", "schema": INFO_SCHEMA,
                           "default_table": "mmtable001"},
                "tables": {"mmtable001": {"id": "mmtable001",
                                          "filename": "Medieval Madness (Williams 1997).vpx"}}},
          medias={"wheels/monochrome/wheel.png": b"\x89PNG set wheel",
                  "bg.png": b"\x89PNG bg"})

    # 4. No media at all, and no tables section either - the folder a metadata build has
    #    never touched. Every lookup has to answer "missing" rather than undefined, and
    #    the entry still has to exist.
    _game(root, "Bare Table (Gottlieb 1980)",
          info={"Info": {"Title": "Bare Table", "Manufacturer": "Gottlieb",
                         "Year": "1980", "Type": "EM", "VPSId": ""},
                "User": {"Rating": 0},
                "vpinfe": {"game_id": "bar0000001", "schema": INFO_SCHEMA}})


def _stabilize(value, real_root: str):
    """Swap the temp directory for a fixed root, and forward-slash what follows it.

    Separators have to be normalized here or the fixture becomes platform-specific:
    a Windows capture writes /library\\Game\\medias\\wheel.png against a committed
    /library/Game/medias/wheel.png, and the check fails on a difference that has
    nothing to do with the payload's shape. Only strings holding the root are
    touched, so anything else keeps its backslashes.
    """
    if isinstance(value, str):
        root = real_root.replace("\\", "/")
        forward = value.replace("\\", "/")
        return forward.replace(root, STABLE_ROOT) if root in forward else value
    if isinstance(value, list):
        return [_stabilize(item, real_root) for item in value]
    if isinstance(value, dict):
        return {key: STABLE_CREATED_AT if key == "created_at" and item else
                _stabilize(item, real_root)
                for key, item in value.items()}
    return value


def _config():
    """An ini selecting the wheel set.

    Sets are opt-in, so without one active the wheel-set folder is invisible - and that
    folder is the case the path-to-URL builder needed its own branch for. The parser only
    reads active sets when it is given a config, so this goes through the real path
    rather than the module-level override.
    """
    parser = ConfigParser()
    parser.read_dict({"Media": {"wheelset": "monochrome"}})
    return parser


def capture() -> dict:
    from common.games.game_parser import GameParser
    from frontend.game_state import games_json

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        build_library(root)
        parser = GameParser(str(root), _config())
        games = parser.get_all_games()

        view: Any
        try:
            from tests.support.entries import entries_for
            view = entries_for(games)
        except ImportError:      # pre-collections layout
            view = games

        payload = {
            "contract1": json.loads(games_json(view, contract=1)),
            "contract2": json.loads(games_json(view, contract=2)),
        }
        return _stabilize(payload, str(root))


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(capture(), indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
