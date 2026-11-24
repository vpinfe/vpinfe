import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from common.games.formats import directb2s_parser as parser

DARK = (10, 10, 10)
RED = (250, 0, 0)
GREEN = (0, 250, 0)
BLUE = (0, 0, 250)


def _png(size: tuple[int, int], color: tuple[int, ...]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA" if len(color) == 4 else "RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def _b64(raw: bytes, wrapped: bool = True) -> str:
    text = base64.b64encode(raw).decode("ascii")
    if not wrapped:
        return text
    # How the real files carry it: line breaks as character references.
    return "&#xD;&#xA;".join(text[i:i + 76] for i in range(0, len(text), 76))


def _bulb(ident: int, picture: str, *, x: int, y: int, size: int = 4, initial: int = 0,
          dual: int = 0, visible: int = 1, parent: str = "Backglass", z: int = 0) -> str:
    return (
        f'    <Bulb Parent="{parent}" ID="{ident}" Name="" RomID="1" RomIDType="1" '
        f'InitialState="{initial}" DualMode="{dual}" Visible="{visible}" LocX="{x}" '
        f'LocY="{y}" Width="{size}" Height="{size}" ZOrder="{z}" IsImageSnippit="0" '
        f'Image="{picture}" />')


def _file(*, header: str = "", bulbs: str = "", images: str | None = None,
          size: tuple[int, int] = (40, 30)) -> bytes:
    if images is None:
        images = f'    <BackglassImage Value="{_b64(_png(size, DARK))}" />'
    text = (
        '<DirectB2SData Version="1.2">\n'
        '  <Name Value="Test" />\n'
        f"{header}"
        "  <Illumination>\n"
        f"{bulbs}"
        "  </Illumination>\n"
        "  <Images>\n"
        f"{images}\n"
        "  </Images>\n"
        "</DirectB2SData>")
    return text.encode("utf-8")


class DirectB2SParserCase(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.folder = Path(self._folder.name)
        root = mock.patch.object(parser, "ROOT", self.folder / "cache")
        root.start()
        self.addCleanup(root.stop)
        parser._facts.clear()

    def write(self, content: bytes, name: str = "table.directb2s") -> Path:
        path = self.folder / name
        path.write_bytes(content)
        return path

    def size(self, path: Path) -> tuple[int, int]:
        with Image.open(path) as opened:
            return opened.size

    def pixel(self, path: Path, at: tuple[int, int]) -> tuple[int, ...]:
        with Image.open(path) as opened:
            return opened.convert("RGB").getpixel(at)  # type: ignore[return-value]


class FactsTests(DirectB2SParserCase):
    def test_reads_the_header(self) -> None:
        header = (
            '  <GrillHeight Value="12" Small="7" />\n'
            '  <DualBackglass Value="1" />\n'
            '  <Author Value="Ann &amp; Bo" />\n'
            '  <Artwork Value="Cy" />\n'
            '  <GameName Value="rom_l5" />\n'
            '  <NumberOfPlayers Value="4" />\n')
        facts = parser.read(self.write(_file(header=header)))
        assert facts is not None
        self.assertEqual(facts.author, "Ann & Bo")
        self.assertEqual(facts.artwork, "Cy")
        self.assertEqual(facts.rom, "rom_l5")
        self.assertEqual(facts.players, 4)
        self.assertEqual(facts.grill, 12)
        self.assertEqual(facts.grill_small, 7)
        self.assertTrue(facts.two_versions)
        self.assertEqual(facts.size, (40, 30))
        self.assertEqual(facts.problems, ())

    def test_a_header_the_file_leaves_out_is_empty_not_an_error(self) -> None:
        facts = parser.read(self.write(_file()))
        assert facts is not None
        self.assertEqual((facts.author, facts.artwork, facts.rom), ("", "", ""))
        self.assertIsNone(facts.players)
        self.assertEqual(facts.grill, 0)
        self.assertIsNone(facts.grill_small)
        self.assertFalse(facts.two_versions)
        self.assertEqual(facts.problems, ())

    def test_a_bad_number_costs_that_fact_only(self) -> None:
        header = '  <NumberOfPlayers Value="four" />\n  <Author Value="Ann" />\n'
        facts = parser.read(self.write(_file(header=header)))
        assert facts is not None
        self.assertIsNone(facts.players)
        self.assertEqual(facts.author, "Ann")
        self.assertEqual(facts.problems, ())

    def test_counts_the_backglass_lamps_and_leaves_the_scoreview_ones(self) -> None:
        lamp = _b64(_png((4, 4), RED))
        bulbs = "\n".join([
            _bulb(1, lamp, x=0, y=0),
            _bulb(2, lamp, x=4, y=0),
            _bulb(3, lamp, x=8, y=0, parent="DMD"),
        ]) + "\n"
        facts = parser.read(self.write(_file(bulbs=bulbs)))
        assert facts is not None
        self.assertEqual(facts.lamps, 2)

    def test_a_still_picture_has_no_lamps(self) -> None:
        facts = parser.read(self.write(_file()))
        assert facts is not None
        self.assertEqual(facts.lamps, 0)

    def test_a_file_with_nothing_to_light_is_still(self) -> None:
        facts = parser.read(self.write(_file()))
        assert facts is not None
        self.assertTrue(facts.still)

    def test_a_file_with_a_visible_lamp_is_not_still(self) -> None:
        bulbs = _bulb(1, _b64(_png((4, 4), RED)), x=0, y=0) + "\n"
        facts = parser.read(self.write(_file(bulbs=bulbs)))
        assert facts is not None
        self.assertFalse(facts.still)

    def test_a_file_whose_every_lamp_is_hidden_or_on_the_scoreview_is_still(self) -> None:
        lamp = _b64(_png((4, 4), RED))
        bulbs = "\n".join([_bulb(1, lamp, x=0, y=0, visible=0),
                           _bulb(2, lamp, x=4, y=0, parent="DMD")]) + "\n"
        facts = parser.read(self.write(_file(bulbs=bulbs)))
        assert facts is not None
        self.assertTrue(facts.still)

    def test_a_file_with_its_own_lit_picture_is_not_still(self) -> None:
        images = (
            f'    <BackglassOffImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <BackglassOnImage Value="{_b64(_png((40, 30), BLUE))}" />')
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertFalse(facts.still)

    def test_scoreview_art_is_sized_when_the_file_has_it(self) -> None:
        images = (
            f'    <BackglassImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <DMDImage Value="{_b64(_png((24, 8), BLUE))}" />')
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertEqual(facts.scoreview_art, (24, 8))

    def test_the_size_is_the_dark_picture_in_a_file_with_an_off_image(self) -> None:
        images = (
            f'    <BackglassImage Value="{_b64(_png((10, 10), DARK))}" />\n'
            f'    <BackglassOffImage Value="{_b64(_png((50, 20), DARK))}" />')
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertEqual(facts.size, (50, 20))

    def test_a_picture_with_unbroken_base64_reads_too(self) -> None:
        images = f'    <BackglassImage Value="{_b64(_png((40, 30), DARK), wrapped=False)}" />'
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertEqual(facts.size, (40, 30))
        self.assertEqual(facts.problems, ())


class DisplaysTests(DirectB2SParserCase):
    def test_a_file_without_displays_has_none(self) -> None:
        facts = parser.read(self.write(_file()))
        assert facts is not None
        self.assertEqual(facts.displays, ())

    def test_the_dmds_the_file_places_are_in_its_facts(self) -> None:
        text = _file().decode().replace(
            "</DirectB2SData>",
            '  <Displays>\n    <DMD Target="Backglass" X="2" Y="3" W="20" H="5" />\n'
            '  </Displays>\n</DirectB2SData>')

        facts = parser.read(self.write(text.encode()))

        assert facts is not None
        (found,) = facts.displays
        self.assertEqual((found.target, tuple(found.placement)), ("Backglass", (2, 3, 20, 5)))
        self.assertEqual(facts.problems, ())


class ProblemTests(DirectB2SParserCase):
    def test_a_file_that_is_not_there_is_none(self) -> None:
        self.assertIsNone(parser.read(self.folder / "missing.directb2s"))

    def test_an_empty_file_is_not_a_backglass(self) -> None:
        facts = parser.read(self.write(b""))
        assert facts is not None
        self.assertEqual(facts.problems, (parser.NOT_B2S,))
        self.assertIsNone(facts.size)

    def test_a_file_that_is_something_else_is_not_a_backglass(self) -> None:
        facts = parser.read(self.write(b"<html><body>no</body></html>"))
        assert facts is not None
        self.assertEqual(facts.problems, (parser.NOT_B2S,))

    def test_no_picture_is_named(self) -> None:
        facts = parser.read(self.write(_file(images="")))
        assert facts is not None
        self.assertEqual(facts.problems, (parser.NO_PICTURE,))

    def test_damaged_base64_is_named_and_the_header_still_reads(self) -> None:
        good = _b64(_png((40, 30), DARK), wrapped=False)
        images = f'    <BackglassImage Value="{good[:200]}!!{good[200:]}" />'
        header = '  <Author Value="Ann" />\n'
        facts = parser.read(self.write(_file(header=header, images=images)))
        assert facts is not None
        self.assertEqual(facts.problems, (parser.BAD_PICTURE,))
        self.assertEqual(facts.author, "Ann")
        self.assertIsNone(facts.size)

    def test_a_picture_that_is_not_an_image_is_named(self) -> None:
        images = f'    <BackglassImage Value="{_b64(b"not a picture at all")}" />'
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertEqual(facts.problems, (parser.BAD_PICTURE,))

    def test_damaged_scoreview_art_leaves_the_backglass_readable(self) -> None:
        images = (
            f'    <BackglassImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <DMDImage Value="{_b64(b"junk")}" />')
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertEqual(facts.size, (40, 30))
        self.assertIsNone(facts.scoreview_art)
        self.assertEqual(facts.problems, (parser.BAD_SCOREVIEW_ART,))

    def test_a_picture_cut_off_part_way_is_named(self) -> None:
        png = _png((400, 300), DARK)
        images = f'    <BackglassImage Value="{_b64(png[:len(png) // 2])}" />'
        facts = parser.read(self.write(_file(images=images)))
        assert facts is not None
        self.assertEqual(facts.problems, (parser.BAD_PICTURE,))

    def test_a_cut_off_file_still_reads_what_came_before_the_cut(self) -> None:
        text = _file(header='  <Author Value="Ann" />\n')
        facts = parser.read(self.write(text[:text.index(b"<Illumination>") + 5]))
        assert facts is not None
        self.assertEqual(facts.author, "Ann")
        self.assertEqual(facts.problems, (parser.NO_PICTURE,))


class CacheTests(DirectB2SParserCase):
    def test_facts_are_read_once_per_version_of_the_file(self) -> None:
        path = self.write(_file(header='  <Author Value="Ann" />\n'))
        with mock.patch.object(parser, "_read_uncached", wraps=parser._read_uncached) as read:
            parser.read(path)
            parser.read(path)
            self.assertEqual(read.call_count, 1)
            path.write_bytes(_file(header='  <Author Value="Bo" />\n'))
            os.utime(path, ns=(1, 2_000_000_000))
            facts = parser.read(path)
            self.assertEqual(read.call_count, 2)
        assert facts is not None
        self.assertEqual(facts.author, "Bo")


    def test_a_failed_read_is_made_again_not_remembered(self) -> None:
        path = self.write(_file(header='  <Author Value="Ann" />\n'))
        real = parser._opened
        with mock.patch.object(parser, "_opened", side_effect=OSError("stale")), \
                self.assertLogs(parser.logger, "WARNING"):
            first = parser.read(path)
        assert first is not None
        self.assertEqual(first.problems, (parser.UNREADABLE,))
        with mock.patch.object(parser, "_opened", wraps=real):
            second = parser.read(path)
        assert second is not None
        self.assertEqual((second.author, second.problems), ("Ann", ()))

    def test_the_file_is_read_whole_not_mapped(self) -> None:
        path = self.write(_file())
        with parser._opened(path) as data:
            self.assertIsInstance(data, bytes)


class PictureTests(DirectB2SParserCase):
    """A 40x30 dark picture with a 4x4 lamp at (4, 4) and another at (20, 4)."""

    def two_lamps(self, *, first: dict | None = None, second: dict | None = None) -> Path:
        bulbs = "\n".join([
            _bulb(1, _b64(_png((4, 4), RED)), x=4, y=4, **(first or {})),
            _bulb(2, _b64(_png((4, 4), GREEN)), x=20, y=4, **(second or {})),
        ]) + "\n"
        return self.write(_file(bulbs=bulbs))

    def test_off_is_the_file_picture_with_no_lamp(self) -> None:
        path = self.two_lamps(first={"initial": 1})
        picture = parser.picture(path, "off")
        self.assertEqual(self.pixel(picture, (5, 5)), DARK)

    def test_on_draws_the_lamps_the_author_marked_lit(self) -> None:
        path = self.two_lamps(first={"initial": 1})
        picture = parser.picture(path, "on")
        self.assertEqual(self.pixel(picture, (5, 5)), RED)
        self.assertEqual(self.pixel(picture, (21, 5)), DARK)

    def test_every_draws_all_of_them(self) -> None:
        path = self.two_lamps(first={"initial": 1})
        picture = parser.picture(path, "every")
        self.assertEqual(self.pixel(picture, (5, 5)), RED)
        self.assertEqual(self.pixel(picture, (21, 5)), GREEN)

    def test_a_hidden_lamp_is_never_drawn(self) -> None:
        path = self.two_lamps(first={"initial": 1, "visible": 0})
        self.assertEqual(self.pixel(parser.picture(path, "every"), (5, 5)), DARK)

    def test_a_scoreview_lamp_is_not_on_the_backglass(self) -> None:
        path = self.two_lamps(first={"initial": 1, "parent": "DMD"})
        self.assertEqual(self.pixel(parser.picture(path, "every"), (5, 5)), DARK)

    def test_a_version_draws_its_own_lamps_and_the_shared_ones(self) -> None:
        bulbs = "\n".join([
            _bulb(1, _b64(_png((4, 4), RED)), x=4, y=4, initial=1, dual=1),
            _bulb(2, _b64(_png((4, 4), GREEN)), x=20, y=4, initial=1, dual=2),
            _bulb(3, _b64(_png((4, 4), BLUE)), x=4, y=20, initial=1, dual=0),
        ]) + "\n"
        path = self.write(_file(header='  <DualBackglass Value="1" />\n', bulbs=bulbs))
        authentic = parser.picture(path, "on", "authentic")
        fantasy = parser.picture(path, "on", "fantasy")
        self.assertEqual(self.pixel(authentic, (5, 5)), RED)
        self.assertEqual(self.pixel(authentic, (21, 5)), DARK)
        self.assertEqual(self.pixel(authentic, (5, 21)), BLUE)
        self.assertEqual(self.pixel(fantasy, (5, 5)), DARK)
        self.assertEqual(self.pixel(fantasy, (21, 5)), GREEN)
        self.assertEqual(self.pixel(fantasy, (5, 21)), BLUE)

    def test_a_lamp_is_drawn_at_the_size_the_file_gives_it(self) -> None:
        bulb = _bulb(1, _b64(_png((2, 2), RED)), x=4, y=4, size=8, initial=1)
        path = self.write(_file(bulbs=bulb + "\n"))
        picture = parser.picture(path, "on")
        self.assertEqual(self.pixel(picture, (11, 11)), RED)
        self.assertEqual(self.pixel(picture, (12, 12)), DARK)

    def test_a_lamp_past_the_edge_is_clipped_not_refused(self) -> None:
        bulb = _bulb(1, _b64(_png((4, 4), RED)), x=38, y=-2, initial=1)
        path = self.write(_file(bulbs=bulb + "\n"))
        picture = parser.picture(path, "on")
        self.assertEqual(self.pixel(picture, (39, 0)), RED)

    def test_a_lamp_with_transparency_lets_the_art_through(self) -> None:
        clear = _b64(_png((4, 4), (250, 0, 0, 0)))
        path = self.write(_file(bulbs=_bulb(1, clear, x=4, y=4, initial=1) + "\n"))
        self.assertEqual(self.pixel(parser.picture(path, "on"), (5, 5)), DARK)

    def test_a_half_clear_lamp_blends_with_the_art(self) -> None:
        half = _b64(_png((4, 4), (250, 0, 0, 128)))
        path = self.write(_file(bulbs=_bulb(1, half, x=4, y=4, initial=1) + "\n"))
        red, green, blue = self.pixel(parser.picture(path, "on"), (5, 5))
        self.assertTrue(100 < red < 150, red)
        self.assertEqual((green, blue), (5, 5))

    def test_a_lamp_that_cannot_be_decoded_leaves_the_rest_drawn(self) -> None:
        bulbs = "\n".join([
            _bulb(1, "!!not base64!!", x=4, y=4, initial=1),
            _bulb(2, _b64(_png((4, 4), GREEN)), x=20, y=4, initial=1),
        ]) + "\n"
        picture = parser.picture(self.write(_file(bulbs=bulbs)), "on")
        self.assertEqual(self.pixel(picture, (5, 5)), DARK)
        self.assertEqual(self.pixel(picture, (21, 5)), GREEN)

    def test_on_prefers_the_files_own_lit_picture(self) -> None:
        images = (
            f'    <BackglassOffImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <BackglassOnImage Value="{_b64(_png((40, 30), BLUE))}" />')
        path = self.write(_file(images=images))
        self.assertEqual(self.pixel(parser.picture(path, "on"), (5, 5)), BLUE)
        self.assertEqual(self.pixel(parser.picture(path, "off"), (5, 5)), DARK)

    def test_cutting_the_grill_drops_its_rows(self) -> None:
        path = self.write(_file(header='  <GrillHeight Value="10" />\n'))
        self.assertEqual(self.size(parser.picture(path, "on")), (40, 30))
        self.assertEqual(
            self.size(parser.picture(path, "on", cut_grill=True)), (40, 20))

    def test_a_grill_taller_than_the_picture_is_not_cut(self) -> None:
        path = self.write(_file(header='  <GrillHeight Value="99" />\n'))
        self.assertEqual(
            self.size(parser.picture(path, "on", cut_grill=True)), (40, 30))

    def test_the_picture_is_made_once_and_stays_out_of_the_games_folder(self) -> None:
        path = self.two_lamps(first={"initial": 1})
        first = parser.picture(path, "on")
        with mock.patch.object(parser, "_draw") as draw:
            self.assertEqual(parser.picture(path, "on"), first)
            draw.assert_not_called()
        self.assertTrue(first.is_relative_to(self.folder / "cache"))
        self.assertEqual(sorted(each.name for each in self.folder.iterdir()),
                         ["cache", "table.directb2s"])

    def test_a_changed_file_is_drawn_again_and_the_old_picture_goes(self) -> None:
        path = self.two_lamps(first={"initial": 1})
        first = parser.picture(path, "on")
        blue = f'    <BackglassImage Value="{_b64(_png((40, 30), BLUE))}" />'
        path.write_bytes(_file(images=blue))
        os.utime(path, ns=(1, 2_000_000_000))
        second = parser.picture(path, "on")
        self.assertNotEqual(first, second)
        self.assertEqual(self.pixel(second, (5, 5)), BLUE)
        self.assertFalse(first.exists())

    def test_each_look_version_and_cut_is_its_own_picture(self) -> None:
        path = self.two_lamps()
        made = {
            parser.picture(path, "on"), parser.picture(path, "off"),
            parser.picture(path, "every"), parser.picture(path, "on", "fantasy"),
            parser.picture(path, "on", cut_grill=True),
        }
        self.assertEqual(len(made), 5)

    def test_later_lamps_draw_over_earlier_ones_by_their_z_order(self) -> None:
        bulbs = "\n".join([
            _bulb(1, _b64(_png((4, 4), RED)), x=4, y=4, initial=1, z=1),
            _bulb(2, _b64(_png((4, 4), GREEN)), x=4, y=4, initial=1, z=0),
        ]) + "\n"
        picture = parser.picture(self.write(_file(bulbs=bulbs)), "on")
        self.assertEqual(self.pixel(picture, (5, 5)), RED)

    def test_a_lamp_made_for_one_version_draws_in_a_file_with_only_one(self) -> None:
        bulb = _bulb(1, _b64(_png((4, 4), RED)), x=4, y=4, initial=1, dual=2)
        path = self.write(_file(bulbs=bulb + "\n"))
        self.assertEqual(self.pixel(parser.picture(path, "on", "authentic"), (5, 5)), RED)

    def test_a_lit_picture_with_no_off_picture_is_not_the_lit_look(self) -> None:
        images = (
            f'    <BackglassImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <BackglassOnImage Value="{_b64(_png((40, 30), BLUE))}" />')
        path = self.write(_file(images=images))
        self.assertEqual(self.pixel(parser.picture(path, "on"), (5, 5)), DARK)

    def test_a_number_that_cannot_be_a_position_costs_that_lamp_only(self) -> None:
        red = _b64(_png((4, 4), RED))
        bulbs = "\n".join([
            _bulb(1, red, x=4, y=4, initial=1).replace('LocX="4"', 'LocX="1e999"'),
            _bulb(2, red, x=4, y=4, initial=1).replace('LocX="4"', 'LocX="99999999999999999999"'),
            _bulb(3, red, x=4, y=4, initial=1, size=40000),
            _bulb(4, _b64(_png((4, 4), GREEN)), x=20, y=4, initial=1),
        ]) + "\n"
        path = self.write(_file(bulbs=bulbs))
        facts = parser.read(path)
        assert facts is not None
        self.assertEqual((facts.lamps, facts.problems), (4, ()))
        picture = parser.picture(path, "on")
        self.assertEqual(self.pixel(picture, (21, 5)), GREEN)
        self.assertEqual(self.pixel(picture, (30, 25)), DARK)

    def test_a_file_that_is_not_there_cannot_be_drawn_and_leaves_no_folder(self) -> None:
        with self.assertRaises(parser.BackglassError):
            parser.picture(self.folder / "gone.directb2s", "on")
        self.assertFalse((self.folder / "cache").exists())

    def test_a_second_name_for_the_file_shares_its_pictures(self) -> None:
        path = self.two_lamps(first={"initial": 1})
        link = self.folder / "link.directb2s"
        link.symlink_to(path)
        made = parser.picture(path, "on")
        self.assertEqual(parser.picture(link, "on"), made)
        self.assertTrue(made.is_file())

    def test_a_file_with_no_picture_cannot_be_drawn(self) -> None:
        with self.assertRaises(parser.BackglassError):
            parser.picture(self.write(_file(images="")), "on")

    def test_a_damaged_picture_cannot_be_drawn_and_leaves_no_file_behind(self) -> None:
        images = f'    <BackglassImage Value="{_b64(b"junk")}" />'
        path = self.write(_file(images=images))
        with self.assertRaises(parser.BackglassError):
            parser.picture(path, "on")
        self.assertEqual([p for p in (self.folder / "cache").rglob("*") if p.is_file()], [])

    def test_an_unknown_look_or_version_is_refused(self) -> None:
        path = self.two_lamps()
        with self.assertRaises(RuntimeError):
            parser.picture(path, "dim")  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            parser.picture(path, "on", "other")  # type: ignore[arg-type]


class ScoreViewArtTests(DirectB2SParserCase):
    def test_the_art_is_served_as_a_file(self) -> None:
        images = (
            f'    <BackglassImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <DMDImage Value="{_b64(_png((24, 8), BLUE))}" />')
        art = parser.scoreview_art(self.write(_file(images=images)))
        assert art is not None
        self.assertEqual(self.size(art), (24, 8))
        self.assertEqual(self.pixel(art, (1, 1)), BLUE)

    def test_none_when_the_file_has_no_art(self) -> None:
        self.assertIsNone(parser.scoreview_art(self.write(_file())))

    def test_none_when_the_art_is_damaged(self) -> None:
        images = (
            f'    <BackglassImage Value="{_b64(_png((40, 30), DARK))}" />\n'
            f'    <DMDImage Value="{_b64(b"junk")}" />')
        self.assertIsNone(parser.scoreview_art(self.write(_file(images=images))))

    def test_none_when_the_file_is_not_there(self) -> None:
        self.assertIsNone(parser.scoreview_art(self.folder / "missing.directb2s"))


if __name__ == "__main__":
    unittest.main()
