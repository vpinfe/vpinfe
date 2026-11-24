"""Building a game library to test against, once.

Thirty-six `setUp` methods opened a temporary directory and registered its cleanup, and
thirteen files wrote a `.info` by hand - so the shape of a game folder was encoded
independently in thirteen places, and a change to it had to be found in all of them.

Two things here. `TempTree` is the setUp; `write_game` is the folder. Neither hides
anything a test needs to be explicit about: what a test cares about it still passes in,
and what it does not care about has one definition instead of thirteen.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.devices import device_registry
from common.games import info_codec
from common.games.game import Game
from common.games.info_file import INFO_SCHEMA


def own_registry(case: unittest.TestCase, path: Path | str) -> device_registry.DeviceRegistry:
    """Point this install's device registry at `path` for the test. The process holds one,
    and without this every test reads and writes the same self row.

    The mobile devices live in the `vpinfe.json` beside `path`, and the `/config` routes
    write through `get_ini_config`, so that is pointed at the same file: a route and the
    registry must see one list.
    """
    from unittest.mock import patch

    from common.settings import config_service

    device_registry.reset_for_tests(Path(path))
    case.addCleanup(device_registry.reset_for_tests)
    registry = device_registry.get_device_registry()
    patcher = patch.object(config_service, "get_ini_config", lambda: registry.config())
    patcher.start()
    case.addCleanup(patcher.stop)
    return registry


def own_locations(case: unittest.TestCase, folder: Path | str):
    """This install's locations, kept in a config folder of the test's own.

    The `/config` routes write through `get_ini_config`, so that is pointed at the same
    `vpinfe.json` the store reads - a route and the store must see one list.
    """
    from unittest.mock import patch

    from common.config.config_store import ConfigStore
    from common.games import library_folders
    from common.settings import config_service

    kept = Path(folder)
    kept.mkdir(parents=True, exist_ok=True)
    store = library_folders.LocationStore(kept)
    for target, replacement in (
            (library_folders, ("get_location_store", lambda: store)),
            (config_service, ("get_ini_config", lambda: ConfigStore(kept / "vpinfe.json")))):
        patcher = patch.object(target, replacement[0], replacement[1])
        patcher.start()
        case.addCleanup(patcher.stop)
    return store


def own_launchers(case: unittest.TestCase, folder: Path | str):
    """This install's launchers, kept in a config folder of the test's own.

    The `/config` routes write through `get_ini_config`, so that is pointed at the same
    `vpinfe.json` the store reads. Starts with none, so a count in a test is its own.
    """
    from unittest.mock import patch

    from common.config.config_store import ConfigStore
    from common.games import launchers
    from common.settings import config_service

    kept = Path(folder)
    kept.mkdir(parents=True, exist_ok=True)
    store = launchers.LauncherStore(kept)
    store.save([], {})
    for target, replacement in (
            (launchers, ("get_launcher_store", lambda: store)),
            (config_service, ("get_ini_config", lambda: ConfigStore(kept / "vpinfe.json")))):
        patcher = patch.object(target, replacement[0], replacement[1])
        patcher.start()
        case.addCleanup(patcher.stop)
    return store


class TempTree(unittest.TestCase):
    """A test case with `self.root`, a temporary directory cleaned up afterwards, and a
    device registry of its own, in a directory beside it, as `self.registry_path`."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        kept = TemporaryDirectory()
        self.addCleanup(kept.cleanup)
        self.registry_path = Path(kept.name) / "devices.json"
        own_registry(self, self.registry_path)


def write_game(root: Path | str, name: str = "Example", *,
               info: dict | None = None,
               vpx: bool = True,
               medias: dict[str, bytes] | None = None,
               files: dict[str, bytes] | None = None) -> Path:
    """One game folder: a `.vpx`, its `.info`, and whatever media it needs.

    `info` is written as given, so a test that cares about the metadata says exactly
    what it wants, except that a record stamped with the current schema is the in-memory
    shape and goes out as the app would save it; omit it for a folder with none, which is
    its own case. `medias` go under `medias/`, `files` anywhere under the folder - the two
    tiers media resolution reads, plus whatever else a game carries.
    """
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    if vpx:
        (folder / f"{name}.vpx").write_bytes(b"not really a vpx")
    if info is not None:
        stamped = info.get("vpinfe", {}).get("schema") == INFO_SCHEMA
        on_disk = info_codec.encode(info) if stamped else info
        (folder / f"{name}.info").write_text(json.dumps(on_disk), encoding="utf-8")
    for rel, data in (files or {}).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for rel, data in (medias or {}).items():
        target = folder / "medias" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return folder


def game_info(title: str = "Example", *, vps_id: str = "vps-1", game_id: str = "",
              tables: dict | None = None, **sections) -> dict:
    """A current-schema record as held in memory, with only the parts a test names."""
    info: dict = {"Info": {"Title": title, "VPSId": vps_id}, "vpinfe": {"schema": INFO_SCHEMA}}
    if game_id:
        info["vpinfe"]["game_id"] = game_id
    if tables is not None:
        info["tables"] = tables
    for section, values in sections.items():
        info.setdefault(section, {}).update(values)
    return info


def fake_game(folder: Path | str, name: str = "Example", *,
              meta: dict | None = None, **extra) -> Game:
    """What `GameParser` hands the rest of the app, without parsing anything.

    Thirteen files built this by hand, so the attribute names a consumer reads were
    written out thirteen times and a rename had to find all of them. Anything a test
    needs beyond the four common attributes it passes as `extra`, which is also how the
    optional `*Exists` flags and image paths get set.

    A real `Game`, not a stand-in: every field is present with its real default, so a
    consumer reading one a test did not think to set gets what the app would give it.
    """
    game = Game(
        full_path_game=str(folder),
        full_path_vpx_file=str(Path(folder) / f"{name}.vpx"),
        game_dir_name=name,
        meta_config=meta if meta is not None else {},
    )
    for key, value in extra.items():
        setattr(game, key, value)
    return game


def add_mobile(registry: device_registry.DeviceRegistry, device_id: str, name: str = "iPhone",
               address: str = "192.168.1.50", port: int = 2112) -> None:
    """List a VPX Mobile device the way `/config/mobile_devices` does, under an id of the
    test's choosing."""
    store = registry.config()
    store.set_list(device_registry.MOBILE_DEVICES_KEY, [
        *store.get_list(device_registry.MOBILE_DEVICES_KEY),
        {"id": device_id, "name": name, "address": address, "port": port}])
    store.save()
