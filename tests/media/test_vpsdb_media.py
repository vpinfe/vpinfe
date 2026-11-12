import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import requests

from common.games import asset_origin
from common.online import cache_stamps
from common.online.vpsdb_cache import VPSDatabaseCache
from common.online.vpsdb_media import (
    REMOTE_KEYS,
    VPSMediaDownloader,
    offered,
    published_url,
)
from tests.support.library import fake_game
from tests.support.stamps import OwnStamps


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


OURS = b"\x89PNG the wheel vpinmediadb publishes"
THEIRS = b"\x89PNG a wheel someone drew themselves"


class OwnershipTests(unittest.TestCase):
    """A file identical to what vpinmediadb publishes is its file, ledger entry or not.

    vpinmediadb artwork loses its entry whenever a table folder is copied, an .info is
    regenerated, or the media came from another tool.
    """

    def _downloader(self, remote_md5: str):
        return VPSMediaDownloader(
            {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                       "wheel_md5": remote_md5}},
            playfieldvariant="table", playfieldresolution="1k", playfieldvideoresolution="1k",
        )

    def _run(self, on_disk: bytes, remote_md5: str):
        """Returns (result, bytes still on disk, whether a download was attempted)."""
        with TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wheel.png"
            wheel.write_bytes(on_disk)
            dl = self._downloader(remote_md5)
            with mock.patch.object(dl, "download_media_file") as fetch:
                result = dl.download_media(
                    "vps-1", dl.media_index["vps-1"], "wheel",
                    str(wheel), str(wheel))
            return result, wheel.read_bytes(), fetch.called

    def test_a_users_own_artwork_is_left_alone_and_never_claimed(self) -> None:
        """The bug this fixes: an unrecorded file was treated as ours, stamped with the
        remote hash without being downloaded, then overwritten on the next sync."""
        result, on_disk, fetched = self._run(THEIRS, _md5(OURS))

        self.assertIsNone(result, "returning nothing is what stops record() claiming it")
        self.assertEqual(on_disk, THEIRS, "the user's file must survive untouched")
        self.assertFalse(fetched)

    def test_our_own_artwork_stays_managed_without_a_ledger_entry(self) -> None:
        """The regression the naive fix would have caused: treating absence as the
        user's would freeze vpinmediadb art whose entry was lost, forever."""
        result, _, fetched = self._run(OURS, _md5(OURS))

        self.assertIsNotNone(result, "hashes match, so this is demonstrably our file")
        self.assertEqual(result[1], _md5(OURS))
        self.assertFalse(fetched, "already current - nothing to fetch")

    def test_without_a_remote_hash_we_cannot_prove_ownership(self) -> None:
        """Silence protects. An index entry with no md5 is not evidence."""
        result, on_disk, fetched = self._run(THEIRS, "")

        self.assertIsNone(result)
        self.assertEqual(on_disk, THEIRS)
        self.assertFalse(fetched)

    def test_a_missing_file_is_still_downloaded(self) -> None:
        """Ownership only gates files that already exist; a gap is still filled."""
        with TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wheel.png"
            dl = self._downloader(_md5(OURS))
            with mock.patch.object(dl, "download_media_file") as fetch:
                dl.download_media("vps-1", dl.media_index["vps-1"], "wheel",
                                  str(wheel), str(wheel))
            self.assertTrue(fetch.called)


OLD = b"\x89PNG the wheel vpinmediadb used to publish"


