import json
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from common.games import game_repository
from common.games.collection_store import (
    COLLECTIONS_SCHEMA,
    CollectionStore,
    collections_schema,
    restorable_collections_backup,
)
from common.games.info_file import MetaConfig
from tests.support.library import TempTree, fake_game, write_game


def _game(root: Path, name: str, *, vpsid: str = "", altvpsid: str = "", game_id: str = ""):
    meta = {"Info": {"VPSId": vpsid, "Title": name}, "vpinfe": {}}
    if altvpsid:
        meta["vpinfe"]["alt_vpsid"] = altvpsid
    if game_id:
        meta["vpinfe"]["game_id"] = game_id
    return fake_game(write_game(root, name, info=meta, vpx=False), name, meta=meta)


def _collections(path: Path, sections: dict) -> CollectionStore:
    lines = []
    for name, members in sections.items():
        lines.append(f"[{name}]")
        lines.append(f"vpsids = {','.join(members)}")
        lines.append("type = vpsid")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return CollectionStore(str(path))


class MigrationTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "collections.ini"

    def test_membership_moves_onto_game_ids(self) -> None:
        game = _game(self.root, "Medieval Madness", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})

        moved = collections.migrate_membership_to_game_ids([game])

        self.assertEqual(moved, 1)
        self.assertEqual(collections.get_members("Favorites"), ["id-mm"])

    def test_a_second_pass_finds_nothing_to_move(self) -> None:
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})
        collections.migrate_membership_to_game_ids([game])

        again = CollectionStore(str(self.ini))

        self.assertEqual(again.schema_version(), COLLECTIONS_SCHEMA)
        self.assertEqual(again.migrate_membership_to_game_ids([game]), 0)
        self.assertEqual(again.get_members("Favorites"), ["id-mm"])

    def test_a_newer_file_is_left_alone(self) -> None:
        """An older build must not rewrite membership it does not understand."""
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["something-new"]})
        collections._stamp_schema(COLLECTIONS_SCHEMA + 5)
        collections.save()

        reopened = CollectionStore(str(self.ini))
        with self.assertLogs("vpinfe.common.games.collection_store", level="WARNING"):
            moved = reopened.migrate_membership_to_game_ids([game])

        self.assertEqual(moved, 0)
        self.assertEqual(reopened.get_members("Favorites"), ["something-new"])

    def test_an_entry_with_no_matching_game_is_kept(self) -> None:
        """The game may just not be here now; dropping it loses the membership."""
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-mm", "vps-gone"]})

        collections.migrate_membership_to_game_ids([game])

        self.assertEqual(sorted(collections.get_members("Favorites")), ["id-mm", "vps-gone"])

    def test_the_reserved_section_is_not_a_collection(self) -> None:
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})
        collections.migrate_membership_to_game_ids([game])

        reopened = CollectionStore(str(self.ini))

        self.assertEqual(reopened.get_collections_name(), ["Favorites"])
        self.assertEqual(reopened.schema_version(), COLLECTIONS_SCHEMA,
                         "the version is a field, not a collection")

    def test_membership_recorded_under_an_alt_vpsid_still_migrates(self) -> None:
        game = _game(self.root, "MM", vpsid="vps-base", altvpsid="vps-alt", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-alt"]})

        collections.migrate_membership_to_game_ids([game])

        self.assertEqual(collections.get_members("Favorites"), ["id-mm"])


class ResolvesWhateverIsStillRawTests(TempTree):
    """VPS ids in a file the stamp calls migrated still move onto games once a game
    carries them: a library read gives games their VPS id after startup, and a save made
    before the first migration stamps the file as done."""

    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "collections.ini"
        self.json = self.root / "collections.json"

    def test_an_empty_library_leaves_the_ids_for_the_next_pass(self) -> None:
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})
        self.assertEqual(collections.migrate_membership_to_game_ids([]), 0)

        later = CollectionStore(str(self.json))
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")

        self.assertEqual(later.migrate_membership_to_game_ids([game]), 1)
        self.assertEqual(CollectionStore(str(self.json)).get_members("Favorites"), ["id-mm"])

    def test_a_save_before_the_first_pass_does_not_end_it(self) -> None:
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})
        collections.save()
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")

        reopened = CollectionStore(str(self.json))

        self.assertEqual(reopened.migrate_membership_to_game_ids([game]), 1)
        self.assertEqual(reopened.get_members("Favorites"), ["id-mm"])

    def test_a_vps_id_moves_to_every_game_that_carries_it(self) -> None:
        a = _game(self.root, "A", vpsid="shared", game_id="id-a")
        b = _game(self.root, "B", vpsid="shared", game_id="id-b")
        collections = _collections(self.ini, {"Favorites": ["shared"]})

        collections.migrate_membership_to_game_ids([a, b])

        self.assertEqual(collections.get_members("Favorites"), ["id-a", "id-b"])

    def test_a_game_already_a_member_is_not_added_twice(self) -> None:
        a = _game(self.root, "A", vpsid="shared", game_id="id-a")
        b = _game(self.root, "B", vpsid="shared", game_id="id-b")
        collections = _collections(self.ini, {"Favorites": ["id-a", "shared"]})

        collections.migrate_membership_to_game_ids([a, b])

        self.assertEqual(collections.get_members("Favorites"), ["id-a", "id-b"])

    def test_a_named_table_survives_a_pass(self) -> None:
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        self.json.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Favorites", "type": "manual", "members": [
                 {"game": "id-mm", "table": "t1"}, "vps-other"]}]}), encoding="utf-8")
        collections = CollectionStore(str(self.json))

        self.assertEqual(collections.migrate_membership_to_game_ids([game]), 0)
        self.assertEqual(collections.get_member_refs("Favorites"),
                         [{"game": "id-mm", "table": "t1"}, {"game": "vps-other"}])

    def test_a_game_already_named_with_a_table_is_not_added_whole(self) -> None:
        a = _game(self.root, "A", vpsid="shared", game_id="id-a")
        b = _game(self.root, "B", vpsid="shared", game_id="id-b")
        self.json.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Favorites", "type": "manual", "members": [
                 {"game": "id-a", "table": "t1"}, "shared"]}]}), encoding="utf-8")
        collections = CollectionStore(str(self.json))

        collections.migrate_membership_to_game_ids([a, b])

        self.assertEqual(collections.get_member_refs("Favorites"),
                         [{"game": "id-a", "table": "t1"}, {"game": "id-b"}])

    def test_a_game_that_is_only_absent_is_not_a_warning_every_time(self) -> None:
        self.json.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Favorites", "type": "manual",
                              "members": ["id-absent"]}]}), encoding="utf-8")
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")

        with self.assertNoLogs("vpinfe.common.games.collection_store", "WARNING"):
            CollectionStore(str(self.json)).migrate_membership_to_game_ids([game])

    def test_the_startup_read_resolves_them(self) -> None:
        from common.games import library_refresh

        games = [object()]
        with (mock.patch.object(library_refresh, "_catalog_for"),
              mock.patch.object(library_refresh, "enrich", return_value={}),
              mock.patch.object(library_refresh, "_art_for"),
              mock.patch.object(library_refresh, "_seen_unmatched", return_value=[]),
              mock.patch.object(library_refresh, "_waiting", return_value=[]),
              mock.patch("common.games.auto_match.match_new", return_value={}),
              mock.patch.object(library_refresh, "_resolve_collections") as resolve):
            library_refresh.read_at_startup(games, [])

        resolve.assert_called_once_with(games)

    def test_a_pass_that_changes_nothing_writes_nothing(self) -> None:
        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})
        collections.migrate_membership_to_game_ids([game])
        before = self.json.stat().st_mtime_ns

        self.assertEqual(
            CollectionStore(str(self.json)).migrate_membership_to_game_ids([game]), 0)
        self.assertEqual(self.json.stat().st_mtime_ns, before)

    def test_a_library_read_resolves_them(self) -> None:
        from common.games import library_refresh

        game = _game(self.root, "MM", vpsid="vps-mm", game_id="id-mm")
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})
        collections.migrate_membership_to_game_ids([])

        with mock.patch("common.games.collections_service.get_collections_manager",
                        return_value=CollectionStore(str(self.json))):
            library_refresh._resolve_collections([game])

        self.assertEqual(CollectionStore(str(self.json)).get_members("Favorites"), ["id-mm"])


