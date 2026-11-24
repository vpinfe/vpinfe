"""Manufacturer logos, keyed by metadata and read from one flat folder.

The rules under test: a file is found by its name without regard to case, the slug drops
corporate boilerplate so VPSdb's name variants converge, and the alias map catches what no
rule can.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from common.media.manufacturer_logos import (
    add_logos,
    configure_manufacturer_logos,
    manufacturer_logo_file,
    manufacturer_logo_url,
    manufacturer_slug,
    resolve_logos_dir,
)
from tests.support.library import fake_game
from tests.support.theme_server import ThemeServer


def _logos(test: unittest.TestCase, tmp: str) -> Path:
    root = Path(tmp) / "manufacturer_logos"
    root.mkdir(parents=True)
    configure_manufacturer_logos(root)
    test.addCleanup(configure_manufacturer_logos, None)
    return root


class AddLogosTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = _logos(self, self._tmp.name)
        self.source = Path(self._tmp.name) / "source"
        self.source.mkdir()

    def image(self, name: str, body: bytes = b"new") -> Path:
        path = self.source / name
        path.write_bytes(body)
        return path

    def test_a_logo_for_a_maker_in_the_library_is_copied_under_the_makers_name(self) -> None:
        done = add_logos(["Williams"], [("Williams", self.image("Williams.png"))])

        self.assertEqual(done, {"placed": ["Williams.png"], "held": []})
        self.assertEqual((self.folder / "williams.png").read_bytes(), b"new")
        self.assertIsNotNone(manufacturer_logo_file("williams"))

    def test_an_image_no_maker_asks_for_is_not_copied(self) -> None:
        done = add_logos(["Williams"], [("Kodi", self.image("Kodi.png"))])

        self.assertEqual(done, {"placed": [], "held": []})
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_maker_found_by_the_slug_rule_is_found_by_its_logo(self) -> None:
        done = add_logos(["Bally Manufacturing Corporation"],
                         [("Bally", self.image("Bally.png"))])

        self.assertEqual(done["placed"], ["Bally.png"])

    def test_a_logo_the_folder_already_serves_is_kept(self) -> None:
        (self.folder / "williams.jpg").write_bytes(b"mine")

        done = add_logos(["Williams"], [("Williams", self.image("Williams.png"))])

        self.assertEqual(done, {"placed": [], "held": ["Williams.png"]})
        self.assertEqual((self.folder / "williams.jpg").read_bytes(), b"mine")
        self.assertFalse((self.folder / "williams.png").exists())

    def test_a_makers_alias_decides_which_image_serves_it(self) -> None:
        (self.folder / "manufacturers.json").write_text(
            json.dumps({"D. Gottlieb & Co.": "gottlieb"}), encoding="utf-8")

        done = add_logos(["D. Gottlieb & Co."], [("Gottlieb", self.image("Gottlieb.png"))])

        self.assertEqual(done["placed"], ["Gottlieb.png"])

    def test_the_built_in_alias_serves_a_maker_with_no_logo_of_its_own(self) -> None:
        done = add_logos(["Taito do Brasil"], [("Taito", self.image("Taito.png"))])

        self.assertEqual(done["placed"], ["Taito.png"])
        self.assertEqual(manufacturer_logo_url("Taito do Brasil"), "/manufacturers/taito/logo")

    def test_a_name_cannot_put_a_file_outside_the_folder(self) -> None:
        done = add_logos(["x", "a b"], [("../x", self.image("x.png")),
                                        ("a/b", self.image("b.png"))])

        self.assertEqual(done["placed"], ["x.png", "b.png"])
        self.assertEqual(sorted(one.name for one in self.folder.iterdir()), ["a-b.png", "x.png"])
        self.assertFalse((self.folder.parent / "x.png").exists())

    def test_a_file_that_is_not_an_image_is_left(self) -> None:
        done = add_logos(["Williams"], [("Williams", self.image("Williams.txt"))])

        self.assertEqual(done, {"placed": [], "held": []})

    def test_the_folder_is_made_when_it_is_not_there_yet(self) -> None:
        configure_manufacturer_logos(Path(self._tmp.name) / "later" / "logos")

        add_logos(["Williams"], [("Williams", self.image("Williams.png"))])

        self.assertTrue((Path(self._tmp.name) / "later" / "logos" / "williams.png").is_file())

    def test_with_no_folder_set_it_says_so(self) -> None:
        configure_manufacturer_logos(None)

        with self.assertRaises(FileNotFoundError):
            add_logos(["Williams"], [("Williams", self.image("Williams.png"))])


class SlugTests(unittest.TestCase):
    def test_corporate_suffixes_drop_so_name_variants_converge(self) -> None:
        for variant in ("Bally", "Bally Manufacturing",
                        "Bally Manufacturing Corporation"):
            self.assertEqual(manufacturer_slug(variant), "bally")
        for variant in ("Williams", "Williams Electronics"):
            self.assertEqual(manufacturer_slug(variant), "williams")

    def test_multi_word_brands_keep_their_words(self) -> None:
        self.assertEqual(manufacturer_slug("Data East"), "data-east")
        self.assertEqual(manufacturer_slug("Chicago Coin"), "chicago-coin")
        self.assertEqual(manufacturer_slug("Juegos Populares"), "juegos-populares")

    def test_a_name_that_is_all_suffixes_keeps_itself(self) -> None:
        """"Williams Electronics" must never collide with a brand literally
        named "Electronics"."""
        self.assertEqual(manufacturer_slug("Electronics"), "electronics")

    def test_punctuation_never_reaches_the_filename(self) -> None:
        self.assertEqual(manufacturer_slug("D. Gottlieb & Co."), "d-gottlieb")

    def test_the_wider_suffixes_drop_as_written(self) -> None:
        for name, slug in (("Playmatic S.A.", "playmatic"),
                           ("Recreativos Franco S.A.L.", "recreativos-franco"),
                           ("Stern Pinball LLC", "stern-pinball"),
                           ("Bally Wulff GmbH", "bally-wulff"),
                           ("Maibesa Ltda", "maibesa")):
            self.assertEqual(manufacturer_slug(name), slug)

    def test_the_letters_of_a_suffix_stay_in_a_name_that_uses_them(self) -> None:
        self.assertEqual(manufacturer_slug("Sega"), "sega")
        self.assertEqual(manufacturer_slug("Sal Gonzalez"), "sal-gonzalez")
        self.assertEqual(manufacturer_slug("S.A."), "s-a")
        self.assertEqual(manufacturer_slug("Premier U.S.A."), "premier-u-s-a")
        self.assertEqual(manufacturer_slug("Sonic S.A.R.L."), "sonic-s-a-r-l")

    def test_a_slug_is_its_own_slug_whatever_the_name_it_came_from(self) -> None:
        for name in ("Electronics S.A.", "S.A. Inc", "Co", "Premier U.S.A.", "Taito do Brasil",
                     "Williams Electronics", "S.A."):
            slug = manufacturer_slug(name)
            self.assertEqual(manufacturer_slug(slug), slug, name)


class LookupTests(unittest.TestCase):
    def test_a_logo_is_found_by_the_manufacturers_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "bally.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Bally Manufacturing"),
                             "/manufacturers/bally/logo")

    def test_case_and_the_extensions_case_do_not_matter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "Data East.PNG").write_bytes(b"png")
            (root / "WILLIAMS Electronics.jpg").write_bytes(b"jpg")

            self.assertEqual(manufacturer_logo_url("data east"), "/manufacturers/data-east/logo")
            self.assertEqual(manufacturer_logo_file("williams"),
                             root / "WILLIAMS Electronics.jpg")

    def test_names_compare_with_their_spaces_removed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "GamePlan.png").write_bytes(b"png")
            (root / "Data East.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_file("game-plan"), root / "GamePlan.png")
            self.assertEqual(manufacturer_logo_url("Game Plan"), "/manufacturers/game-plan/logo")
            self.assertEqual(manufacturer_logo_url("DataEast"), "/manufacturers/dataeast/logo")
            self.assertEqual(manufacturer_logo_file("data-east"), root / "Data East.png")

    def test_taito_do_brasil_finds_the_taito_logo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "Taito.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Taito do Brasil"),
                             "/manufacturers/taito/logo")
            self.assertEqual(manufacturer_logo_file("taito"), root / "Taito.png")

    def test_a_logo_named_for_taito_do_brasil_is_not_shadowed_by_the_alias_file(self) -> None:
        """The folder's own alias map wins over the one that ships."""
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "Taito.png").write_bytes(b"png")
            (root / "Sao Paulo.png").write_bytes(b"png")
            (root / "manufacturers.json").write_text(
                json.dumps({"Taito do Brasil": "sao-paulo"}), encoding="utf-8")

            self.assertEqual(manufacturer_logo_url("Taito do Brasil"),
                             "/manufacturers/sao-paulo/logo")

    def test_a_logo_of_its_own_comes_before_the_alias_that_ships(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "Taito do Brasil.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Taito do Brasil"),
                             "/manufacturers/taito-do-brasil/logo")

            (root / "Taito.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Taito do Brasil"),
                             "/manufacturers/taito-do-brasil/logo")
            self.assertEqual(manufacturer_logo_file("taito-do-brasil"),
                             root / "Taito do Brasil.png")

    def test_a_file_named_for_the_slug_beats_one_that_only_shares_its_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "DataEast.png").write_bytes(b"png")
            (root / "data-east.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_file("data-east"), root / "data-east.png")
            self.assertEqual(manufacturer_logo_file("dataeast"), root / "DataEast.png")

    def test_every_name_that_matched_before_still_does(self) -> None:
        pairs = (("Williams Electronics", "williams.png"),
                 ("Bally Manufacturing Corporation", "Bally.png"),
                 ("D. Gottlieb & Co.", "D Gottlieb.png"),
                 ("Mylstar Electronics, Incorporated", "Mylstar.png"),
                 ("Mr. Game", "Mr Game.png"),
                 ("Data East", "Data East.PNG"),
                 ("Chicago Coin", "chicago-coin.png"))
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            for _, file in pairs:
                (root / file).write_bytes(b"png")

            for name, file in pairs:
                found = manufacturer_logo_url(name)
                self.assertIsNotNone(found, f"{name} lost {file}")
                assert found is not None
                self.assertEqual(manufacturer_logo_file(found.split("/")[2]), root / file)

    def test_a_folder_that_is_not_an_image_is_not_a_logo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "stern.png").mkdir()
            (root / "stern.txt").write_bytes(b"text")

            self.assertIsNone(manufacturer_logo_url("Stern"))

    def test_a_file_added_later_shows_without_a_restart(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            self.assertIsNone(manufacturer_logo_url("Stern"))

            (root / "stern.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Stern"), "/manufacturers/stern/logo")

    def test_two_images_for_one_name_pick_the_same_one_every_time(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "stern.jpg").write_bytes(b"jpg")
            (root / "Stern.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_file("stern"), root / "Stern.png")

    def test_the_alias_map_redirects(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "manufacturers.json").write_text(
                json.dumps({"D. Gottlieb & Co.": "gottlieb"}), encoding="utf-8")
            (root / "gottlieb.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("D. Gottlieb & Co."),
                             "/manufacturers/gottlieb/logo")

            (root / "manufacturers.json").write_text(
                json.dumps({"d-gottlieb": "premier"}), encoding="utf-8")
            (root / "premier.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("D. Gottlieb & Co."),
                             "/manufacturers/premier/logo")

    def test_no_file_no_name_or_no_folder_all_mean_none(self) -> None:
        with TemporaryDirectory() as tmp:
            _logos(self, tmp)
            self.assertIsNone(manufacturer_logo_url("Zaccaria"))
            self.assertIsNone(manufacturer_logo_url(""))
        configure_manufacturer_logos(None)
        self.assertIsNone(manufacturer_logo_url("Bally"))

    def test_a_slug_that_is_not_one_finds_no_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "bally.png").write_bytes(b"png")

            self.assertIsNone(manufacturer_logo_file("../bally"))
            self.assertIsNone(manufacturer_logo_file("Bally"))

    def test_a_broken_alias_file_degrades_to_no_aliases(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "manufacturers.json").write_text("not json", encoding="utf-8")
            (root / "bally.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Bally"), "/manufacturers/bally/logo")

    def test_an_empty_alias_value_is_a_placeholder_not_an_alias(self) -> None:
        """A hand-edited TODO entry must never erase a working slug."""
        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "manufacturers.json").write_text(
                json.dumps({"Bally Manufacturing": "", "Bally Wulff": "  "}),
                encoding="utf-8")
            (root / "bally.png").write_bytes(b"png")

            self.assertEqual(manufacturer_logo_url("Bally Manufacturing"),
                             "/manufacturers/bally/logo")