class UpdateDownloadedTests(unittest.TestCase):
    """Art we placed is replaced when the catalog replaces it, and nothing else is."""

    def _run(self, on_disk: bytes, recorded: str, *, update: bool = True,
             published: tuple[str, ...] = (), fails: bool = False):
        """Returns (result, bytes on disk after)."""
        with TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wheel.png"
            wheel.write_bytes(on_disk)
            dl = VPSMediaDownloader(
                {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                           "wheel_md5": _md5(OURS)}},
                playfieldvariant="table", playfieldresolution="1k",
                playfieldvideoresolution="1k", update_downloaded=update)

            def fetch(url, dest):
                if fails:
                    raise requests.ConnectionError("offline")
                Path(dest).write_bytes(OURS)

            with mock.patch("common.online.vpsdb_media.download_file", side_effect=fetch):
                result = dl.download_media("vps-1", dl.media_index["vps-1"], "wheel",
                                           str(wheel), str(wheel), recorded_md5=recorded,
                                           published=published)
            leftovers = sorted(path.name for path in Path(tmp).iterdir())
            self.assertEqual(leftovers, ["wheel.png"])
            return result, wheel.read_bytes()

    def test_ours_and_current_is_kept(self) -> None:
        result, on_disk = self._run(OURS, _md5(OURS))
        self.assertEqual(result[1], _md5(OURS))
        self.assertEqual(on_disk, OURS)

    def test_ours_and_stale_is_replaced(self) -> None:
        result, on_disk = self._run(OLD, _md5(OLD))
        self.assertEqual(on_disk, OURS)
        self.assertEqual(result[1], _md5(OURS), "the new hash is what gets recorded")

    def test_art_edited_since_it_was_recorded_is_left(self) -> None:
        result, on_disk = self._run(THEIRS, _md5(OLD))
        self.assertIsNone(result)
        self.assertEqual(on_disk, THEIRS)

    def test_art_with_no_ledger_entry_is_left(self) -> None:
        result, on_disk = self._run(OLD, "")
        self.assertIsNone(result)
        self.assertEqual(on_disk, OLD)

    def test_the_switch_off_leaves_stale_art(self) -> None:
        result, on_disk = self._run(OLD, _md5(OLD), update=False)
        self.assertIsNone(result)
        self.assertEqual(on_disk, OLD)

    def test_art_the_catalog_still_publishes_at_another_size_is_left(self) -> None:
        result, on_disk = self._run(OLD, _md5(OLD), published=(_md5(OLD),))
        self.assertIsNone(result)
        self.assertEqual(on_disk, OLD)

    def test_a_failed_replacement_keeps_the_file(self) -> None:
        result, on_disk = self._run(OLD, _md5(OLD), fails=True)
        self.assertIsNone(result)
        self.assertEqual(on_disk, OLD)

    def test_the_game_download_reads_the_recorded_hash(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Cactus Canyon (Bally 1998)"
            (root / "medias").mkdir(parents=True)
            (root / "medias" / "wheel.png").write_bytes(OLD)
            (root / f"{root.name}.info").write_text(json.dumps({"assets": {
                "medias/wheel.png": {"source": {"host": "vpinmediadb",
                                                "hash": _md5(OLD)}}}}), encoding="utf-8")
            dl = VPSMediaDownloader(
                {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                           "wheel_md5": _md5(OURS)}},
                playfieldvariant="table", playfieldresolution="1k",
                playfieldvideoresolution="1k", update_downloaded=True)
            meta = mock.Mock()
            with mock.patch("common.online.vpsdb_media.download_file",
                            side_effect=lambda url, dest: Path(dest).write_bytes(OURS)):
                dl.download_media_for_game(_game_at(root), "vps-1", meta)

            self.assertEqual((root / "medias" / "wheel.png").read_bytes(), OURS)
            meta.add_asset.assert_called_once_with(
                str(root / "medias" / "wheel.png"), "vpinmediadb", _md5(OURS))

    def test_a_file_another_host_placed_is_never_replaced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Cactus Canyon (Bally 1998)"
            (root / "medias").mkdir(parents=True)
            (root / "medias" / "wheel.png").write_bytes(OLD)
            (root / f"{root.name}.info").write_text(json.dumps({"assets": {
                "medias/wheel.png": {"source": {"host": asset_origin.RECORDED,
                                                "hash": _md5(OLD)}}}}), encoding="utf-8")
            dl = VPSMediaDownloader(
                {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                           "wheel_md5": _md5(OURS)}},
                playfieldvariant="table", playfieldresolution="1k",
                playfieldvideoresolution="1k", update_downloaded=True)
            meta = mock.Mock()
            with mock.patch("common.online.vpsdb_media.download_file",
                            side_effect=lambda url, dest: Path(dest).write_bytes(OURS)):
                dl.download_media_for_game(_game_at(root), "vps-1", meta)

            self.assertEqual((root / "medias" / "wheel.png").read_bytes(), OLD)
            meta.add_asset.assert_not_called()


