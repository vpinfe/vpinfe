import json
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from common import ids
from common.games import game_identity, library_folders
from common.games.game_repository import game_to_row
from common.games.info_file import MetaConfig
from common.games.library_folders import KIND_GAME, KIND_ROOT, Location
from tests.support.library import TempTree, fake_game, write_game
from tests.support.saved import save_record, saved_record


def _game(root: Path, name: str = "Example", meta: dict | None = None):
    """A game folder with a .info file, shaped like GameParser produces."""
    return fake_game(write_game(root, name, info=meta, vpx=False), name, meta=meta or {})


class MintedIdTests(TempTree):
    """Short enough to read down a phone, long enough not to collide."""

    def test_an_id_is_short_and_unambiguous(self) -> None:
        minted = game_identity.new_id()

        self.assertEqual(len(minted), game_identity.ID_LENGTH)
        self.assertTrue(set(minted) <= set(game_identity.ID_ALPHABET))
        self.assertFalse(set(minted) & set("0OIl"), "these are misread out loud")

    def test_ids_do_not_repeat(self) -> None:
        minted = [game_identity.new_id() for _ in range(5000)]

        self.assertEqual(len(set(minted)), len(minted))

    def test_a_metadata_rebuild_mints_the_same_shape(self) -> None:
        """Two writers assign ids; only one format may come out of them."""
        info = self.root / "Example.info"
        MetaConfig(str(info)).write_config_meta({"vpsdata": {}, "vpxdata": {"filename": "x.vpx"}})

        minted = saved_record(info)["vpinfe"]["game_id"]
        self.assertEqual(len(minted), game_identity.ID_LENGTH)
        self.assertTrue(set(minted) <= set(game_identity.ID_ALPHABET))



