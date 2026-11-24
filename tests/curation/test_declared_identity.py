"""What a file claims to be, declared by whatever sent it.

Whatever delivered the bytes knows what it asked for. VPinFE used to throw that away and
re-derive it from the bytes, and that guess measured 32% top-1 against 32% random, 57%
confidently wrong. So the sender says, and we record.

What is pinned here is the part that keeps the record trustworthy: the accepted set is
closed, there is no way to say "I guessed", a weaker claim cannot overwrite a stronger one,
and a claim that names a record without saying how it is known is refused rather than
stored.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from common.games import identity_claims as claims
from common.games.info_file import MetaConfig
from tests.support.library import TempTree, write_game


def _identity(**kw) -> claims.DeclaredIdentity:
    return claims.DeclaredIdentity(**kw)


class VocabularyTests(unittest.TestCase):
    def test_construction_and_declared_rank_together(self) -> None:
        """Neither inferred anything - we built it, or we watched it arrive."""
        self.assertEqual(claims.rank(claims.CONSTRUCTION), claims.rank(claims.DECLARED))

    def test_a_person_choosing_ranks_below_a_witness(self) -> None:
        self.assertGreater(claims.rank(claims.USER), claims.rank(claims.DECLARED))

    def test_a_tie_made_for_a_person_ranks_below_theirs(self) -> None:
        self.assertGreater(claims.rank(claims.EXACT), claims.rank(claims.USER))
        self.assertTrue(claims.outranks(claims.USER, claims.EXACT))
        self.assertFalse(claims.outranks(claims.EXACT, claims.USER))

    def test_an_unknown_basis_ranks_last(self) -> None:
        self.assertGreater(claims.rank("vibes"), claims.rank(claims.USER))

    def test_there_is_no_value_for_a_guess(self) -> None:
        """The closed set is what makes this safe to accept over HTTP."""
        self.assertEqual(claims.ACCEPTED_FROM_CALLERS, (claims.DECLARED, claims.USER))
        self.assertNotIn("auto", claims.ACCEPTED_FROM_CALLERS)

    def test_a_client_cannot_claim_construction(self) -> None:
        """We build files; nobody else gets to say they did."""
        self.assertNotIn(claims.CONSTRUCTION, claims.ACCEPTED_FROM_CALLERS)
        problems = _identity(vps_file_id="f", host_item_id="h",
                             confirmed_by=claims.CONSTRUCTION).problems()
        self.assertTrue(problems)

    def test_an_equal_claim_does_not_overwrite(self) -> None:
        """Re-importing the same file must not churn the .info."""
        self.assertFalse(claims.outranks(claims.DECLARED, claims.DECLARED))
        self.assertTrue(claims.outranks(claims.DECLARED, claims.USER))
        self.assertFalse(claims.outranks(claims.USER, claims.DECLARED))


class ValidationTests(unittest.TestCase):
    def test_saying_nothing_is_allowed(self) -> None:
        """How a caller that inferred an identity stays out of the record."""
        identity = _identity()
        self.assertTrue(identity.is_empty)
        self.assertEqual(identity.problems(), [])

    def test_naming_a_record_needs_a_basis(self) -> None:
        problems = _identity(vps_file_id="f", host_item_id="h").problems()
        self.assertTrue(any("confirmed_by" in p for p in problems), problems)

    def test_a_basis_with_no_identity_says_how_nothing_is_known(self) -> None:
        self.assertTrue(_identity(confirmed_by=claims.DECLARED).problems())

    def test_a_vps_file_id_needs_the_item_it_came_from(self) -> None:
        """One VPS record was measured fronting eleven artifacts, so the record id alone
        cannot say which one arrived."""
        problems = _identity(vps_file_id="f", confirmed_by=claims.DECLARED).problems()
        self.assertTrue(any("host_item_id" in p for p in problems), problems)

    def test_a_basis_outside_the_set_is_refused(self) -> None:
        for basis in ("auto", "guess", "probably", claims.EXACT):
            self.assertTrue(_identity(game_id="g", confirmed_by=basis).problems(), basis)

    def test_declaring_only_a_game_is_fine(self) -> None:
        """What dropping on a game row means, and it names no upstream record."""
        self.assertEqual(_identity(game_id="g", confirmed_by=claims.USER).problems(), [])


class SourceBlockTests(unittest.TestCase):
    def test_it_records_which_record_this_is_not_how_it_arrived(self) -> None:
        block = claims.source_block(
            _identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                      confirmed_by=claims.DECLARED, game_id="g", table_id="t"),
            md5="abc")
        self.assertEqual(block, {"host": "vpsdb", "host_item_id": "h1",
                                 "vps_file_id": "f1", "confirmed_by": "declared",
                                 "hash": "abc"})
        # game_id and table_id say where it goes, not which record it is.
        self.assertNotIn("game_id", block)

    def test_empty_fields_are_left_out_rather_than_written_blank(self) -> None:
        self.assertEqual(claims.source_block(_identity(host="user")), {"host": "user"})


class RecordingTests(TempTree):
    """The claim reaching the `.info`, through the writer every import uses."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, "Example", info={
            "Info": {"Name": "Example"}, "VPinFE": {}, "User": {}})
        self.info = self.folder / "Example.info"
        self.asset = self.folder / "medias" / "wheel.png"
        self.asset.parent.mkdir(parents=True, exist_ok=True)
        self.asset.write_bytes(b"png")

    def _source(self) -> dict:
        meta = MetaConfig(str(self.info))
        assets: dict[str, Any] = meta.data.get("assets", {})
        first: dict[str, Any] = next(iter(assets.values()), {})
        return first.get("source", {})

    def test_a_declared_identity_lands_in_the_source_block(self) -> None:
        MetaConfig(str(self.info)).add_asset(
            str(self.asset), "vpsdb",
            identity=_identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                               confirmed_by=claims.DECLARED))
        self.assertEqual(self._source().get("vps_file_id"), "f1")
        self.assertEqual(self._source().get("confirmed_by"), "declared")

    def test_the_version_the_catalog_lists_is_recorded_with_the_tie(self) -> None:
        catalog = [{"id": "e1", "b2sFiles": [{"id": "f1", "version": "2.1"}]}]
        with patch("common.games.game_service.load_vpsdb", return_value=catalog):
            MetaConfig(str(self.info)).add_asset(
                str(self.asset), "vpsdb",
                identity=_identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                                   confirmed_by=claims.DECLARED))
        self.assertEqual(self._source().get("vps_version"), "2.1")

    def test_a_record_the_catalog_does_not_hold_records_no_version(self) -> None:
        with patch("common.games.game_service.load_vpsdb", return_value=[]):
            MetaConfig(str(self.info)).add_asset(
                str(self.asset), "vpsdb",
                identity=_identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                                   confirmed_by=claims.DECLARED))
        self.assertNotIn("vps_version", self._source())

    def test_no_identity_writes_what_it_always_did(self) -> None:
        MetaConfig(str(self.info)).add_asset(str(self.asset), "user", "hash1")
        self.assertEqual(self._source(), {"host": "user", "hash": "hash1"})

    def test_a_weaker_claim_does_not_overwrite_a_stronger_one(self) -> None:
        MetaConfig(str(self.info)).add_asset(
            str(self.asset), "vpsdb",
            identity=_identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                               confirmed_by=claims.DECLARED))
        MetaConfig(str(self.info)).add_asset(
            str(self.asset), "user",
            identity=_identity(host="user", host_item_id="h2", vps_file_id="f2",
                               confirmed_by=claims.USER))
        self.assertEqual(self._source().get("vps_file_id"), "f1",
                         "a person picking must not overwrite a witnessed download")

    def test_a_stronger_claim_does(self) -> None:
        MetaConfig(str(self.info)).add_asset(
            str(self.asset), "user",
            identity=_identity(host="user", host_item_id="h2", vps_file_id="f2",
                               confirmed_by=claims.USER))
        MetaConfig(str(self.info)).add_asset(
            str(self.asset), "vpsdb",
            identity=_identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                               confirmed_by=claims.DECLARED))
        self.assertEqual(self._source().get("vps_file_id"), "f1")


