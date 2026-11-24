"""Taking 3.0's state back off an install.

The round trip is the real test: a 2.x install, migrated, reset, and the `.info` files
byte-identical to what went in. The second one matters more - running the migrations
again and asserting every one did work is what keeps the marker list honest, because a
migration whose marker nobody recorded here would report success while doing nothing.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

from common.config.config_store import ConfigStore
from common.devices import install_setup
from common.devices.device_registry import DeviceRegistry
from common.games import library_discovery, revert_3x
from common.games.collection_migration import ensure_last_played, ensure_order_direction
from common.games.collection_store import CollectionStore
from common.games.game_identity import ensure_unique_ids, game_id
from common.games.game_parser import GameParser
from common.games.info_migration import schema_of
from common.games.table_identity import ensure_unique_table_ids
from common.games.tables import TABLES_KEY, table_id
from tests.support.browser_session import free_port
from tests.support.library import TempTree, write_game
from tests.support.saved import saved_record

LEGACY_INFO = {
    "Info": {"Title": "Dr. Dude", "VPSId": "vps-dude", "Rom": "dd_l2",
             "Authors": "someone"},
    "User": {"Rating": 4, "StartCount": 12, "Tags": ["fast"]},
    "VPXFile": {"filename": "Dr. Dude.vpx", "filehash": "abc123", "rom": "dd_l2"},
    "Medias": {"wheel": {"Source": "user"}},
}

LEGACY_INI = """[Settings]
gamerootdir = {games}
"""

LEGACY_COLLECTIONS = """[Favorites]
vpsids = vps-dude,vps-taxi