class DerivedIdTests(TempTree):
    """A folder with no id is given the one its place in a location makes."""

    def _locations(self, *held: tuple[str, Path]):
        rows = [Location(location_id=one, path=str(path), kind=KIND_ROOT) for one, path in held]
        patched = mock.patch.object(library_folders, "configured", return_value=rows)
        patched.start()
        self.addCleanup(patched.stop)

    def test_the_same_parts_make_the_same_id_in_the_same_shape(self) -> None:
        made = ids.derive_id("loc", "Game (2024)")

        self.assertEqual(made, ids.derive_id("loc", "Game (2024)"))
        self.assertEqual(len(made), ids.LENGTH)
        self.assertTrue(set(made) <= set(ids.ALPHABET))

    def test_different_parts_make_different_ids(self) -> None:
        self.assertNotEqual(ids.derive_id("loc", "a"), ids.derive_id("loc", "b"))
        self.assertNotEqual(ids.derive_id("loc", "a"), ids.derive_id("other", "a"))
        self.assertNotEqual(ids.derive_id("a", "bc"), ids.derive_id("ab", "c"))

    def test_a_name_read_composed_or_decomposed_is_one_id(self) -> None:
        self.assertEqual(ids.derive_id("loc", "Café"), ids.derive_id("loc", "Café"))

    def test_a_folder_is_named_by_its_location_and_its_path_inside_it(self) -> None:
        self._locations(("here", self.root))
        folder = self.root / "Shelf" / "Game"

        self.assertEqual(game_identity.minted_id(str(folder)),
                         ids.derive_id("here", "Shelf/Game"))

    def test_repointing_a_location_at_the_same_games_keeps_their_ids(self) -> None:
        self._locations(("here", self.root))
        before = game_identity.minted_id(str(self.root / "Game"))
        elsewhere = self.root / "moved"
        self._locations(("here", elsewhere))

        self.assertEqual(game_identity.minted_id(str(elsewhere / "Game")), before)

    def test_the_same_folder_in_another_location_is_another_game(self) -> None:
        self._locations(("one", self.root / "a"), ("two", self.root / "b"))

        self.assertNotEqual(game_identity.minted_id(str(self.root / "a" / "Game")),
                            game_identity.minted_id(str(self.root / "b" / "Game")))

    def test_a_game_location_is_named_by_the_location_alone(self) -> None:
        folder = self.root / "Game"
        rows = [Location(location_id="one", path=str(folder), kind=KIND_GAME)]
        with mock.patch.object(library_folders, "configured", return_value=rows):
            self.assertEqual(game_identity.minted_id(str(folder)), ids.derive_id("one", ""))

    def test_a_table_is_named_by_its_game_and_its_file(self) -> None:
        self._locations(("here", self.root))
        game = str(self.root / "Game")

        self.assertEqual(game_identity.minted_id(game, "Game.vpx"),
                         ids.derive_id("here", "Game", "Game.vpx"))
        self.assertNotEqual(game_identity.minted_id(game, "Game.vpx"),
                            game_identity.minted_id(game))

    def test_a_name_that_is_not_valid_text_still_makes_an_id(self) -> None:
        made = ids.derive_id("loc", "Game\udcff")

        self.assertEqual(len(made), ids.LENGTH)
        self.assertNotEqual(made, ids.derive_id("loc", "Game\udcfe"))

    def test_a_folder_no_location_holds_gets_a_random_id(self) -> None:
        self._locations(("here", self.root / "a"))
        folder = str(self.root / "b" / "Game")

        made = {game_identity.minted_id(folder) for _ in range(5)}

        self.assertEqual(len(made), 5)
        self.assertNotIn(ids.derive_id("here", "Game"), made)

    def test_nested_locations_name_the_folder_by_the_one_it_was_found_through(self) -> None:
        self._locations(("outer", self.root), ("inner", self.root / "Shelf"))
        folder = str(self.root / "Shelf" / "Game")

        self.assertEqual(game_identity.minted_id(folder, location_id="inner"),
                         ids.derive_id("inner", "Game"))
        self.assertEqual(game_identity.minted_id(folder, location_id="outer"),
                         ids.derive_id("outer", "Shelf/Game"))
        self.assertEqual(game_identity.minted_id(folder), ids.derive_id("outer", "Shelf/Game"))

    def test_a_location_that_no_longer_holds_the_folder_falls_to_one_that_does(self) -> None:
        self._locations(("here", self.root))

        self.assertEqual(
            game_identity.minted_id(str(self.root / "Game"), location_id="gone"),
            ids.derive_id("here", "Game"))

    def test_a_folder_that_took_an_old_ones_id_gets_another(self) -> None:
        """A renamed folder keeps the id it was written with, and a new folder at the old
        path would derive that same id."""
        self._locations(("here", self.root))
        held = ids.derive_id("here", "Game")
        renamed = _game(self.root, "Renamed", meta={"vpinfe": {"game_id": held}})
        renamed.location_id = "here"
        fresh = _game(self.root, "Game")
        fresh.location_id = "here"

        resolved = game_identity.resolve_ids([fresh, renamed])

        self.assertEqual(resolved.shadowed, ())
        self.assertEqual(len(resolved.by_id), 2)
        self.assertIs(resolved.by_id[held], renamed)

    def test_a_batch_looks_up_the_locations_once(self) -> None:
        self._locations(("here", self.root))
        games = [_game(self.root, f"Game {n}") for n in range(3)]
        for one in games:
            one.location_id = "here"

        game_identity.resolve_ids(games)

        configured = cast(mock.MagicMock, library_folders.configured)
        self.assertEqual(configured.call_count, 1)

    def test_ensure_id_makes_it_and_writes_it_as_it_is(self) -> None:
        self._locations(("here", self.root))
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})
        game.location_id = "here"

        self.assertEqual(game_identity.ensure_id(game), ids.derive_id("here", "Example"))

    def test_a_reissued_id_is_not_the_one_the_path_makes(self) -> None:
        self._locations(("here", self.root))
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})
        game.location_id = "here"
        first = game_identity.ensure_id(game)

        reissued = game_identity.ensure_id(game, force_new=True)

        self.assertNotEqual(reissued, first)
        self.assertNotEqual(reissued, ids.derive_id("here", "Example"))