class ReportTests(unittest.TestCase):
    def test_the_report_shows_slug_alias_and_resolution_per_name(self) -> None:
        from common.media.manufacturer_logos import manufacturer_report

        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "manufacturers.json").write_text(
                json.dumps({"Premier Technology": "gottlieb"}), encoding="utf-8")
            (root / "gottlieb.png").write_bytes(b"png")
            (root / "bally.png").write_bytes(b"png")

            rows = {row["name"]: row for row in manufacturer_report(
                ["Bally Manufacturing", "Premier Technology", "Bally Wulff"])}

        self.assertEqual(rows["Bally Manufacturing"],
                         {"name": "Bally Manufacturing", "slug": "bally",
                          "aliased_to": None, "logo": "/manufacturers/bally/logo"})
        self.assertEqual(rows["Premier Technology"]["aliased_to"], "gottlieb")
        self.assertEqual(rows["Premier Technology"]["logo"], "/manufacturers/gottlieb/logo")
        self.assertIsNone(rows["Bally Wulff"]["logo"],
                          "a miss is visible, never a fuzzy match")

    def test_the_report_exposes_an_alias_bypassing_the_named_file(self) -> None:
        """The invisible failure the report exists for: your file, shadowed by an alias."""
        from common.media.manufacturer_logos import manufacturer_report

        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "manufacturers.json").write_text(
                json.dumps({"Premier Technology": "gottlieb"}), encoding="utf-8")
            (root / "gottlieb.png").write_bytes(b"png")
            (root / "premier-technology.png").write_bytes(b"png")

            row = manufacturer_report(["Premier Technology"])[0]

        self.assertEqual(row["aliased_to"], "gottlieb")
        self.assertEqual(row["logo"], "/manufacturers/gottlieb/logo",
                         "the file named for the manufacturer is not what resolves")

    def test_the_report_still_slugs_with_no_folder(self) -> None:
        from common.media.manufacturer_logos import manufacturer_report

        configure_manufacturer_logos(None)
        rows = manufacturer_report(["Williams Electronics", "", "  "])

        self.assertEqual(rows, [{"name": "Williams Electronics",
                                 "slug": "williams", "aliased_to": None,
                                 "logo": None}])