class ImportRecordingTests(TempTree):
    """A declared identity surviving a real import, through the code the endpoint calls."""

    def _plan_and_import(self, identity):
        import zipfile

        from common.uploads.asset_analyzer_service import analyze_upload_session
        from common.uploads.asset_import_service import (
            build_import_plan,
            execute_import_plan,
        )

        game = write_game(self.root, "Target", info={
            "Info": {"Name": "Target"}, "vpinfe": {"game_id": "tgt1"}, "User": {}})
        session = self.root / "session"
        session.mkdir()
        archive = session / "drop.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Target.directb2s", "b2s bytes")

        analysis, source = analyze_upload_session(session)
        plan = build_import_plan(analysis, game_dir=game)
        declared = {"Target.directb2s": identity} if identity else None
        execute_import_plan(plan, source, declared=declared)
        return MetaConfig(str(game / "Target.info"))

    def _import_again(self, identity, body: str):
        """Another drop of the same file name into the game `_plan_and_import` made."""
        import zipfile

        from common.uploads.asset_analyzer_service import analyze_upload_session
        from common.uploads.asset_import_service import (
            build_import_plan,
            execute_import_plan,
        )

        game = self.root / "Target"
        session = self.root / "session2"
        session.mkdir()
        with zipfile.ZipFile(session / "drop.zip", "w") as zf:
            zf.writestr("Target.directb2s", body)
        analysis, source = analyze_upload_session(session)
        plan = build_import_plan(analysis, game_dir=game)
        execute_import_plan(plan, source, declared={"Target.directb2s": identity})
        return MetaConfig(str(game / "Target.info"))

    def _tie_of(self, meta) -> dict:
        [entry] = [a.get("source", {}) for a in meta.data.get("assets", {}).values()]
        return entry

    def test_importing_a_later_version_of_the_release_held_records_the_version(self) -> None:
        """The same release at the same rank keeps the first witness, but the version the
        tie measures an update from is the one VPS lists for the file now held."""
        identity = _identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                             confirmed_by=claims.DECLARED)
        release = {"id": "f1", "version": "1.0"}
        listing = [{"id": "e1", "b2sFiles": [release]}]
        with patch("common.games.game_service.load_vpsdb", return_value=listing):
            first = self._plan_and_import(identity)
        release["version"] = "1.2"
        with patch("common.games.game_service.load_vpsdb", return_value=listing):
            again = self._import_again(identity, "newer b2s bytes")

        self.assertEqual("1.0", self._tie_of(first)["vps_version"])
        self.assertEqual("1.2", self._tie_of(again)["vps_version"])

    def test_importing_the_same_version_again_leaves_the_record_alone(self) -> None:
        identity = _identity(host="vpsdb", host_item_id="h1", vps_file_id="f1",
                             confirmed_by=claims.DECLARED)
        listing = [{"id": "e1", "b2sFiles": [{"id": "f1", "version": "1.0"}]}]
        with patch("common.games.game_service.load_vpsdb", return_value=listing):
            first = self._plan_and_import(identity)
            before = dict(self._tie_of(first))
            again = self._import_again(identity, "same b2s bytes")

        self.assertEqual(before, self._tie_of(again))

    def test_a_declared_identity_reaches_the_info(self) -> None:
        meta = self._plan_and_import(_identity(
            host="vpsdb", host_item_id="h1", vps_file_id="f1",
            confirmed_by=claims.DECLARED))
        sources = [a.get("source", {}) for a in meta.data.get("assets", {}).values()]
        self.assertTrue(any(s.get("vps_file_id") == "f1" for s in sources), sources)
        self.assertTrue(any(s.get("confirmed_by") == "declared" for s in sources), sources)

    def test_an_import_that_declares_nothing_writes_no_claim(self) -> None:
        """Absence means never examined, which is a different state from unmatched."""
        meta = self._plan_and_import(None)
        for asset in meta.data.get("assets", {}).values():
            self.assertNotIn("confirmed_by", asset.get("source", {}))

    def test_a_name_the_plan_did_not_import_is_ignored(self) -> None:
        """A bundle may carry more than the user selected; that is not an error."""
        meta = self._plan_and_import(_identity(
            host="vpsdb", host_item_id="h1", vps_file_id="f1",
            confirmed_by=claims.DECLARED))
        self.assertTrue(meta.data.get("assets"))


