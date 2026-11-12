"""Moving a file between a game and its tables.

What is pinned is what is on disk afterwards: which names exist, which copies Backups holds,
what the ledger says, and that a failure or an Undo leaves the folder as it was found.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from common import service_errors
from common.backups import folder as backup_folder
from common.games import file_move, game_repository, media_ops
from common.host import launch_state
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.saved import saved_record

GAME_ID = "Moved000001"
FOLDER = "Cactus"
NAMES = {"t1": FOLDER, "t2": "Cactus B", "t3": "Cactus C", "t4": "Cactus D"}
DIRECTB2S = ".directb2s"


def tables(*ids: str) -> dict:
    return {key: {"id": key, "filename": f"{NAMES[key]}.vpx"} for key in ids}


class MoveCase(TempTree):
    """A game folder named Cactus, holding four tables: one named like the folder, and B, C
    and D."""

    ids = ("t1", "t2", "t3", "t4")

    def setUp(self) -> None:
        super().setUp()
        self.backups = self.root / "backups"
        self.running = launch_state.LaunchState()
        self.info = game_info("Cactus", game_id=GAME_ID, tables=tables(*self.ids))
        self.info["assets"] = {"Cactus.directb2s": {"source": {"host": "vpinmediadb",
                                                               "hash": "abc"}}}
        self.folder = write_game(self.root / "library", FOLDER, info=self.info, vpx=False,
                                 files={f"{NAMES[key]}.vpx": b"vpx" for key in self.ids})
        game = fake_game(self.folder, FOLDER, meta=self.info)
        for patcher in (
                patch.object(game_repository, "catalog", side_effect=lambda: {GAME_ID: game}),
                patch.object(backup_folder, "current", return_value=self.backups),
                patch.object(media_ops, "reread"),
                patch.object(launch_state, "current",
                             side_effect=lambda: self.running)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, name: str, data: bytes = b"x") -> Path:
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def files(self, extension: str) -> list[str]:
        return sorted(path.relative_to(self.folder).as_posix()
                      for path in self.folder.rglob("*")
                      if path.is_file() and path.suffix.lower() == extension)

    def kept(self) -> list[str]:
        return sorted(path.name.split(".vpinfe-")[0] for path in self.backups.rglob("*")
                      if path.is_file())

    def ledger(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info")["assets"]


class AssetToTablesTests(MoveCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("Cactus.directb2s", b"game")

    def move(self, *ids: str) -> dict:
        return file_move.to_tables(GAME_ID, file_move.ASSET, "backglass", list(ids))

    def test_one_table_takes_the_file_and_the_game_has_none(self) -> None:
        done = self.move("t2")

        self.assertEqual(self.files(DIRECTB2S), ["Cactus B.directb2s"])
        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"game")
        self.assertEqual(done["uses"], ["Cactus B"])
        self.assertFalse(done["stays"])

    def test_the_tables_left_without_say_so_and_the_one_named_like_the_folder_is_among_them(
            self) -> None:
        done = self.move("t2")

        self.assertEqual(sorted(done["lost"]), ["Cactus", "Cactus C", "Cactus D"])

    def test_several_tables_each_get_a_copy_and_the_game_keeps_nothing(self) -> None:
        done = self.move("t2", "t3")

        self.assertEqual(self.files(DIRECTB2S), ["Cactus B.directb2s", "Cactus C.directb2s"])
        self.assertEqual(done["uses"], ["Cactus B", "Cactus C"])
        self.assertEqual(self.kept(), [])

    def test_choosing_every_table_without_its_own_leaves_the_file_where_it_is(self) -> None:
        self.put("Cactus B.directb2s", b"b own")
        done = self.move("t2", "t3", "t4")

        self.assertTrue(done["stays"])
        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])
        self.assertEqual(done["lost"], [])
        self.assertEqual(sorted(done["uses"]), ["Cactus B", "Cactus C", "Cactus D"])

    def test_a_table_that_had_its_own_has_it_kept_in_backups_when_it_goes_back_to_the_game(
            self) -> None:
        self.put("Cactus B.directb2s", b"b own")
        done = self.move("t2", "t3", "t4")

        self.assertEqual(done["replaced"][0]["table"], "Cactus B")
        self.assertEqual(self.kept(), ["Cactus B.directb2s"])
        kept = next(self.backups.rglob("Cactus B.directb2s.vpinfe-*-move"))
        self.assertEqual(kept.read_bytes(), b"b own")
        self.assertIn("assets", kept.relative_to(self.backups).parts)

    def test_a_file_that_leaves_replaces_the_own_file_of_the_table_it_goes_to(self) -> None:
        self.put("Cactus B.directb2s", b"b own")

        self.move("t2")

        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"game")
        self.assertEqual(self.kept(), ["Cactus B.directb2s"])

    def test_choosing_no_table_when_the_file_would_leave_is_refused_and_nothing_moves(
            self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.move()

        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])

    def test_choosing_no_table_when_nothing_would_change_is_refused(self) -> None:
        for name in ("Cactus B", "Cactus C", "Cactus D"):
            self.put(f"{name}.directb2s")

        with self.assertRaises(service_errors.RefusedError):
            self.move()

    def test_the_table_named_like_the_folder_cannot_be_chosen(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.move("t1")

        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])

    def test_a_game_with_no_file_has_nothing_to_move(self) -> None:
        (self.folder / "Cactus.directb2s").unlink()

        with self.assertRaises(service_errors.RefusedError):
            self.move("t2")

    def test_a_table_that_is_not_the_games_is_not_found(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            self.move("nope")

    def test_nothing_moves_while_a_table_of_the_game_plays(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        with self.assertRaises(service_errors.BlockedError):
            self.move("t2")

        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])

    def test_another_game_playing_does_not_stop_it(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id="Other")

        self.move("t2")

        self.assertEqual(self.files(DIRECTB2S), ["Cactus B.directb2s"])

    def test_the_ledger_follows_a_rename(self) -> None:
        self.move("t2")

        self.assertEqual(list(self.ledger()), ["Cactus B.directb2s"])
        self.assertEqual(self.ledger()["Cactus B.directb2s"]["source"]["hash"], "abc")

    def test_the_ledger_is_copied_to_every_table_that_gets_a_copy(self) -> None:
        self.move("t2", "t3")

        self.assertEqual(sorted(self.ledger()), ["Cactus B.directb2s", "Cactus C.directb2s"])

    def test_undo_puts_back_the_game_file_the_replaced_own_file_and_the_ledger(self) -> None:
        self.put("Cactus B.directb2s", b"b own")
        done = self.move("t2")

        file_move.undo(GAME_ID, done["undo"])

        self.assertEqual(self.files(DIRECTB2S), ["Cactus B.directb2s", "Cactus.directb2s"])
        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"b own")
        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"game")
        self.assertEqual(list(self.ledger()), ["Cactus.directb2s"])
        self.assertEqual(self.kept(), [])

    def test_undo_removes_the_copies_it_made(self) -> None:
        done = self.move("t2", "t3")

        file_move.undo(GAME_ID, done["undo"])

        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])

    def test_a_move_is_not_undone_through_another_game(self) -> None:
        done = self.move("t2")

        with self.assertRaises(service_errors.NotFoundError):
            file_move.undo("Other", done["undo"])

        file_move.undo(GAME_ID, done["undo"])

    def test_a_move_is_undone_once(self) -> None:
        done = self.move("t2")
        file_move.undo(GAME_ID, done["undo"])

        with self.assertRaises(service_errors.NotFoundError):
            file_move.undo(GAME_ID, done["undo"])

    def test_undo_waits_while_a_table_plays_and_can_be_tried_again(self) -> None:
        done = self.move("t2")
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        with self.assertRaises(service_errors.BlockedError):
            file_move.undo(GAME_ID, done["undo"])
        self.running = launch_state.LaunchState()
        file_move.undo(GAME_ID, done["undo"])

        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])

    def test_a_copy_that_fails_puts_every_file_back(self) -> None:
        self.put("Cactus B.directb2s", b"b own")

        with patch.object(file_move, "_copy", side_effect=OSError("the share is not answering")), \
                self.assertRaises(service_errors.BlockedError):
            self.move("t2", "t3")

        self.assertEqual(self.files(DIRECTB2S), ["Cactus B.directb2s", "Cactus.directb2s"])
        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"b own")
        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"game")
        self.assertEqual(self.kept(), [])
        self.assertEqual(list(self.ledger()), ["Cactus.directb2s"])

    def test_a_rename_that_fails_puts_every_file_back(self) -> None:
        self.put("Cactus B.directb2s", b"b own")

        with patch.object(file_move, "_rename", side_effect=OSError("in use")), \
                self.assertRaises(service_errors.BlockedError):
            self.move("t2")

        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"b own")
        self.assertEqual(self.files(DIRECTB2S), ["Cactus B.directb2s", "Cactus.directb2s"])


class AssetToGameTests(MoveCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("Cactus B.directb2s", b"b own")

    def move(self, table: str = "t2", kind: str = "backglass") -> dict:
        return file_move.to_game(GAME_ID, file_move.ASSET, kind, table)

    def test_a_table_file_becomes_the_games_when_the_game_has_none(self) -> None:
        (self.folder / "Cactus.directb2s").unlink(missing_ok=True)

        done = self.move()

        self.assertEqual(self.files(DIRECTB2S), ["Cactus.directb2s"])
        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"b own")
        self.assertEqual(done["replaced"], [])
        self.assertEqual(done["changed_for"], [])
        self.assertEqual(sorted(done["uses"]), ["Cactus B", "Cactus C", "Cactus D"])

    def test_the_file_it_replaces_is_kept_and_the_tables_that_read_it_are_named(self) -> None:
        self.put("Cactus.directb2s", b"game")

        done = self.move()

        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"b own")
        self.assertEqual(self.kept(), ["Cactus.directb2s"])
        self.assertEqual(sorted(done["changed_for"]), ["Cactus", "Cactus C", "Cactus D"])

    def test_undo_puts_both_files_back(self) -> None:
        self.put("Cactus.directb2s", b"game")
        done = self.move()

        file_move.undo(GAME_ID, done["undo"])

        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"b own")
        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"game")
        self.assertEqual(self.kept(), [])

    def test_a_script_does_not_go_to_the_game(self) -> None:
        self.put("Cactus B.vbs")

        with self.assertRaises(service_errors.RefusedError):
            self.move(kind="script")

        self.assertTrue((self.folder / "Cactus B.vbs").is_file())

    def test_a_point_of_view_does_not_go_to_the_game(self) -> None:
        self.put("Cactus B.pov")

        with self.assertRaises(service_errors.RefusedError):
            self.move(kind="pov")

    def test_the_table_named_like_the_folder_already_uses_the_games_file(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.move("t1")

    def test_a_table_with_no_file_of_its_own_has_nothing_to_move(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.move("t3")

    def test_nothing_moves_while_a_table_of_the_game_plays(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        with self.assertRaises(service_errors.BlockedError):
            self.move()

    def test_a_failure_puts_both_files_back(self) -> None:
        self.put("Cactus.directb2s", b"game")

        with patch.object(file_move, "_rename", side_effect=OSError("in use")), \
                self.assertRaises(service_errors.BlockedError):
            self.move()

        self.assertEqual((self.folder / "Cactus B.directb2s").read_bytes(), b"b own")
        self.assertEqual((self.folder / "Cactus.directb2s").read_bytes(), b"game")
        self.assertEqual(self.kept(), [])


class PointOfViewTests(MoveCase):
    """A point of view is read under the table's own name only, so a game's is the table
    named like the folder's and giving it away takes it from that table."""

    def setUp(self) -> None:
        super().setUp()
        self.put("Cactus.pov", b"pov")

    def test_it_goes_to_the_table_chosen_and_the_folder_named_table_loses_it(self) -> None:
        done = file_move.to_tables(GAME_ID, file_move.ASSET, "pov", ["t2"])

        self.assertEqual(self.files(".pov"), ["Cactus B.pov"])
        self.assertEqual(done["lost"], ["Cactus"])

    def test_it_never_stays_on_the_game(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            file_move.to_tables(GAME_ID, file_move.ASSET, "pov", [])

    def test_it_can_be_copied_to_several(self) -> None:
        file_move.to_tables(GAME_ID, file_move.ASSET, "pov", ["t2", "t3"])

        self.assertEqual(self.files(".pov"), ["Cactus B.pov", "Cactus C.pov"])


class ScriptTests(MoveCase):
    def test_a_script_goes_to_a_table_and_the_others_lose_it(self) -> None:
        self.put("Cactus.vbs", b"script")

        done = file_move.to_tables(GAME_ID, file_move.ASSET, "script", ["t2"])

        self.assertEqual(self.files(".vbs"), ["Cactus B.vbs"])
        self.assertEqual(sorted(done["lost"]), ["Cactus", "Cactus C", "Cactus D"])


class MediaTests(MoveCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("medias/(Flyer) Cactus.png", b"one")
        self.put("medias/(Flyer 2) Cactus.png", b"two")
        self.put("medias/(Wheel) Cactus.png", b"wheel")

    def media(self) -> list[str]:
        return sorted(path.name for path in (self.folder / "medias").iterdir())

    def test_a_flyers_pages_move_together(self) -> None:
        file_move.to_tables(GAME_ID, file_move.MEDIA, "flyer", ["t2"])

        self.assertEqual(self.media(), ["(Flyer 2) Cactus B.png", "(Flyer) Cactus B.png",
                                        "(Wheel) Cactus.png"])

    def test_they_stay_in_the_folder_they_sit_in(self) -> None:
        self.put("(Wheel) Cactus.jpg", b"beside the table")
        (self.folder / "medias" / "(Wheel) Cactus.png").unlink()

        file_move.to_tables(GAME_ID, file_move.MEDIA, "wheel", ["t2"])

        self.assertTrue((self.folder / "(Wheel) Cactus B.jpg").is_file())
        self.assertFalse((self.folder / "medias" / "(Wheel) Cactus B.jpg").exists())

    def test_every_page_of_a_tables_own_flyer_goes_to_the_game_and_the_games_is_kept(
            self) -> None:
        self.put("medias/(Flyer) Cactus B.png", b"b one")
        self.put("medias/(Flyer 2) Cactus B.png", b"b two")
        self.put("medias/(Flyer 3) Cactus B.png", b"b three")

        done = file_move.to_game(GAME_ID, file_move.MEDIA, "flyer", "t2")

        self.assertEqual(self.media(), ["(Flyer 2) Cactus.png", "(Flyer 3) Cactus.png",
                                        "(Flyer) Cactus.png", "(Wheel) Cactus.png"])
        self.assertEqual((self.folder / "medias" / "(Flyer 3) Cactus.png").read_bytes(),
                         b"b three")
        self.assertEqual(len(done["replaced"]), 2)
        self.assertEqual(self.kept(), ["(Flyer 2) Cactus.png", "(Flyer) Cactus.png"])
        self.assertTrue(all("medias" in path.relative_to(self.backups).parts
                            or "media" in path.relative_to(self.backups).parts
                            for path in self.backups.rglob("*") if path.is_file()))

    def test_undo_puts_the_pages_back(self) -> None:
        done = file_move.to_tables(GAME_ID, file_move.MEDIA, "flyer", ["t2"])

        file_move.undo(GAME_ID, done["undo"])

        self.assertEqual(self.media(), ["(Flyer 2) Cactus.png", "(Flyer) Cactus.png",
                                        "(Wheel) Cactus.png"])

    def test_a_kind_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            file_move.to_tables(GAME_ID, file_move.MEDIA, "nonsense", ["t2"])


class CaseOnlyTests(TempTree):
    def test_a_rename_that_changes_only_the_case_goes_through(self) -> None:
        source = self.root / "(Backglass) cactus b.png"
        source.write_bytes(b"x")

        file_move._rename(source, self.root / "(Backglass) Cactus B.png")

        self.assertEqual([path.name for path in self.root.iterdir()],
                         ["(Backglass) Cactus B.png"])

    def test_a_rename_never_writes_over_another_file(self) -> None:
        (self.root / "a.png").write_bytes(b"a")
        (self.root / "b.png").write_bytes(b"b")

        with self.assertRaises(FileExistsError):
            file_move._rename(self.root / "a.png", self.root / "b.png")

        self.assertEqual((self.root / "b.png").read_bytes(), b"b")


class PlanTests(TempTree):
    def plan(self, selected: list[str], own: set[str], names: tuple[str, ...] = (
            "Cactus", "B", "C", "D"), kind: str = "backglass") -> file_move.Plan:
        return file_move.plan_to_tables(file_move.ASSET, kind, "Cactus", list(names),
                                        selected, lambda stem: stem in own)

    def test_it_stays_when_every_table_without_its_own_is_chosen(self) -> None:
        plan = self.plan(["B", "C", "D"], own={"B"})

        self.assertTrue(plan.stays)
        self.assertEqual(plan.lost, [])

    def test_it_leaves_when_one_that_reads_it_is_not_chosen(self) -> None:
        plan = self.plan(["B", "C"], own=set())

        self.assertFalse(plan.stays)
        self.assertEqual(sorted(plan.lost), ["Cactus", "D"])

    def test_a_table_with_its_own_that_is_not_chosen_is_not_among_those_who_lose(self) -> None:
        plan = self.plan(["B"], own={"D"})

        self.assertFalse(plan.stays)
        self.assertEqual(sorted(plan.lost), ["C", "Cactus"])

    def test_choosing_what_already_reads_it_changes_nothing(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.plan(["B", "C", "D"], own=set())

    def test_the_only_table_of_a_game_takes_the_file_under_its_own_name(self) -> None:
        plan = self.plan(["B"], own=set(), names=("B",))

        self.assertFalse(plan.stays)
        self.assertEqual(plan.selected, ["B"])
        self.assertEqual(plan.lost, [])

    def test_the_only_table_cannot_be_left_without_the_file_it_uses(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.plan([], own=set(), names=("B",))

    def test_the_stem_is_the_one_spelled_on_disk(self) -> None:
        plan = self.plan(["b"], own=set())

        self.assertEqual(plan.selected, ["B"])

    def test_a_table_named_like_the_folder_in_another_case_cannot_be_chosen(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.plan(["CACTUS"], own=set())


class StateTests(MoveCase):
    def test_it_says_what_the_game_and_each_table_has(self) -> None:
        self.put("Cactus.directb2s", b"game")
        self.put("Cactus B.directb2s", b"b own")
        self.put("Cactus B.ini", b"[Player]")

        found = file_move.state(GAME_ID, file_move.ASSET, "backglass")

        self.assertEqual(found["game"]["files"], ["Cactus.directb2s"])
        by_stem = {row["stem"]: row for row in found["tables"]}
        self.assertEqual(by_stem["Cactus B"]["own"]["files"], ["Cactus B.directb2s"])
        self.assertIsNone(by_stem["Cactus C"]["own"])
        self.assertTrue(by_stem["Cactus"]["folder_named"])
        self.assertIsNone(by_stem["Cactus"]["own"])
        self.assertTrue(by_stem["Cactus B"]["ini"])
        self.assertFalse(by_stem["Cactus C"]["ini"])
        self.assertTrue(found["to_game"])
        self.assertFalse(found["playing"])

    def test_a_point_of_view_cannot_go_to_the_game(self) -> None:
        self.assertFalse(file_move.state(GAME_ID, file_move.ASSET, "pov")["to_game"])

    def test_it_says_when_a_table_is_playing(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        self.assertTrue(file_move.state(GAME_ID, file_move.ASSET, "backglass")["playing"])


class RollbackTests(MoveCase):
    def test_a_step_that_cannot_be_reversed_does_not_stop_the_others(self) -> None:
        run = file_move._Run(GAME_ID, self.root, file_move.ASSET)
        first, second = self.root / "one", self.root / "two"
        first.write_text("1")
        second.write_text("2")
        run.ops = [file_move._Op("copy", first), file_move._Op("copy", second)]
        real = file_move._reverse

        def reverse(op: file_move._Op) -> None:
            if op.path == second:
                raise OSError("busy")
            real(op)

        with patch.object(file_move, "_reverse", reverse), self.assertRaises(OSError):
            run.rollback()

        self.assertFalse(first.exists())