[Recent]
type = filter
sort_by = LastRun
order_by = Ascending
"""


def legacy_info(title: str, vps_id: str) -> dict:
    """One `.info` in the shape 2.x wrote, with nothing 3.0 stamps on it."""
    info = json.loads(json.dumps(LEGACY_INFO))
    info["Info"]["Title"] = title
    info["Info"]["VPSId"] = vps_id
    info["VPXFile"]["filename"] = f"{title}.vpx"
    return info


def unversioned_count(games_root: Path) -> int:
    return sum(1 for path in games_root.glob("*/*.info")
               if schema_of(json.loads(path.read_text(encoding="utf-8"))) is None)


def table_id_count(games_root: Path) -> int:
    """How many tables across the library carry an id. A 2.x `.info` has no tables
    section at all, so this is zero until the backfill has run."""
    return sum(
        1
        for path in games_root.glob("*/*.info")
        for entry in (saved_record(path).get(TABLES_KEY)
                      or {}).values()
        if isinstance(entry, dict) and table_id(entry))


def run_startup_migrations(games_root: Path, config_dir: Path) -> dict:
    """The one-time conversions a start performs, in main.py's order, and what each did.

    Every value is a count or a flag of work actually done. A migration whose marker is
    already set answers zero, so this is what turns "the reset cleared every marker"
    into an assertion instead of a claim.
    """
    work: dict[str, int | bool] = {}

    had_json = (config_dir / "vpinfe.json").exists()
    ConfigStore(str(config_dir / "vpinfe.ini"))
    work["settings_converted"] = not had_json and (config_dir / "vpinfe.json").exists()

    registry = DeviceRegistry(config_dir / "state" / "devices.json")
    work["install_id_minted"] = not registry.self_id()
    registry.ensure_self()

    unversioned = unversioned_count(games_root)
    table_ids = table_id_count(games_root)
    games = GameParser(str(games_root)).get_all_games()
    work["game_ids_minted"] = sum(1 for game in games if not game_id(game))
    ensure_unique_ids(games)
    ensure_unique_table_ids(games)
    work["info_files_migrated"] = unversioned - unversioned_count(games_root)
    work["table_ids_minted"] = table_id_count(games_root) - table_ids

    collections = CollectionStore(str(config_dir / "collections.json"))
    work["members_rekeyed"] = collections.migrate_membership_to_game_ids(games)
    work["last_played_seeded"] = ensure_last_played(collections)
    # Not work a 2.x start does: an ini already holds the direction 2.x showed.
    ensure_order_direction(collections)
    return work


class StubInstance(HTTPServer):
    """Something answering on the HTTP port. `name` is what its discovery document says,
    so a test can be a VPinFE or can be whatever else grabbed the port first."""

    def __init__(self, name: str | None, delay: float = 0.0):
        self.name = name
        self.delay = delay
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:                                   # noqa: N802 - BaseHTTPRequestHandler
        server = cast(StubInstance, self.server)
        name = server.name
        time.sleep(server.delay)
        body = b"not json" if name is None else json.dumps({"name": name}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


class RevertTestCase(TempTree):
    """A 2.x install: a library of unversioned `.info` files and an ini pair beside it."""

    GAMES = (("Dr. Dude", "vps-dude"), ("Taxi", "vps-taxi"), ("Whirlwind", "vps-whirl"))

    def setUp(self) -> None:
        super().setUp()
        self.games_root = self.root / "tables"
        self.config_dir = self.root / "config"
        self.games_root.mkdir()
        self.config_dir.mkdir()
        self.closed_port = free_port()
        for title, vps_id in self.GAMES:
            write_game(self.games_root, title, info=legacy_info(title, vps_id))
        (self.config_dir / "vpinfe.ini").write_text(
            LEGACY_INI.format(games=self.games_root), encoding="utf-8")
        (self.config_dir / "collections.ini").write_text(LEGACY_COLLECTIONS,
                                                         encoding="utf-8")
        self.original = self._library_bytes()

    def _library_bytes(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in self.games_root.glob("*/*.info")}

    def _backup_copies(self) -> list[str]:
        return sorted(path.name for path in self.root.rglob("*")
                      if ".vpinfe-" in path.name or path.name.startswith(".vpinfe_write_"))

    def _reset(self, **kwargs) -> dict:
        kwargs.setdefault("http_port", self.closed_port)
        kwargs.setdefault("state_dir", self.config_dir / "state")
        return revert_3x.reset(self.games_root, self.config_dir, **kwargs)

    def _say_listening_on(self, port: int) -> None:
        DeviceRegistry(self.config_dir / "state" / "devices.json").set_self(port=port)

    def _migrate(self) -> dict:
        return run_startup_migrations(self.games_root, self.config_dir)


class RoundTripTests(RevertTestCase):
    def test_a_migrated_install_comes_back_exactly_as_it_went_in(self):
        self._migrate()
        self.assertNotEqual(self._library_bytes(), self.original)

        self._reset()

        self.assertEqual(self._library_bytes(), self.original)
        self.assertEqual(self._backup_copies(), [])
        self.assertEqual(sorted(p.name for p in self.config_dir.rglob("*") if p.is_file()),
                         ["collections.ini", "vpinfe.ini"])

    def test_every_migration_runs_again_after_a_reset(self):
        """The point of the whole exercise. A marker the reset misses makes the
        migration behind it a silent no-op, and a second pass is the only thing that
        can tell that apart from a pass that worked."""
        first = self._migrate()
        self.assertTrue(all(first.values()), first)

        self._reset()
        second = self._migrate()

        self.assertEqual(second, first)

    def test_a_2x_sort_keeps_its_direction_through_a_start(self):
        self._migrate()

        store = CollectionStore(str(self.config_dir / "collections.json"))
        self.assertEqual(store.get_order("Recent")["direction"], "asc")

    def test_a_second_migration_without_a_reset_does_nothing(self):
        """The defect this tool exists for, asserted so the test above cannot pass by
        accident: without the reset, every one of those counts is zero."""
        self._migrate()

        self.assertFalse(any(self._migrate().values()))

    def test_the_2x_files_themselves_are_never_written(self):
        self._migrate()
        ini = (self.config_dir / "vpinfe.ini").read_bytes()
        collections = (self.config_dir / "collections.ini").read_bytes()

        self._reset()

        self.assertEqual((self.config_dir / "vpinfe.ini").read_bytes(), ini)
        self.assertEqual((self.config_dir / "collections.ini").read_bytes(), collections)


class ConfigTests(RevertTestCase):
    def test_a_hand_made_repro_file_is_not_a_backup(self):
        """The reset names what it deletes. Anything shaped as "delete what looks like a
        backup" eats the repro copies kept beside the real files."""
        self._migrate()
        for name in ("collections.json.bak-repro3", "vpinfe.json.bak-repro"):
            (self.config_dir / name).write_text("mine", encoding="utf-8")

        self._reset()

        self.assertTrue((self.config_dir / "collections.json.bak-repro3").exists())
        self.assertTrue((self.config_dir / "vpinfe.json.bak-repro").exists())

    def test_the_2x_files_of_other_features_are_left_alone(self):
        for name in ("roms.json", "vpsdb.json"):
            (self.config_dir / name).write_text("{}", encoding="utf-8")
        for name in ("themes", "collection_icons", "plugin_profiles", "cache", "assets"):
            (self.config_dir / name).mkdir()
        self._migrate()

        self._reset()

        for name in ("roms.json", "vpsdb.json", "themes", "collection_icons",
                     "plugin_profiles", "cache", "assets"):
            self.assertTrue((self.config_dir / name).exists(), name)

    def test_3x_only_files_go_whether_or_not_a_migration_wrote_them(self):
        self._migrate()
        (self.config_dir / "state").mkdir(exist_ok=True)
        (self.config_dir / "state" / "devices.json").write_text("{}", encoding="utf-8")
        (self.config_dir / "manager-ui-state.json").write_text("{}", encoding="utf-8")

        result = self._reset()

        self.assertIn("devices.json", result["config_removed"])
        self.assertIn("manager-ui-state.json", result["config_removed"])

    def test_what_only_3x_kept_goes_from_the_state_folder(self):
        self._migrate()
        state = self.config_dir / "state"
        state.mkdir(exist_ok=True)
        for name in ("dismissed.json", "arrivals.json", "draw_order.json"):
            (state / name).write_text("{}", encoding="utf-8")
            (self.config_dir / name).write_text("{}", encoding="utf-8")

        result = self._reset()

        for name in ("dismissed.json", "arrivals.json", "draw_order.json"):
            self.assertFalse((state / name).exists())
            self.assertIn(name, result["config_removed"])
            self.assertTrue((self.config_dir / name).exists())

    def test_the_marks_are_copied_aside_before_they_go(self):
        """Wanted and Ignored are entered by hand and nothing can rebuild them, so the reset
        removes marks.json from the state folder but leaves a copy beside it, which the next
        reset does not sweep."""
        self._migrate()
        state = self.config_dir / "state"
        state.mkdir(exist_ok=True)
        (state / "marks.json").write_text('{"schema": 1, "marks": []}', encoding="utf-8")

        result = self._reset()

        self.assertFalse((state / "marks.json").exists())
        self.assertIn("marks.json", result["config_removed"])
        kept = list(state.glob("marks.json.vpinfe-*"))
        self.assertEqual(len(kept), 1, f"expected one copy, found {kept}")
        self.assertIn(kept[0].name, result["config_kept"])

    def _list_a_mobile_device(self) -> Path:
        path = self.config_dir / "vpinfe.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mobile_devices"] = [{"id": "Pppp444444", "name": "iPhone",
                                   "address": "192.168.1.50", "port": 2112}]
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_the_settings_are_copied_aside_when_they_list_a_mobile_device(self):
        """A mobile device is entered by hand and nothing re-announces it, so the reset is
        the one place it can be lost for good. vpinfe.json still goes - 2.x does not read
        it - but a copy stays behind, and that copy is not itself swept."""
        self._migrate()
        path = self._list_a_mobile_device()

        result = self._reset()

        self.assertIn("vpinfe.json", result["config_removed"])
        self.assertFalse(path.exists())
        kept = list(self.config_dir.glob("vpinfe.json.vpinfe-*"))
        self.assertEqual(len(kept), 1, f"expected one copy, found {kept}")
        self.assertIn(kept[0].name, result["config_kept"])
        saved = json.loads(kept[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["mobile_devices"][0]["address"], "192.168.1.50",
                         "the device is recoverable by hand")

    def test_settings_listing_no_device_leave_no_copy(self):
        self._migrate()

        result = self._reset()

        self.assertIn("vpinfe.json", result["config_removed"])
        self.assertEqual(result["config_kept"], [])

    def test_a_second_reset_does_not_sweep_the_copy_the_first_kept(self):
        self._migrate()
        self._list_a_mobile_device()
        first = self._reset()["config_kept"]
        self.assertTrue(first)
        self._migrate()

        self._reset()

        for name in first:
            self.assertTrue((self.config_dir / name).exists(), name)

    def test_the_device_registry_goes_without_a_copy(self):
        """It holds only what the next start mints or hears again."""
        self._migrate()
        state = self.config_dir / "state"
        self.assertTrue((state / "devices.json").exists())

        result = self._reset()

        self.assertIn("devices.json", result["config_removed"])
        self.assertNotIn("devices.json", " ".join(result["config_kept"]))
        self.assertEqual(list(state.glob("devices.json*")), [])

    def test_the_roster_is_copied_aside_before_it_goes(self):
        self._migrate()
        (self.config_dir / "players.json").write_text(
            json.dumps({"schema": 2, "players": [
                {"id": "Pppp111111", "name": "", "initials": "OWN", "owner": True},
                {"id": "Pppp222222", "name": "Alex", "initials": "ABC", "owner": False}]}),
            encoding="utf-8")

        result = self._reset()

        self.assertIn("players.json", result["config_removed"])
        self.assertFalse((self.config_dir / "players.json").exists())
        kept = list(self.config_dir.glob("players.json.vpinfe-*"))
        self.assertEqual(len(kept), 1, f"expected one copy, found {kept}")
        self.assertIn(kept[0].name, result["config_kept"])
        saved = json.loads(kept[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["players"][1]["name"], "Alex")

    def test_a_half_written_temp_file_is_swept_up(self):
        self._migrate()
        (self.config_dir / ".vpinfe_write_abc.tmp").write_text("", encoding="utf-8")
        (self.games_root / "Taxi" / ".vpinfe_write_xyz.tmp").write_text("", encoding="utf-8")

        self._reset()

        self.assertEqual(self._backup_copies(), [])

    def test_an_install_with_no_ini_comes_back_as_a_fresh_install(self):
        """Not a keep-list. An install that never had 2.x settings has nothing to go
        back to, and first-run defaults are the only baseline that occurs in the wild."""
        (self.config_dir / "vpinfe.ini").unlink()
        (self.config_dir / "collections.ini").unlink()
        self._migrate()

        result = self._reset()

        self.assertEqual(result["end_state"], revert_3x.FRESH_INSTALL)
        self.assertFalse((self.config_dir / "vpinfe.json").exists())
        self.assertTrue(
            install_setup.pending(ConfigStore(str(self.config_dir / "vpinfe.ini"))))

    def test_an_install_with_an_ini_reports_that_it_kept_it(self):
        self._migrate()

        self.assertEqual(self._reset()["end_state"], revert_3x.RESTORED_FROM_2X)


class LibraryTests(RevertTestCase):
    def test_a_game_3x_added_loses_the_info_3x_made_for_it(self):
        """And it is the id backfill that made it, which writes a game_id and no schema
        stamp - so "is it stamped" is the wrong question to ask of the file."""
        self._migrate()
        write_game(self.games_root, "New Game", info=None)
        ensure_unique_ids(GameParser(str(self.games_root)).get_all_games())
        made = self.games_root / "New Game" / "New Game.info"
        self.assertEqual(json.loads(made.read_text(encoding="utf-8")).keys(), {"vpinfe"})

        result = self._reset()

        self.assertFalse(made.exists())
        self.assertEqual(result["deleted_info"], ["New Game"])

    def test_a_2x_file_nothing_has_upgraded_yet_is_left_alone(self):
        """It has no backup either, but 3.0 never wrote it - so there is nothing of
        3.0's in that folder to take out, and deleting it would destroy 2.x data."""
        untouched = write_game(self.games_root, "Fresh Import",
                               info=legacy_info("Fresh Import", "vps-fresh"))
        before = (untouched / "Fresh Import.info").read_bytes()

        result = self._reset()

        self.assertEqual((untouched / "Fresh Import.info").read_bytes(), before)
        self.assertEqual(result["deleted_info"], [])

    def _two_table_game(self) -> Path:
        folder = self.games_root / "Taxi"
        (folder / "Taxi Night.vpx").write_bytes(b"night")
        (folder / "Taxi Day.vpx").write_bytes(b"day")
        return folder

    def _discover(self) -> None:
        library_discovery.discover(GameParser(str(self.games_root)).get_all_games())

    def test_a_table_file_3x_made_goes_with_the_reset(self):
        folder = self._two_table_game()
        self._migrate()
        self._discover()
        self.assertTrue((folder / "Taxi Night.info").exists())

        self._reset()

        self.assertEqual(sorted(path.name for path in folder.glob("*.info")), ["Taxi.info"])
        self.assertEqual(self.original["Taxi.info"], (folder / "Taxi.info").read_bytes())
        self.assertEqual([path for path in folder.iterdir() if ".vpinfe-" in path.name], [])

    def test_a_table_file_another_program_wrote_comes_back_as_it_was(self):
        folder = self._two_table_game()
        foreign = json.dumps({"User": {"StartCount": 4}}).encode()
        (folder / "Taxi Night.info").write_bytes(foreign)
        self._migrate()
        self._discover()
        self.assertNotEqual(foreign, (folder / "Taxi Night.info").read_bytes())

        self._reset()

        self.assertEqual(foreign, (folder / "Taxi Night.info").read_bytes())

    def test_a_file_too_broken_to_read_is_left_alone(self):
        self._migrate()
        broken = self.games_root / "Taxi" / "Taxi.info"
        for path in broken.parent.glob("*.vpinfe-*"):
            path.unlink()
        broken.write_text("{ not json", encoding="utf-8")

        result = self._reset()

        self.assertEqual(broken.read_text(encoding="utf-8"), "{ not json")
        self.assertEqual(result["deleted_info"], [])

    def test_config_only_leaves_every_info_where_it_is(self):
        self._migrate()
        migrated = self._library_bytes()

        self._reset(config_only=True)

        self.assertEqual(self._library_bytes(), migrated)
        self.assertFalse((self.config_dir / "vpinfe.json").exists())

    def test_the_schema_2_backup_a_restore_left_behind_is_not_restored(self):
        """`restorable_backup` takes anything at or below the schema it is given, so the
        default would put back a schema 2 file rather than the 2.x original under it. The
        restore in the middle carries the game's id, so the bytes differ by that alone."""
        self._migrate()
        from common.games.info_maintenance import restore_library
        restore_library(self.games_root)          # leaves a schema 2 copy in each folder
        self._migrate()

        self._reset()

        def without_id(raw):
            data = json.loads(raw)
            data.pop("vpinfe", None)
            return data

        after = self._library_bytes()
        self.assertEqual(set(after), set(self.original))
        for name, raw in after.items():
            self.assertEqual(without_id(raw), without_id(self.original[name]), name)


class DryRunTests(RevertTestCase):
    def test_it_changes_nothing(self):
        self._migrate()
        write_game(self.games_root, "New Game", info=None)
        ensure_unique_ids(GameParser(str(self.games_root)).get_all_games())
        before = self._library_bytes()
        config_before = sorted(p.name for p in self.config_dir.iterdir())

        self._reset(dry_run=True)

        self.assertEqual(self._library_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.config_dir.iterdir()), config_before)

    def test_it_names_the_same_work_the_run_does(self):
        self._migrate()
        write_game(self.games_root, "New Game", info=None)
        ensure_unique_ids(GameParser(str(self.games_root)).get_all_games())

        planned = self._reset(dry_run=True)
        done = self._reset()

        self.assertEqual(planned["deleted_info"], ["New Game"])
        self.assertEqual(planned["deleted_info"], done["deleted_info"])
        self.assertEqual(planned["config_removed"], done["config_removed"])
        self.assertEqual(planned["restored"], done["restored"])

    def test_it_is_allowed_while_vpinfe_is_running(self):
        """It writes nothing, so refusing would be friction with nothing behind it. It
        does say the real run will refuse, which is what the tester needs to know."""
        self._migrate()
        instance = StubInstance("VPinFE")
        self.addCleanup(instance.stop)

        result = self._reset(dry_run=True, http_port=instance.port)

        self.assertTrue(result["instance_running"])
        self.assertTrue((self.config_dir / "vpinfe.json").exists())


class RunningInstanceTests(RevertTestCase):
    def test_it_refuses_while_vpinfe_is_running(self):
        """Its failure mode is silence: a reset that looks like it worked and is then
        written straight back over by the live process, markers and all."""
        self._migrate()
        instance = StubInstance("VPinFE")
        self.addCleanup(instance.stop)
        migrated = self._library_bytes()

        with self.assertRaises(revert_3x.InstanceRunningError):
            self._reset(http_port=instance.port)

        self.assertTrue((self.config_dir / "vpinfe.json").exists())
        self.assertEqual(self._library_bytes(), migrated)

    def test_it_finds_an_instance_whose_port_was_changed_after_it_started(self):
        """The setting says where the next start listens. The run still answers where it
        started, and a reset that only looked at the setting would write under it."""
        self._migrate()
        instance = StubInstance("VPinFE")
        self.addCleanup(instance.stop)
        self._say_listening_on(instance.port)

        with self.assertRaises(revert_3x.InstanceRunningError):
            self._reset(http_port=self.closed_port)

    def test_a_port_left_behind_by_a_run_that_is_gone_does_not_block_a_reset(self):
        self._migrate()
        self._say_listening_on(self.closed_port)

        result = self._reset()

        self.assertFalse(result["instance_running"])

    def test_something_else_on_that_port_does_not_block_a_reset(self):
        """The port is configurable and ordinary. Refusing on a bare TCP connect would
        make an unrelated server on 8001 look like a running VPinFE."""
        self._migrate()
        for name in ("Grafana", None):
            with self.subTest(answers=name):
                instance = StubInstance(name)
                self.addCleanup(instance.stop)
                self.assertFalse(revert_3x.running_instance(instance.port))

    def test_a_listener_that_does_not_speak_http_does_not_block_a_reset(self):
        """It is not an answer in any shape, so it is no instance. Raising out of the probe
        would stop the reset on a port that something unrelated happens to hold."""
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()

        def answer_with_noise() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.recv(1024)
                connection.sendall(b"\x00\x01 not http at all\r\n\r\n")

        thread = threading.Thread(target=answer_with_noise, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)

        self.assertFalse(revert_3x.running_instance(listener.getsockname()[1]))

    def test_a_slow_answer_is_still_a_running_instance(self):
        """A machine under load answers late. Taking late for none would let the reset
        run under a live instance, which writes it straight back."""
        instance = StubInstance("VPinFE", delay=2.0)
        self.addCleanup(instance.stop)
        self.assertTrue(revert_3x.running_instance(instance.port))

    def test_a_port_nobody_is_listening_on_is_not_a_running_instance(self):
        self.assertFalse(revert_3x.running_instance(self.closed_port))


class LiveRefusalTests(unittest.TestCase):
    """The probe against a real VPinFE rather than a stand-in.

    The stub above proves what the code does with an answer; this proves the answer is
    the one a running instance actually gives, which is the half that goes stale when
    the instance moves or its discovery document changes.
    """

    BOOTS_PER_TEST = ("It checks the port is free once its instance has stopped, so the "
                      "test owns the instance from start to stop.")

    def test_a_real_instance_is_seen_on_its_hub_port(self):
        from tempfile import TemporaryDirectory

        from tests.support.live_instance import LiveInstance

        with TemporaryDirectory() as games:
            write_game(games, "Dr. Dude", info=legacy_info("Dr. Dude", "vps-dude"))
            with LiveInstance(Path(games)) as live:
                live.wait_for_api()
                port = live.ports["manager"]
                self.assertTrue(revert_3x.running_instance(port))
                with self.assertRaises(revert_3x.InstanceRunningError):
                    revert_3x.reset(games, live.config_dir, http_port=port,
                                      state_dir=Path(live.config_dir) / "state")
        self.assertFalse(revert_3x.running_instance(port))


if __name__ == "__main__":
    unittest.main()