class RemoteVocabularyTests(unittest.TestCase):
    """The keys we index vpinmediadb's manifest with are its words, not ours.

    A rename of our own media kinds once swept three of these in with them, and
    nothing failed: `download_media` returns None for a key an entry does not carry,
    so the whole symptom was art that never arrived. This is the guard - an entry
    carrying the manifest's real vocabulary, and every kind in it expected to land.
    """

    # One entry in the shape vpinmdb.json actually publishes: resolution buckets
    # holding bg/dmd/table/fss and the videos, the rest at the top level.
    ENTRY = {
        "1k": {"bg": "https://example.invalid/bg.png",
               "dmd": "https://example.invalid/dmd.png",
               "table": "https://example.invalid/table.png",
               "dmd_video": "https://example.invalid/dmd.mp4",
               "table_video": "https://example.invalid/table.mp4"},
        "4k": {"table": "https://example.invalid/table4k.png"},
        "wheel": "https://example.invalid/wheel.png",
        "cab": "https://example.invalid/cab.png",
        "flyer": "https://example.invalid/flyer.png",
        "audio": "https://example.invalid/audio.mp3",
        "realdmd": "https://example.invalid/realdmd.png",
        "realdmd_color": "https://example.invalid/realdmd-color.png",
    }

    def _fetched(self, kinds: set[str] | None = None) -> set[str]:
        with TemporaryDirectory() as tmp:
            game = fake_game(tmp, bg_image_path=None, dmd_image_path=None,
                             wheel_image_path=None, cab_image_path=None,
                             real_dmd_image_path=None, real_dmd_color_image_path=None,
                             flyer_image_path=None, playfield_image_path=None,
                             playfield_video_path=None, dmd_video_path=None,
                             audio_path=None)
            dl = VPSMediaDownloader({"vps-1": self.ENTRY}, playfieldvariant="table",
                                    playfieldresolution="1k",
                                    playfieldvideoresolution="1k")
            with mock.patch.object(dl, "download_media_file") as fetch:
                dl.download_media_for_game(game, "vps-1", kinds=kinds)
            folder = f" {Path(tmp).name}"
            return {Path(call.args[2]).name.replace(folder, "")
                    for call in fetch.call_args_list}

    def test_every_kind_the_manifest_offers_is_fetched(self) -> None:
        self.assertEqual(
            self._fetched(),
            {"(Backglass).png", "(DMD).png", "(Wheel).png", "(Cabinet).png", "(GameInfo).png",
             "(Audio).mp3", "(RealDMD).png", "(RealColorDMD).png", "(Playfield).png",
             "(DMD).mp4", "(Playfield).mp4"},
        )

    def test_new_art_takes_the_names_the_library_chose(self) -> None:
        from common.games import library_policy

        with mock.patch.object(library_policy, "_config_values",
                               return_value={"flyer_name": "vpinfe"}):
            self.assertIn("(Flyer).png", self._fetched({"flyer"}))

    def test_a_file_already_at_its_short_name_is_not_given_a_second_one(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Cactus Canyon (Bally 1998)"
            (root / "medias").mkdir(parents=True)
            (root / "medias" / "wheel.png").write_bytes(b"old")
            dl = VPSMediaDownloader(
                {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                           "wheel_md5": "new"}},
                playfieldvariant="table", playfieldresolution="1k",
                playfieldvideoresolution="1k", update_downloaded=True)
            with mock.patch.object(dl, "download_media_file") as fetch:
                dl.download_media_for_game(_game_at(root), "vps-1", kinds={"wheel"})

            fetch.assert_not_called()
            self.assertEqual(sorted(p.name for p in (root / "medias").iterdir()), ["wheel.png"])

    def test_a_file_in_any_spelling_is_found_when_the_game_knows_no_paths(self) -> None:
        for held in ("wheel.png", "(Wheel) Cactus Canyon (Bally 1998).jpg"):
            with self.subTest(held=held), TemporaryDirectory() as tmp:
                root = Path(tmp) / "Cactus Canyon (Bally 1998)"
                (root / "medias").mkdir(parents=True)
                (root / "medias" / held).write_bytes(b"old")
                dl = VPSMediaDownloader(
                    {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                               "wheel_md5": "new"}},
                    playfieldvariant="table", playfieldresolution="1k",
                    playfieldvideoresolution="1k")
                with mock.patch.object(dl, "download_media_file") as fetch:
                    dl.download_media_for_game(fake_game(root, root.name), "vps-1",
                                               kinds={"wheel"})

                fetch.assert_not_called()
                self.assertEqual([p.name for p in (root / "medias").iterdir()], [held])

    def test_a_kind_left_out_is_never_fetched(self) -> None:
        self.assertEqual(self._fetched({"wheel", "playfield"}),
                         {"(Wheel).png", "(Playfield).png"})

    def test_the_match_download_asks_for_the_kinds_the_library_keeps(self) -> None:
        from common.online.vpsdb import VPSdb

        vps = VPSdb.__new__(VPSdb)
        vps._media_downloader = mock.Mock()
        with mock.patch("common.games.media_fill.kept_kinds", return_value={"wheel"}):
            vps.download_media_for_game(mock.Mock(), "vps-1")
        self.assertEqual(
            vps._media_downloader.download_media_for_game.call_args.kwargs["kinds"],
            {"wheel"})

    def test_the_manifests_words_are_pinned_against_a_rename_of_ours(self) -> None:
        """The whole mapping, so changing one of our kind names fails here and says
        why rather than quietly asking vpinmediadb for a key it has never had."""
        self.assertEqual(REMOTE_KEYS, {
            "backglass": "bg", "scoreview": "dmd", "scoreview_video": "dmd_video",
            "playfield": "table", "playfield_fss": "fss",
            "playfield_video": "table_video", "wheel": "wheel", "cab": "cab",
            "flyer": "flyer", "audio": "audio", "real_dmd": "realdmd",
            "real_dmd_color": "realdmd_color",
        })

    def test_a_kind_the_catalog_does_not_carry_is_absent_rather_than_empty(self) -> None:
        """vpinmediadb has no topper at all. Reporting it as an option with nothing
        behind it would put a dead choice in front of someone."""
        self.assertNotIn("topper", offered({"vps-1": self.ENTRY}, "vps-1"))
        self.assertIsNone(published_url({"vps-1": self.ENTRY}, "vps-1", "topper"))

    def test_every_size_an_entry_carries_is_offered_largest_first(self) -> None:
        """The configured resolution is right for an unattended refresh and wrong for
        someone choosing a picture, so the catalog reports both."""
        playfield = offered({"vps-1": self.ENTRY}, "vps-1")["playfield"]
        self.assertEqual([item["size"] for item in playfield], ["4k", "1k"])
        published = published_url({"vps-1": self.ENTRY}, "vps-1", "playfield")
        assert published is not None
        self.assertEqual(published[0], "https://example.invalid/table4k.png")