class ReferenceFileTests(unittest.TestCase):
    def test_the_reference_is_written_beside_the_images(self) -> None:
        from common.media.manufacturer_logos import write_manufacturer_reference

        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)

            path = write_manufacturer_reference(["Bally", "Data East"])
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(path, root / "manufacturers-reference.json")
        self.assertIn("Generated by VPinFE", payload["about"])
        self.assertEqual([row["slug"] for row in payload["manufacturers"]],
                         ["bally", "data-east"])

    def test_no_folder_or_no_names_writes_nothing(self) -> None:
        from common.media.manufacturer_logos import write_manufacturer_reference

        configure_manufacturer_logos(None)
        self.assertIsNone(write_manufacturer_reference(["Bally"]))
        with TemporaryDirectory() as tmp:
            configure_manufacturer_logos(Path(tmp))
            self.addCleanup(configure_manufacturer_logos, None)
            self.assertIsNone(write_manufacturer_reference([]))

    def test_vps_names_come_deduped_and_sorted_from_the_cache(self) -> None:
        from common.media.manufacturer_logos import vps_manufacturer_names

        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "vpsdb.json"
            cache.write_text(json.dumps([
                {"manufacturer": "Williams"}, {"manufacturer": "Bally"},
                {"manufacturer": "Williams"}, {"manufacturer": ""},
                {"name": "no manufacturer key"},
            ]), encoding="utf-8")

            self.assertEqual(vps_manufacturer_names(cache), ["Bally", "Williams"])
        self.assertEqual(vps_manufacturer_names("/nonexistent/vpsdb.json"), [])