class TableRecordingTests(TempTree):
    """A table's release is read off the table's own entry, so that is where a claim goes."""

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, "Example", info={
            "Info": {"Name": "Example"}, "VPinFE": {}, "User": {}})
        self.meta = MetaConfig(str(self.folder / "Example.info"))

    def _source(self) -> dict:
        meta = MetaConfig(str(self.folder / "Example.info"))
        entries = meta.data.get("tables", {})
        return next((one.get("source", {}) for one in entries.values()
                     if one.get("filename") == "Example.vpx"), {})

    def test_a_declared_table_is_tied_on_its_own_entry(self) -> None:
        self.assertTrue(self.meta.tie_table_to_declared(
            "Example.vpx", _identity(host="vpuniverse", host_item_id="h1", vps_file_id="f1",
                                     confirmed_by=claims.DECLARED)))
        self.assertEqual(self._source().get("vps_file_id"), "f1")
        self.assertEqual(self._source().get("confirmed_by"), "declared")

    def test_a_declaration_is_not_churned_by_an_equal_one(self) -> None:
        self.meta.tie_table_to_declared(
            "Example.vpx", _identity(host="vpuniverse", host_item_id="h1",
                                     vps_file_id="first", confirmed_by=claims.DECLARED))
        self.assertFalse(self.meta.tie_table_to_declared(
            "Example.vpx", _identity(host="vpuniverse", host_item_id="h2",
                                     vps_file_id="second", confirmed_by=claims.DECLARED)))
        self.assertEqual(self._source().get("vps_file_id"), "first")


if __name__ == "__main__":
    unittest.main()