def _game_at(root: Path):
    """Enough of a Game for download_media_for_game. Every media path points at the
    canonical name; only the wheel exists on disk in these tests."""
    paths = {"bg_image_path": "bg.png", "dmd_image_path": "dmd.png",
             "wheel_image_path": "wheel.png", "cab_image_path": "cab.png",
             "real_dmd_image_path": "realdmd.png",
             "real_dmd_color_image_path": "realdmd-color.png",
             "flyer_image_path": "flyer.png", "playfield_image_path": "table.png",
             "dmd_video_path": "dmd.mp4", "playfield_video_path": "table.mp4",
             "audio_path": "audio.mp3"}
    game = fake_game(root, root.name)
    for attr, name in paths.items():
        setattr(game, attr, str(root / "medias" / name))
    return game


class RecordingTests(unittest.TestCase):
    """What reaches the assets ledger, going through the real recording path."""

    def _downloader(self, remote_md5: str):
        return VPSMediaDownloader(
            {"vps-1": {"wheel": "https://example.invalid/wheel.png",
                       "wheel_md5": remote_md5}},
            playfieldvariant="table", playfieldresolution="1k", playfieldvideoresolution="1k")

    def test_a_file_we_never_wrote_is_not_recorded_as_ours(self) -> None:
        """download_media returns None for anything it declined to touch, and record()
        writes nothing for None - so the ledger cannot claim a user's artwork."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Cactus Canyon (Bally 1998)"
            (root / "medias").mkdir(parents=True)
            (root / "medias" / "wheel.png").write_bytes(THEIRS)
            dl = self._downloader(_md5(OURS))
            meta = mock.Mock()
            with mock.patch.object(dl, "download_media_file"):
                dl.download_media_for_game(_game_at(root), "vps-1", meta)

            meta.add_asset.assert_not_called()

    def test_a_file_we_placed_is_recorded_by_path_with_its_hash(self) -> None:
        """The entry is keyed by where the file went, not by which kind it is - a kind
        cannot say which build's artwork it means."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "Cactus Canyon (Bally 1998)"
            (root / "medias").mkdir(parents=True)
            (root / "medias" / "wheel.png").write_bytes(OURS)
            dl = self._downloader(_md5(OURS))
            meta = mock.Mock()
            with mock.patch.object(dl, "download_media_file"):
                dl.download_media_for_game(_game_at(root), "vps-1", meta)

            meta.add_asset.assert_called_once_with(
                str(root / "medias" / "wheel.png"), "vpinmediadb", _md5(OURS))