class ConfigTests(unittest.TestCase):
    def test_the_folder_defaults_under_the_config_dir(self) -> None:
        self.assertEqual(resolve_logos_dir("", "/cfg"), Path("/cfg/manufacturer_logos"))
        self.assertEqual(resolve_logos_dir("  ", "/cfg"), Path("/cfg/manufacturer_logos"))
        self.assertEqual(resolve_logos_dir("/elsewhere/logos", "/cfg"),
                         Path("/elsewhere/logos"))


class PayloadTests(unittest.TestCase):
    def test_every_game_row_carries_the_logo_url_or_null(self) -> None:
        import json as _json

        from frontend.game_state import games_json
        from tests.support.entries import entries_for

        game = fake_game(
                   "/games/Cactus Canyon (Bally 1998)", "Cactus Canyon (Bally 1998)",
                   meta={"Info": {"Manufacturer": "Bally Manufacturing"}},
                   full_path_vpx_file="/games/Cactus Canyon (Bally 1998)/Cactus Canyon.vpx",
                   pup_pack_exists=False, alt_color_exists=False, alt_sound_exists=False,
               )

        with TemporaryDirectory() as tmp:
            root = _logos(self, tmp)
            (root / "bally.png").write_bytes(b"png")

            rows = _json.loads(games_json(entries_for([game]), contract=1))

        self.assertEqual(rows[0]["ManufacturerLogoPath"], "/manufacturers/bally/logo")