class MembershipTests(TempTree):
    """The four defects that made VPS-keyed membership unusable."""

    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "collections.ini"

    def test_a_game_vpsdb_never_matched_can_join_a_collection(self) -> None:
        """Defect 1: its VPS id is empty, so it could not be a member at all."""
        game = _game(self.root, "Homebrew", vpsid="", game_id="id-home")
        collections = _collections(self.ini, {"Favorites": ["id-home"]})

        self.assertTrue(collections.is_member(game, set(collections.get_members("Favorites"))))

    def test_two_games_sharing_a_vps_id_are_distinguishable(self) -> None:
        """Defect 2: one VPS id, two games - membership could not tell them apart."""
        a = _game(self.root, "A", vpsid="shared", game_id="id-a")
        b = _game(self.root, "B", vpsid="shared", game_id="id-b")
        collections = _collections(self.ini, {"Favorites": ["id-a"]})
        members = set(collections.get_members("Favorites"))

        self.assertTrue(collections.is_member(a, members))
        self.assertFalse(collections.is_member(b, members))

    def test_membership_survives_a_vpx_update(self) -> None:
        """Defect 3: membership keyed on anything a rebuild can rewrite is orphaned by
        one. The game's own id is the only key a rebuild never touches."""
        info = self.root / "MM.info"

        def rebuild(filehash):
            meta = MetaConfig(str(info))
            meta.write_config_meta({
                "vpsdata": {"id": "vps-mm", "name": "MM", "manufacturer": "Williams",
                            "year": "1997", "type": "SS", "theme": [], "ipdbUrl": ""},
                "vpxdata": {"filename": "MM.vpx", "file_hash": filehash, "version": "1",
                            "release_date": "", "save_date": "", "save_rev": "",
                            "manufacturer": "", "year": "", "type": "",
                            "vbs_hash": "", "rom": "", "author_name": "",
                            "detect_nfozzy": False, "detect_fleep": False, "detect_ssf": False,
                            "detect_lut": False, "detect_scorbit": False,
                            "detect_fastflips": False, "detect_flex": False},
            })
            return json.loads(info.read_text(encoding="utf-8"))

        first = rebuild("hash-a")
        game_id_value = first["vpinfe"]["game_id"]

        # User re-points the game, then updates the .vpx.
        data = json.loads(info.read_text(encoding="utf-8"))
        data["vpinfe"]["alt_vpsid"] = "vps-override"
        info.write_text(json.dumps(data), encoding="utf-8")
        after = rebuild("hash-b")

        game = fake_game(self.root, "MM", meta=after)
        collections = _collections(self.ini, {"Favorites": []})

        self.assertEqual(after["vpinfe"]["game_id"], game_id_value)
        self.assertTrue(collections.is_member(game, {game_id_value}))