class VpsCacheTests(OwnStamps):
    def test_vps_cache_loads_local_list_without_network_version(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "vpsdb.json").write_text(
                json.dumps([{"id": "vps-1", "name": "Example"}]),
                encoding="utf-8",
            )
            cache = VPSDatabaseCache(
                config_dir,
                db_url="https://example.invalid/db.json",
                last_update_url="https://example.invalid/last.json",
            )

            with mock.patch.object(cache, "fetch_last_update", return_value=None):
                self.assertEqual(cache.ensure_current(), [{"id": "vps-1", "name": "Example"}])

    def _cache(self, tmp: str, held: str | None = None) -> VPSDatabaseCache:
        if held is not None:
            cache_stamps.put("vpsdb", version=held)
        (Path(tmp) / "vpsdb.json").write_text("[]", encoding="utf-8")
        return VPSDatabaseCache(Path(tmp), db_url="https://example.invalid/db.json",
                                last_update_url="https://example.invalid/last.json")

    def test_a_failed_download_leaves_the_held_version(self) -> None:
        import requests

        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp, held="1000")
            with mock.patch("common.online.vpsdb_cache.get_bytes",
                            side_effect=requests.ConnectionError("offline")):
                cache._update_if_needed("2000")
            self.assertEqual(cache_stamps.get("vpsdb", "version"), "1000")

    def test_versions_compare_as_numbers(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp, held="999")
            with mock.patch("common.online.vpsdb_cache.get_bytes",
                            return_value=b"[]") as fetched:
                cache._update_if_needed("1000")
            fetched.assert_called_once()
            self.assertEqual(cache_stamps.get("vpsdb", "version"), "1000")

    def test_the_held_version_is_not_downloaded_again(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = self._cache(tmp, held="1000")
            with mock.patch("common.online.vpsdb_cache.get_bytes") as fetched:
                cache._update_if_needed("1000")
            fetched.assert_not_called()

if __name__ == "__main__":
    unittest.main()