class GameIdTests(TempTree):

    def test_reading_an_unassigned_game_returns_empty_and_writes_nothing(self) -> None:
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})
        info = Path(game.full_path_game) / "Example.info"
        before = info.read_text(encoding="utf-8")

        self.assertEqual(game_identity.game_id(game), "")
        self.assertEqual(info.read_text(encoding="utf-8"), before)

    def test_ensure_id_mints_and_persists(self) -> None:
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})

        minted = game_identity.ensure_id(game)
        info = Path(game.full_path_game) / "Example.info"
        on_disk = saved_record(info)

        self.assertTrue(minted)
        self.assertEqual(on_disk["vpinfe"]["game_id"], minted)
        self.assertEqual(game_identity.game_id(game), minted)

    def test_ensure_id_is_stable_across_calls(self) -> None:
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})

        first = game_identity.ensure_id(game)
        second = game_identity.ensure_id(game)

        self.assertEqual(first, second)

    def test_ensure_id_adopts_an_id_already_on_disk(self) -> None:
        """The in-memory copy can be stale; disk wins over minting a second id."""
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})
        info = Path(game.full_path_game) / "Example.info"
        info.write_text(json.dumps({"Info": {"VPSId": "vps-1"},
                                    "vpinfe": {"game_id": "already-here"}}),
                        encoding="utf-8")

        self.assertEqual(game_identity.ensure_id(game), "already-here")

    def test_ensure_id_preserves_the_rest_of_the_meta(self) -> None:
        game = _game(self.root, meta={
            "Info": {"VPSId": "vps-1", "Title": "Example"},
            "User": {"Rating": 4},
            "vpinfe": {"alt_title": "My Example"},
            "VPXFile": {},
        })

        game_identity.ensure_id(game)
        info = Path(game.full_path_game) / "Example.info"
        on_disk = saved_record(info)

        self.assertEqual(on_disk["User"]["Rating"], 8)
        self.assertEqual(on_disk["vpinfe"]["alt_title"], "My Example")
        self.assertEqual(on_disk["Info"]["Title"], "Example")


class IdentityOutlivesVpsIdTests(TempTree):
    """The cases that make the VPS-derived id unusable as a primary key."""


    def _rebuild(self, path: Path, filehash: str) -> dict:
        """Run a metadata rebuild the way build_metadata does."""
        meta = MetaConfig(str(path))
        meta.write_config_meta({
            "vpsdata": {"id": "vps-1", "name": "Example", "manufacturer": "Bally",
                        "year": "1990", "type": "SS", "theme": [], "ipdbUrl": ""},
            "vpxdata": {"filename": "Example.vpx", "file_hash": filehash, "version": "1.0",
                        "release_date": "", "save_date": "", "save_rev": "",
                        "manufacturer": "Bally", "year": "1990", "type": "SS",
                        "vbs_hash": "", "rom": "", "author_name": "",
                        "detect_nfozzy": False, "detect_fleep": False, "detect_ssf": False,
                        "detect_lut": False, "detect_scorbit": False, "detect_fastflips": False,
                        "detect_flex": False},
        })
        return saved_record(path)

    def test_a_metadata_rebuild_mints_an_id(self) -> None:
        info = self.root / "Example.info"

        rebuilt = self._rebuild(info, "hash-a")

        self.assertTrue(rebuilt["vpinfe"]["game_id"])

    def test_a_match_survives_a_table_update(self) -> None:
        """A machine match names the machine, not the file, so a newer build of the
        table is not a reason to stop applying it."""
        info = self.root / "Example.info"
        first = self._rebuild(info, "hash-a")
        game_id = first["vpinfe"]["game_id"]

        data = saved_record(info)
        data["vpinfe"]["alt_vpsid"] = "vps-override"
        save_record(info, data)
        after = self._rebuild(info, "hash-b")["vpinfe"]

        self.assertEqual(after["alt_vpsid"], "vps-override")
        self.assertEqual(after["game_id"], game_id)

    def test_a_rebuild_that_changes_nothing_leaves_a_match_alone(self) -> None:
        info = self.root / "Example.info"
        self._rebuild(info, "hash-a")

        data = saved_record(info)
        data["vpinfe"]["alt_vpsid"] = "vps-override"
        save_record(info, data)
        after = self._rebuild(info, "hash-a")["vpinfe"]

        self.assertEqual(after["alt_vpsid"], "vps-override")

    def test_the_id_survives_repeated_rebuilds(self) -> None:
        info = self.root / "Example.info"

        first = self._rebuild(info, "hash-a")["vpinfe"]["game_id"]
        second = self._rebuild(info, "hash-a")["vpinfe"]["game_id"]

        self.assertEqual(first, second)

    def test_a_game_vpsdb_never_matched_still_gets_an_id(self) -> None:
        """The VPS-derived id is empty here, which is why it can't be the key."""
        game = _game(self.root, "Unmatched", meta={"Info": {"VPSId": ""}})

        minted = game_identity.ensure_id(game)
        row = game_to_row(game)

        self.assertTrue(minted)
        self.assertEqual(row["vpsid"], "", "precondition: no VPS-derived id")
        self.assertEqual(row["vpinfe_id"], minted)


