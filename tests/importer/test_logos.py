"""The manufacturer logos a source keeps with its media: which file stands for a maker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.extensions import host

host.Registry().load(host.BUNDLED_DIR / "library_importer")

from vpinfe_ext_library_importer import logos  # noqa: E402


class LogosCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.media = Path(self._tmp.name) / "Media"
        self.folder = self.media / "Company Logos"
        self.folder.mkdir(parents=True)

    def make(self, *names: str) -> None:
        for name in names:
            (self.folder / name).write_bytes(b"png")

    def chosen(self) -> dict[str, str]:
        return {maker: path.name for maker, path in logos.candidates(str(self.media))}


class CandidateTests(LogosCase):
    def test_a_file_is_the_logo_of_the_maker_it_is_named_for(self) -> None:
        self.make("Williams.png", "Bally.jpg")

        self.assertEqual(self.chosen(), {"Bally": "Bally.jpg", "Williams": "Williams.png"})

    def test_an_animated_png_is_a_logo_too(self) -> None:
        self.make("Stern.apng")

        self.assertEqual(self.chosen(), {"Stern": "Stern.apng"})

    def test_the_file_with_no_era_wins_over_the_ones_with_one(self) -> None:
        self.make("Gottlieb (1971-1980).png", "Gottlieb.png", "Gottlieb (1950-1970).png")

        self.assertEqual(self.chosen(), {"Gottlieb": "Gottlieb.png"})

    def test_of_several_eras_the_latest_wins(self) -> None:
        self.make("Gottlieb (1950-1970).png", "Gottlieb (1971-1980).png",
                  "Gottlieb (1981-1984).png")

        self.assertEqual(self.chosen(), {"Gottlieb": "Gottlieb (1981-1984).png"})

    def test_an_era_with_no_end_is_the_latest(self) -> None:
        self.make("Stern (1999-2004).png", "Stern (2005-).png")

        self.assertEqual(self.chosen(), {"Stern": "Stern (2005-).png"})

    def test_a_file_with_no_era_wins_over_an_era_under_another_spelling(self) -> None:
        self.make("Gottlieb (1990-).png", "Gottlieb Co.png")

        self.assertEqual([path.name for _maker, path in logos.candidates(str(self.media))],
                         ["Gottlieb Co.png", "Gottlieb (1990-).png"])

    def test_a_file_that_is_not_an_image_is_ignored(self) -> None:
        self.make("Readme.txt", "Thumbs.db")

        self.assertEqual(self.chosen(), {})

    def test_the_folder_is_found_in_any_case(self) -> None:
        self.folder.rename(self.media / "company logos")
        (self.media / "company logos" / "Bally.png").write_bytes(b"png")

        self.assertEqual(self.chosen(), {"Bally": "Bally.png"})

    def test_a_media_folder_with_no_logos_is_nothing(self) -> None:
        self.folder.rmdir()

        self.assertEqual(self.chosen(), {})
        self.assertEqual(logos.candidates(""), [])


class BringTests(LogosCase):
    def ctx(self, *makers: str) -> mock.MagicMock:
        ctx = mock.MagicMock()
        ctx.games.list_games.return_value = {
            "games": [{"id": f"g{at}", "manufacturer": one} for at, one in enumerate(makers)]}
        ctx.games.put_manufacturer_logos.return_value = {"placed": ["A.png"], "held": ["B.png"]}
        return ctx

    def test_the_makers_in_the_library_are_offered_with_the_logos_found(self) -> None:
        self.make("Williams.png")
        ctx = self.ctx("Williams", "Williams", "", "Bally")

        done = logos.bring(ctx, str(self.media))

        makers, found = ctx.games.put_manufacturer_logos.call_args.args
        self.assertEqual(makers, ["Bally", "Williams"])
        self.assertEqual([(name, path.name) for name, path in found],
                         [("Williams", "Williams.png")])
        self.assertEqual(done, {"placed": 1, "held": 1})

    def test_core_is_not_asked_when_the_source_has_no_logos(self) -> None:
        ctx = self.ctx("Williams")

        self.assertEqual(logos.bring(ctx, str(self.media)), {"placed": 0, "held": 0})
        ctx.games.put_manufacturer_logos.assert_not_called()

    def test_a_logo_core_refuses_to_read_is_a_warning_not_a_failure(self) -> None:
        from common.extensions.contract import ContractError

        self.make("Williams.png")
        ctx = self.ctx("Williams")
        ctx.games.put_manufacturer_logos.side_effect = ContractError("outside")

        self.assertEqual(logos.bring(ctx, str(self.media)), {"placed": 0, "held": 0})
        ctx.logger.warning.assert_called_once()

    def test_a_refused_logo_is_named_for_the_report(self) -> None:
        from common.extensions.contract import ContractError

        self.make("Williams.png")
        ctx = self.ctx("Williams")
        ctx.games.put_manufacturer_logos.side_effect = ContractError("outside")
        lost: list[dict[str, str]] = []

        logos.bring(ctx, str(self.media), lost)

        ctx.t.assert_called_once_with("lost.logos")
        self.assertEqual(lost, [{"what": ctx.t.return_value, "detail": ctx.why.return_value}])

    def test_a_library_with_no_logo_folder_is_a_warning_not_a_failure(self) -> None:
        self.make("Williams.png")
        ctx = self.ctx("Williams")
        ctx.games.put_manufacturer_logos.side_effect = FileNotFoundError("no folder")

        self.assertEqual(logos.bring(ctx, str(self.media)), {"placed": 0, "held": 0})
        ctx.logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