class DisplayPathTests(TempTree):
    """The Manager resolves collections by map lookup, not is_member().

    Both paths have to agree. When they did not, migrating simply emptied the
    collections column - membership was intact on disk and correct in the frontend,
    and the only symptom was a table row that had stopped saying what it belonged to.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "collections.ini"

    def _row_for(self, game):
        with mock.patch.object(game_repository, "COLLECTIONS_PATH", self.ini):
            mapping = game_repository.collections_by_game_id()
        return game_repository.game_to_row(game, mapping)

    def test_a_migrated_collection_still_shows_on_the_game_row(self) -> None:
        game = _game(self.root, "Medieval Madness", vpsid="vps-mm", game_id="id-mm")
        game.full_path_vpx_file = str(self.root / "Medieval Madness" / "MM.vpx")
        collections = _collections(self.ini, {"Favorites": ["vps-mm"]})

        collections.migrate_membership_to_game_ids([game])

        self.assertEqual(self._row_for(game)["collections"], ["Favorites"],
                         "the migration must not empty the collections column")

    def test_an_entry_the_migration_could_not_resolve_still_shows(self) -> None:
        """It stays VPS-keyed, and the migration will not run again to fix it.

        Happens when the table was not installed at migration time. is_member()
        tolerates this, so the row lookup has to as well or the two disagree.
        """
        game = _game(self.root, "Late Arrival", vpsid="vps-late", game_id="id-late")
        game.full_path_vpx_file = str(self.root / "Late Arrival" / "Late.vpx")
        collections = _collections(self.ini, {"Favorites": ["vps-late"]})
        collections._stamp_schema()
        collections.save()

        self.assertEqual(collections.get_members("Favorites"), ["vps-late"],
                         "precondition: the entry was never rewritten")
        self.assertEqual(self._row_for(game)["collections"], ["Favorites"])

    def test_an_entry_keyed_by_alt_vps_id_still_shows(self) -> None:
        game = _game(self.root, "Repointed", vpsid="vps-base",
                       altvpsid="vps-alt", game_id="id-repointed")
        game.full_path_vpx_file = str(self.root / "Repointed" / "Repointed.vpx")
        _collections(self.ini, {"Favorites": ["vps-alt"]})

        self.assertEqual(self._row_for(game)["collections"], ["Favorites"])

    def test_a_game_with_no_vps_id_shows_its_collections(self) -> None:
        """The row lookup has to key on the game id, not on anything VPS-derived."""
        game = _game(self.root, "Homebrew", vpsid="", game_id="id-home")
        game.full_path_vpx_file = str(self.root / "Homebrew" / "Homebrew.vpx")
        _collections(self.ini, {"Favorites": ["id-home"]})

        self.assertEqual(self._row_for(game)["collections"], ["Favorites"])

    def test_filter_collections_are_not_in_the_map(self) -> None:
        """They have no member list; membership is decided per game when displayed."""
        self.ini.write_text("[Recent]\ntype = filter\nletter = All\n", encoding="utf-8")

        with mock.patch.object(game_repository, "COLLECTIONS_PATH", self.ini):
            self.assertEqual(game_repository.collections_by_game_id(), {})


class BackupTests(TempTree):
    """Collections are the other file this branch rewrites.

    The .info migration has kept a copy since the start; this one did not, and it moves
    membership onto ids an older build cannot resolve - so without a copy a downgrade left
    every collection reading as empty with nothing to go back to.
    """

    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "collections.ini"

    def _backups(self):
        return sorted(p for p in self.root.iterdir() if ".vpinfe-" in p.name)

    def test_the_migration_keeps_the_file_it_is_about_to_rewrite(self):
        collections = _collections(self.ini, {"Favorites": ["vps-1"]})
        before = self.ini.read_text(encoding="utf-8")

        collections.migrate_membership_to_game_ids(
            [_game(self.root, "Dr. Dude", vpsid="vps-1", game_id="tid-1")])

        backups = self._backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), before)

    def test_a_run_that_changes_nothing_leaves_no_backup(self):
        """It records that it has run, so a second startup does no work and keeps no copy."""
        collections = _collections(self.ini, {"Favorites": ["vps-1"]})
        collections.migrate_membership_to_game_ids(
            [_game(self.root, "Dr. Dude", vpsid="vps-1", game_id="tid-1")])

        CollectionStore(str(self.ini)).migrate_membership_to_game_ids([])

        self.assertEqual(len(self._backups()), 1)

    def test_the_saved_copy_is_the_one_this_build_would_restore(self):
        collections = _collections(self.ini, {"Favorites": ["vps-1"]})
        collections.migrate_membership_to_game_ids(
            [_game(self.root, "Dr. Dude", vpsid="vps-1", game_id="tid-1")])

        chosen = restorable_collections_backup(self.root)

        self.assertIsNotNone(chosen)
        self.assertIsNone(collections_schema(chosen),
                          "the pre-migration copy predates versioning")

    def test_an_interrupted_save_leaves_the_previous_file_intact(self):
        collections = _collections(self.ini, {"Favorites": ["vps-1"]})
        collections.save()
        before = collections.path.read_text(encoding="utf-8")

        collections.add_collection("Later", ["vps-2"])
        with mock.patch("common.games.collection_store.json.dump",
                        side_effect=OSError("disk went away")):
            with self.assertRaises(OSError):
                collections.save()

        self.assertEqual(collections.path.read_text(encoding="utf-8"), before)
        self.assertNotIn("Later", collections.path.read_text(encoding="utf-8"))


class JsonConversionTests(TempTree):
    """The move off collections.ini. A user's curation is in that file, so the only
    thing that matters is that all of it survives and the ini stays where 2.x reads it."""

    def setUp(self) -> None:
        super().setUp()
        self.ini = self.root / "collections.ini"
        self.json = self.root / "collections.json"

    def _write_ini(self, text: str) -> None:
        self.ini.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")

    def test_an_ini_is_read_without_being_written(self) -> None:
        """Reading must not have a side effect: plenty of callers only ever look."""
        self._write_ini("""
            [vpinfe]
            schema = 1
            [Favorites]
            type = vpsid
            vpsids = id-one,id-two
        """)
        collections = CollectionStore(str(self.json))

        self.assertEqual(collections.get_members("Favorites"), ["id-one", "id-two"])
        self.assertFalse(self.json.exists(), "reading wrote a file")

    def test_saving_converts_and_leaves_the_ini_for_2x(self) -> None:
        self._write_ini("""
            [vpinfe]
            schema = 1
            [Favorites]
            type = vpsid
            vpsids = id-one
            image = fav.png
        """)
        CollectionStore(str(self.json)).save()

        stored = json.loads(self.json.read_text(encoding="utf-8"))
        self.assertEqual(stored["schema"], COLLECTIONS_SCHEMA)
        self.assertEqual(stored["collections"][0]["members"], [{"game": "id-one"}])
        self.assertEqual(stored["collections"][0]["image"], "fav.png")
        self.assertTrue(self.ini.exists(), "2.x still reads the ini; do not delete it")
        self.assertTrue(any(p.name.startswith("collections.ini.vpinfe-")
                            for p in self.root.iterdir()), "no copy kept")

    def test_a_filter_collection_keeps_every_criterion(self) -> None:
        self._write_ini("""
            [80s Williams]
            type = filter
            manufacturer = Williams
            year = 1985
            table_type = SS
            sort_by = Newest
            order_by = Ascending
        """)
        CollectionStore(str(self.json)).save()

        filters = CollectionStore(str(self.json)).get_filters("80s Williams")
        assert filters is not None
        self.assertEqual(filters["manufacturer"], "Williams")
        self.assertEqual(filters["year"], "1985")
        self.assertEqual(filters["game_type"], "SS", "2.x wrote the type as table_type")
        self.assertNotIn("table_type", filters)
        self.assertEqual(filters["sort_by"], "Newest")
        self.assertEqual(filters["order_by"], "Ascending")
        self.assertEqual(filters["theme"], "All", "an unset axis is unconstrained")

    def test_the_order_collections_were_in_is_kept(self) -> None:
        self._write_ini("""
            [Zeta]
            vpsids = a
            [Alpha]
            vpsids = b
            [Mid]
            vpsids = c
        """)
        CollectionStore(str(self.json)).save()

        self.assertEqual(CollectionStore(str(self.json)).get_collections_name(),
                         ["Zeta", "Alpha", "Mid"])

    def test_json_wins_when_both_files_exist(self) -> None:
        """After the conversion the ini is stale; it must never be read again."""
        self._write_ini("[Stale]\nvpsids = old\n")
        self.json.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Current", "type": "manual", "members": ["new"]}]}),
            encoding="utf-8")

        collections = CollectionStore(str(self.json))

        self.assertEqual(collections.get_collections_name(), ["Current"])

    def test_both_reserved_section_spellings_are_filtered(self) -> None:
        """Real files carry [VPinFE] and [vpinfe] side by side - the section was renamed
        without the old one being removed. Reading only the new name turns the old one
        into a collection called "VPinFE"."""
        self._write_ini("""
            [Last Played]
            type = vpsid
            vpsids = a,b

            [VPinFE]
            schema = 1

            [vpinfe]
            schema = 1
        """)
        collections = CollectionStore(str(self.json))

        self.assertEqual(collections.get_collections_name(), ["Last Played"])
        self.assertEqual(collections.schema_version(), 1)

    def test_the_ini_path_still_finds_the_collections(self) -> None:
        """A script or an old config may still name collections.ini."""
        self.json.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Favorites", "type": "manual", "members": ["x"]}]}),
            encoding="utf-8")

        self.assertEqual(CollectionStore(str(self.ini)).get_members("Favorites"), ["x"])


class MemberRefTests(TempTree):
    """A member names a game, and optionally one of its tables.

    This is what lets the same game sit in two collections with a different table in
    each, and appear twice in one curated order.
    """

    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"

    def _saved(self, collections) -> list:
        collections.save()
        return json.loads(self.path.read_text(encoding="utf-8"))["collections"]

    def test_a_bare_id_is_read_as_following_the_game(self) -> None:
        """Everything written before refs existed is a game id and no more."""
        self.path.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Favorites", "type": "manual",
                              "members": ["id-one", "id-two"]}]}), encoding="utf-8")

        collections = CollectionStore(str(self.path))

        self.assertEqual(collections.get_member_refs("Favorites"),
                         [{"game": "id-one"}, {"game": "id-two"}])
        self.assertEqual(collections.get_members("Favorites"), ["id-one", "id-two"])

    def test_one_game_can_appear_twice_with_different_tables(self) -> None:
        """The case the refs exist for: two builds of a game at two positions."""
        collections = CollectionStore(str(self.path))
        collections.add_collection("Friday Night")
        collections.add_member("Friday Night", "mm", table_id="vpw")
        collections.add_member("Friday Night", "afm")
        collections.add_member("Friday Night", "mm", table_id="jp")

        refs = collections.get_member_refs("Friday Night")
        self.assertEqual(refs, [{"game": "mm", "table": "vpw"},
                                {"game": "afm"},
                                {"game": "mm", "table": "jp"}])
        self.assertEqual(collections.get_members("Friday Night"), ["mm", "afm"],
                         "game ids are de-duplicated; the refs are not")

    def test_the_same_pairing_is_not_added_twice(self) -> None:
        collections = CollectionStore(str(self.path))
        collections.add_collection("Favorites")
        collections.add_member("Favorites", "mm", table_id="vpw")
        collections.add_member("Favorites", "mm", table_id="vpw")

        self.assertEqual(len(collections.get_member_refs("Favorites")), 1)

    def test_removing_a_game_removes_every_table_of_it(self) -> None:
        collections = CollectionStore(str(self.path))
        collections.add_collection("Favorites")
        collections.add_member("Favorites", "mm", table_id="vpw")
        collections.add_member("Favorites", "mm", table_id="jp")
        collections.add_member("Favorites", "afm")

        collections.remove_member("Favorites", "mm")

        self.assertEqual(collections.get_member_refs("Favorites"), [{"game": "afm"}])

    def test_removing_one_table_leaves_the_others(self) -> None:
        collections = CollectionStore(str(self.path))
        collections.add_collection("Favorites")
        collections.add_member("Favorites", "mm", table_id="vpw")
        collections.add_member("Favorites", "mm", table_id="jp")

        collections.remove_member("Favorites", "mm", table_id="vpw")

        self.assertEqual(collections.get_member_refs("Favorites"),
                         [{"game": "mm", "table": "jp"}])

    def test_curated_order_survives_a_round_trip(self) -> None:
        collections = CollectionStore(str(self.path))
        collections.add_collection("Tournament")
        for game, table in (("c", "c1"), ("a", ""), ("b", "b2"), ("a", "a9")):
            collections.add_member("Tournament", game, table_id=table)
        self._saved(collections)

        self.assertEqual(CollectionStore(str(self.path)).get_member_refs("Tournament"),
                         [{"game": "c", "table": "c1"}, {"game": "a"},
                          {"game": "b", "table": "b2"}, {"game": "a", "table": "a9"}])

    def test_set_members_takes_ids_or_refs(self) -> None:
        """game_play_service writes ids; a curated save writes refs."""
        collections = CollectionStore(str(self.path))
        collections.add_collection("Last Played")

        collections.set_members("Last Played", ["b", "a"])
        self.assertEqual(collections.get_member_refs("Last Played"),
                         [{"game": "b"}, {"game": "a"}])

        collections.set_members("Last Played", [{"game": "x", "table": "x1"}])
        self.assertEqual(collections.get_member_refs("Last Played"),
                         [{"game": "x", "table": "x1"}])

    def test_a_member_with_no_game_is_dropped(self) -> None:
        self.path.write_text(json.dumps(
            {"schema": COLLECTIONS_SCHEMA,
             "collections": [{"name": "Odd", "type": "manual",
                              "members": ["", {"table": "orphan"}, {"game": "ok"}, 7]}]}),
            encoding="utf-8")

        self.assertEqual(CollectionStore(str(self.path)).get_member_refs("Odd"),
                         [{"game": "ok"}])

    def test_replacing_membership_with_ids_says_it_dropped_a_pin(self) -> None:
        """The Manager saves game ids, so editing a collection that holds a pin
        loses it. Correct for an editor that cannot show pins - but say so."""
        collections = CollectionStore(str(self.path))
        collections.add_collection("Fav")
        collections.add_member("Fav", "mm", table_id="vpw")

        with self.assertLogs("vpinfe.common.games.collection_store", "WARNING") as caught:
            collections.set_members("Fav", ["mm"])

        self.assertIn("mm", "\n".join(caught.output))
        self.assertEqual(collections.get_member_refs("Fav"), [{"game": "mm"}])

    def test_replacing_membership_that_holds_no_pin_is_quiet(self) -> None:
        collections = CollectionStore(str(self.path))
        collections.add_collection("Fav")
        collections.add_member("Fav", "mm")

        with self.assertNoLogs("vpinfe.common.games.collection_store", "WARNING"):
            collections.set_members("Fav", ["mm", "afm"])


class ExclusionTests(TempTree):
    """Exclusions are the other half of membership: filters and members say what is in,
    exclusions overrule both. Excluding a table is not the inverse of pinning one."""

    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"
        self.collections = CollectionStore(str(self.path))
        self.collections.add_collection("90s Bally")

    def test_a_game_and_a_table_are_both_excludable(self) -> None:
        self.collections.exclude("90s Bally", "popeye")
        self.collections.exclude("90s Bally", "afm", table_id="vr")

        self.assertEqual(self.collections.get_excluded_refs("90s Bally"),
                         [{"game": "popeye"}, {"game": "afm", "table": "vr"}])

    def test_the_same_exclusion_is_not_recorded_twice(self) -> None:
        self.collections.exclude("90s Bally", "popeye")
        self.collections.exclude("90s Bally", "popeye")

        self.assertEqual(len(self.collections.get_excluded_refs("90s Bally")), 1)

    def test_unexcluding_a_game_brings_back_its_tables_too(self) -> None:
        """Wanting the game back means wanting all of it back."""
        self.collections.exclude("90s Bally", "afm")
        self.collections.exclude("90s Bally", "afm", table_id="vr")
        self.collections.exclude("90s Bally", "popeye")

        self.collections.unexclude("90s Bally", "afm")

        self.assertEqual(self.collections.get_excluded_refs("90s Bally"),
                         [{"game": "popeye"}])

    def test_unexcluding_one_table_leaves_the_others(self) -> None:
        self.collections.exclude("90s Bally", "afm", table_id="vr")
        self.collections.exclude("90s Bally", "afm", table_id="beta")

        self.collections.unexclude("90s Bally", "afm", table_id="vr")

        self.assertEqual(self.collections.get_excluded_refs("90s Bally"),
                         [{"game": "afm", "table": "beta"}])

    def test_the_key_is_dropped_when_nothing_is_excluded(self) -> None:
        """An empty list in every record is noise in a file people read."""
        self.collections.exclude("90s Bally", "popeye")
        self.collections.unexclude("90s Bally", "popeye")
        self.collections.save()

        stored = json.loads(self.path.read_text(encoding="utf-8"))["collections"][0]
        self.assertNotIn("excluded", stored)

    def test_exclusions_survive_a_round_trip(self) -> None:
        self.collections.exclude("90s Bally", "popeye")
        self.collections.exclude("90s Bally", "afm", table_id="vr")
        self.collections.save()

        reopened = CollectionStore(str(self.path))
        self.assertEqual(reopened.get_excluded_refs("90s Bally"),
                         [{"game": "popeye"}, {"game": "afm", "table": "vr"}])

    def test_a_collection_can_hold_members_and_exclusions_at_once(self) -> None:
        """The hybrid the storage has to support even before the UI exposes it."""
        self.collections.add_member("90s Bally", "addams")
        self.collections.exclude("90s Bally", "popeye")
        self.collections.save()

        reopened = CollectionStore(str(self.path))
        self.assertEqual(reopened.get_member_refs("90s Bally"), [{"game": "addams"}])
        self.assertEqual(reopened.get_excluded_refs("90s Bally"), [{"game": "popeye"}])


class UnresolvedMemberReportingTests(TempTree):
    """A member nothing matched is named, not just counted.

    The count alone said 19 members had not resolved on a real library and gave no way
    to find them - and an unresolved member renders in the UI as raw hex, so the file is
    the only place to look. Not an error: the game may simply not be present yet, and
    the file converges as tables are seen.
    """

    def _store(self, members):
        return _collections(self.root / "collections.ini", {"Favorites": list(members)})

    def test_it_says_which_collection_and_which_ids(self) -> None:
        store = self._store(["vps-gone-1", "vps-gone-2"])
        with self.assertLogs("vpinfe.common.games.collection_store", "WARNING") as logged:
            store.migrate_membership_to_game_ids([])
        joined = "\n".join(logged.output)
        self.assertIn("Favorites", joined)
        self.assertIn("vps-gone-1", joined)
        self.assertIn("vps-gone-2", joined)

    def test_a_collection_that_resolves_says_nothing(self) -> None:
        store = self._store([])
        with self.assertNoLogs("vpinfe.common.games.collection_store", "WARNING"):
            store.migrate_membership_to_game_ids([])


if __name__ == "__main__":
    unittest.main()