class UniquenessTests(TempTree):

    def test_ensure_unique_ids_assigns_every_game(self) -> None:
        games = [_game(self.root, n, meta={"Info": {}}) for n in ("A", "B", "C")]

        by_id = game_identity.ensure_unique_ids(games)

        self.assertEqual(len(by_id), 3)
        self.assertTrue(all(game_identity.game_id(t) for t in games))

    def test_a_copied_game_folder_is_left_alone_and_reported(self) -> None:
        """It used to be given a fresh id, which settles the clash by writing to
        somebody's file - and which of the two should keep the id is not ours to
        decide. Both records stand; one answers for the id and the other is reported.
        """
        original = _game(self.root, "Original", meta={"Info": {}})
        assigned = game_identity.ensure_id(original)
        # Copying the folder copies the .info, and with it the id.
        copy = _game(self.root, "Copy", meta={"Info": {}, "vpinfe": {"game_id": assigned}})

        with self.assertLogs("vpinfe.common.games.game_identity", level="WARNING"):
            found = game_identity.resolve_ids([original, copy], order=[])

        self.assertEqual(game_identity.game_id(copy), assigned,
                         "the copy's record is not rewritten to settle this")
        self.assertEqual(game_identity.game_id(original), assigned)
        self.assertEqual(len(found.by_id), 1)
        self.assertEqual(len(found.shadowed), 1)


class LookupTests(TempTree):

    def test_find_by_id_returns_the_matching_game(self) -> None:
        wanted = _game(self.root, "Wanted", meta={"Info": {}})
        other = _game(self.root, "Other", meta={"Info": {}})
        wanted_id = game_identity.ensure_id(wanted)
        game_identity.ensure_id(other)

        self.assertIs(game_identity.find_by_id([other, wanted], wanted_id), wanted)

    def test_find_by_id_rejects_missing_and_blank_ids(self) -> None:
        unassigned = _game(self.root, "Unassigned", meta={"Info": {}})

        self.assertIsNone(game_identity.find_by_id([unassigned], "no-such-id"))
        self.assertIsNone(game_identity.find_by_id([unassigned], ""))


class RowFieldTests(TempTree):

    def test_a_row_carries_correlation_ids_and_identity_separately(self) -> None:
        """VPS ids correlate with other services; vpinfe_id is what identifies the game."""
        game = _game(self.root, meta={
            "Info": {"VPSId": "vps-1"},
            "vpinfe": {"alt_vpsid": "vps-override"},
        })
        assigned = game_identity.ensure_id(game)

        row = game_to_row(game)

        self.assertEqual(row["vpsid"], "vps-1")
        self.assertEqual(row["alt_vpsid"], "vps-override")
        self.assertEqual(row["vpinfe_id"], assigned)
        # There is no derived "id" to pick up by accident.
        self.assertNotIn("id", row)

    def test_row_reports_an_empty_game_id_before_one_is_assigned(self) -> None:
        game = _game(self.root, meta={"Info": {"VPSId": "vps-1"}})

        self.assertEqual(game_to_row(game)["vpinfe_id"], "")


if __name__ == "__main__":
    unittest.main()