class RouteTests(unittest.TestCase):
    """What the frontend's file server answers at the URL the payload carries."""

    _tmp: TemporaryDirectory[str]
    root: Path
    server: ThemeServer
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "manufacturer_logos"
        cls.root.mkdir(parents=True)
        (cls.root / "Bally.JPG").write_bytes(b"bally")
        (cls.root / "stern.png").write_bytes(b"stern")
        configure_manufacturer_logos(cls.root)
        cls.server = ThemeServer().start()
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        configure_manufacturer_logos(None)
        cls._tmp.cleanup()

    def _get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                        timeout=5) as response:
                return response.status, response.read(), response.headers["Content-Type"]
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read(), ""

    def test_a_logo_is_served_whatever_the_case_of_its_file_name(self) -> None:
        self.assertEqual(self._get("/manufacturers/bally/logo"),
                         (200, b"bally", "image/jpeg"))
        self.assertEqual(self._get("/manufacturers/stern/logo")[:2], (200, b"stern"))

    def test_a_manufacturer_with_no_logo_is_a_404(self) -> None:
        self.assertEqual(self._get("/manufacturers/zaccaria/logo")[0], 404)

    def test_a_path_out_of_the_folder_is_a_404(self) -> None:
        self.assertEqual(self._get("/manufacturers/..%2Fmanufacturer_logos%2Fbally/logo")[0], 404)
        self.assertEqual(self._get("/manufacturers/bally/logo/extra")[0], 404)


if __name__ == "__main__":
    unittest.main()
