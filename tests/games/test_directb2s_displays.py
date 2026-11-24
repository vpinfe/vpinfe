"""The DMD a backglass file places, read and spliced as bytes.

A file is megabytes of picture text, so what is pinned is that a change touches the
`<Displays>` element and nothing else: every other byte, every other attribute, and the
file's own line ending and indent.
"""

from __future__ import annotations

import unittest

from common.games.formats import directb2s_displays as displays
from common.games.formats.directb2s_displays import Placement

PICTURE = b'<Image Value="' + b"QUJD" * 50 + b'" />'


def body(*, newline: bytes = b"\r\n", extra: bytes = b"") -> bytes:
    return (b'<DirectB2SData Version="1.3">' + newline
            + b'  <Name Value="Taxi" />' + newline
            + b"  <Images>" + newline + b"    " + PICTURE + newline + b"  </Images>" + newline
            + extra + b"</DirectB2SData>" + newline)


def with_displays(inner: bytes, newline: bytes = b"\r\n") -> bytes:
    return body(newline=newline, extra=b"  <Displays>" + newline + inner + b"  </Displays>"
                + newline)


DMD = b'    <DMD Target="Backglass" X="10" Y="20" W="300" H="80" Style="Dots" Tint="#ff0000" />\r\n'


class ReadTests(unittest.TestCase):
    def test_a_file_without_displays_has_none(self) -> None:
        self.assertEqual(displays.read(body()), ())

    def test_each_dmd_is_read_with_its_window_and_place(self) -> None:
        data = with_displays(
            DMD + b'    <DMD Target="ScoreView" X="1" Y="2" W="3" H="4" />\r\n')

        self.assertEqual(
            displays.read(data),
            (displays.Display("Backglass", Placement(10, 20, 300, 80)),
             displays.Display("ScoreView", Placement(1, 2, 3, 4))))

    def test_a_missing_target_is_the_backglass(self) -> None:
        data = with_displays(b'    <DMD X="1" Y="2" W="3" H="4" />\r\n')

        self.assertEqual(displays.placed(displays.read(data), "Backglass"),
                         Placement(1, 2, 3, 4))

    def test_a_dmd_with_a_source_is_not_the_default_one(self) -> None:
        data = with_displays(b'    <DMD Source="Extra" X="1" Y="2" W="3" H="4" />\r\n')

        (found,) = displays.read(data)

        self.assertEqual(found.source, "Extra")
        self.assertIsNone(displays.placed((found,), "Backglass"))

    def test_a_dmd_that_holds_an_image_is_read(self) -> None:
        data = with_displays(
            b'    <DMD Target="Backglass" X="5" Y="6" W="7" H="8">\r\n'
            b'      <GlassImage Value="QUJD" />\r\n    </DMD>\r\n')

        self.assertEqual(displays.placed(displays.read(data), "Backglass"),
                         Placement(5, 6, 7, 8))

    def test_a_displays_element_that_never_closes_reads_as_none(self) -> None:
        self.assertEqual(displays.read(body(extra=b"  <Displays>\r\n")), ())


class PlaceTests(unittest.TestCase):
    def test_a_dmd_is_added_as_the_last_child_in_the_files_own_style(self) -> None:
        data = body()

        out = displays.place(data, "Backglass", Placement(10, 20, 300, 80))

        self.assertEqual(
            out, data.replace(
                b"</DirectB2SData>",
                b'  <Displays>\r\n    <DMD Target="Backglass" X="10" Y="20" W="300" H="80" />\r\n'
                b"  </Displays>\r\n</DirectB2SData>"))

    def test_an_lf_file_gets_lf(self) -> None:
        out = displays.place(body(newline=b"\n"), "Backglass", Placement(1, 2, 3, 4))

        self.assertNotIn(b"\r", out)
        self.assertEqual(displays.placed(displays.read(out), "Backglass"), Placement(1, 2, 3, 4))

    def test_moving_keeps_every_other_attribute_and_byte(self) -> None:
        data = with_displays(DMD)

        out = displays.place(data, "Backglass", Placement(11, 22, 333, 88))

        self.assertEqual(
            out, data.replace(b'X="10" Y="20" W="300" H="80"', b'X="11" Y="22" W="333" H="88"'))

    def test_a_dmd_with_no_size_attributes_gets_them(self) -> None:
        data = with_displays(b'    <DMD Target="Backglass" Style="Dots" />\r\n')

        out = displays.place(data, "Backglass", Placement(1, 2, 3, 4))

        self.assertIn(b'Style="Dots"', out)
        self.assertEqual(displays.placed(displays.read(out), "Backglass"), Placement(1, 2, 3, 4))

    def test_moving_keeps_an_image_inside_the_dmd(self) -> None:
        inner = (b'    <DMD Target="Backglass" X="5" Y="6" W="7" H="8">\r\n'
                 b'      <GlassImage Value="QUJD" />\r\n    </DMD>\r\n')

        out = displays.place(with_displays(inner), "Backglass", Placement(50, 60, 70, 80))

        self.assertIn(b'<GlassImage Value="QUJD" />\r\n    </DMD>', out)
        self.assertIn(b'X="50" Y="60" W="70" H="80">', out)

    def test_one_window_is_placed_without_touching_the_other(self) -> None:
        data = with_displays(DMD)

        out = displays.place(data, "ScoreView", Placement(1, 2, 3, 4))

        self.assertEqual(displays.placed(displays.read(out), "Backglass"),
                         Placement(10, 20, 300, 80))
        self.assertEqual(displays.placed(displays.read(out), "ScoreView"), Placement(1, 2, 3, 4))
        self.assertIn(DMD, out)

    def test_a_dmd_with_a_source_stays_when_the_default_one_goes(self) -> None:
        other = b'    <DMD Source="Extra" X="1" Y="2" W="3" H="4" />\r\n'

        out = displays.place(with_displays(DMD + other), "Backglass", None)

        self.assertEqual(displays.read(out),
                         (displays.Display("Backglass", Placement(1, 2, 3, 4), "Extra"),))

    def test_taking_the_only_dmd_out_takes_the_empty_displays_element_with_it(self) -> None:
        for name, data in (("crlf", body()), ("lf", body(newline=b"\n")),
                           ("bom", BOM + body())):
            with self.subTest(name):
                added = displays.place(data, "Backglass", Placement(10, 20, 300, 80))

                self.assertEqual(displays.place(added, "Backglass", None), data)

    def test_a_one_line_file_keeps_only_the_line_break_the_switch_on_added(self) -> None:
        added = displays.place(SINGLE_LINE, "Backglass", Placement(10, 20, 300, 80))

        out = displays.place(added, "Backglass", None)

        self.assertEqual(out.replace(b"\n", b""), SINGLE_LINE)
        self.assertNotIn(b"Displays", out)

    def test_a_displays_element_that_holds_anything_else_stays(self) -> None:
        other = b'    <DMD Source="Extra" X="1" Y="2" W="3" H="4" />\r\n'
        note = b"    <!-- kept -->\r\n"
        for name, inner in (("a dmd with a source", other), ("a comment", note)):
            with self.subTest(name):
                out = displays.place(with_displays(DMD + inner), "Backglass", None)

                self.assertEqual(out, with_displays(inner))

    def test_a_displays_element_with_attributes_stays_empty(self) -> None:
        data = body(extra=b'  <Displays Version="2">\r\n' + DMD + b"  </Displays>\r\n")

        out = displays.place(data, "Backglass", None)

        self.assertIn(b'<Displays Version="2">', out)
        self.assertEqual(displays.read(out), ())

    def test_taking_out_what_is_not_there_changes_nothing(self) -> None:
        data = body()

        self.assertEqual(displays.place(data, "Backglass", None), data)

    def test_an_empty_and_a_self_closing_displays_element_take_a_dmd(self) -> None:
        for shell in (b"  <Displays></Displays>\r\n", b"  <Displays />\r\n"):
            with self.subTest(shell=shell):
                out = displays.place(body(extra=shell), "Backglass", Placement(1, 2, 3, 4))

                self.assertEqual(displays.placed(displays.read(out), "Backglass"),
                                 Placement(1, 2, 3, 4))
                self.assertTrue(out.endswith(b"</DirectB2SData>\r\n"))

    def test_the_picture_text_is_never_changed(self) -> None:
        out = displays.place(body(), "Backglass", Placement(1, 2, 3, 4))

        self.assertIn(PICTURE, out)

    def test_a_dmd_with_no_size_is_refused(self) -> None:
        for where in (Placement(1, 2, 0, 4), Placement(1, 2, 3, -1)):
            with self.subTest(where=where), self.assertRaises(displays.DisplaysError):
                displays.place(body(), "Backglass", where)

    def test_a_window_the_plugin_does_not_have_is_refused(self) -> None:
        with self.assertRaises(displays.DisplaysError):
            displays.place(body(), "Marquee", Placement(1, 2, 3, 4))

    def test_a_file_that_is_not_a_b2s_file_is_refused(self) -> None:
        with self.assertRaises(displays.DisplaysError):
            displays.place(b"<html></html>", "Backglass", Placement(1, 2, 3, 4))


SINGLE_LINE = (b'<DirectB2SData Version="1.3"><Name Value="Taxi" />'
               b'<Images><BackglassImage Value="QUJD" /></Images></DirectB2SData>')
BOM = b"\xef\xbb\xbf"


def back_by_splice(data: bytes, window: str, where: Placement | None) -> bytes:
    """The file after a change, and that the splice the change leaves takes it back to the
    byte."""
    changed = displays.place(data, window, where)
    change = displays.splice_of(data, changed)
    assert change.back(changed) == data
    return changed


class SpliceTests(unittest.TestCase):
    def test_a_dmd_added_to_a_file_with_none_comes_out_to_the_byte(self) -> None:
        for name, data in (("crlf", body()), ("lf", body(newline=b"\n")),
                           ("one line", SINGLE_LINE),
                           ("bom", BOM + body()), ("bom, one line", BOM + SINGLE_LINE),
                           ("no final newline", body().rstrip())):
            with self.subTest(name):
                changed = back_by_splice(data, "Backglass", Placement(1, 2, 3, 4))

                self.assertEqual(displays.placed(displays.read(changed), "Backglass"),
                                 Placement(1, 2, 3, 4))

    def test_taking_out_the_last_dmd_and_its_element_comes_back_to_the_byte(self) -> None:
        for name, data in (("crlf", body()), ("bom", BOM + body(newline=b"\n"))):
            with self.subTest(name):
                added = displays.place(data, "ScoreView", Placement(1, 2, 3, 4))

                self.assertEqual(back_by_splice(added, "ScoreView", None), data)

    def test_a_dmd_added_after_the_last_image_tag_with_no_line_break(self) -> None:
        data = b"<DirectB2SData><Images></Images></DirectB2SData>"

        changed = back_by_splice(data, "Backglass", Placement(1, 2, 3, 4))

        self.assertEqual(changed.count(b"<Displays>"), 1)

    def test_a_move_and_a_removal_come_out_to_the_byte(self) -> None:
        data = with_displays(DMD)

        moved = back_by_splice(data, "Backglass", Placement(9, 9, 9, 9))
        back_by_splice(data, "Backglass", None)
        back_by_splice(moved, "ScoreView", Placement(1, 2, 3, 4))

    def test_a_move_keeps_only_the_bytes_it_changed(self) -> None:
        data = with_displays(DMD)

        change = displays.splice_of(data, displays.place(data, "Backglass",
                                                          Placement(10, 20, 301, 80)))

        self.assertEqual((change.removed, change.inserted), (b"0", b"1"))

    def test_undoing_into_a_file_that_is_not_as_written_is_refused(self) -> None:
        data = with_displays(DMD)
        changed = displays.place(data, "Backglass", Placement(10, 20, 301, 80))
        change = displays.splice_of(data, changed)

        with self.assertRaises(displays.DisplaysError):
            change.back(b"x" + changed)


class QuoteTests(unittest.TestCase):
    SINGLE = b"    <DMD Target='ScoreView' X='1' Y='2' W='3' H='4' Style='Dots' />\r\n"

    def test_single_quoted_attributes_are_read(self) -> None:
        data = with_displays(self.SINGLE)

        self.assertEqual(displays.placed(displays.read(data), "ScoreView"), Placement(1, 2, 3, 4))

    def test_a_single_quoted_dmd_is_moved_not_added_again(self) -> None:
        data = with_displays(self.SINGLE)

        out = displays.place(data, "ScoreView", Placement(1, 2, 30, 4))

        self.assertEqual(out.count(b"<DMD "), 1)
        self.assertIn(b"<DMD Target='ScoreView' X='1' Y='2' W='30' H='4' Style='Dots' />", out)

    def test_a_single_quoted_displays_element_is_used_not_added_again(self) -> None:
        data = body(extra=b"  <Displays Mode='x'>\r\n  </Displays>\r\n")

        out = displays.place(data, "Backglass", Placement(1, 2, 3, 4))

        self.assertEqual(out.count(b"<Displays"), 1)

    def test_a_displays_element_that_cannot_be_read_is_never_given_a_second(self) -> None:
        data = body(extra=b"  <Displays Mode=x>\r\n  </Displays>\r\n")

        with self.assertRaises(displays.DisplaysError):
            displays.place(data, "Backglass", Placement(1, 2, 3, 4))
        self.assertEqual(displays.read(data), ())

    def test_a_dmd_that_cannot_be_read_is_never_given_a_twin(self) -> None:
        data = with_displays(b"    <DMD Target=Backglass X=1 />\r\n")

        with self.assertRaises(displays.DisplaysError):
            displays.place(data, "Backglass", Placement(1, 2, 3, 4))

    def test_two_displays_elements_are_refused(self) -> None:
        data = with_displays(DMD).replace(
            b"</DirectB2SData>", b"  <Displays>\r\n  </Displays>\r\n</DirectB2SData>")

        with self.assertRaises(displays.DisplaysError):
            displays.place(data, "Backglass", Placement(1, 2, 3, 4))


class OtherWindowTests(unittest.TestCase):
    OTHER = b'    <DMD Target="Marquee" X="7" Y="7" W="7" H="7" />\r\n'

    def test_a_dmd_for_another_window_is_not_reported(self) -> None:
        data = with_displays(self.OTHER + DMD)

        self.assertEqual([one.target for one in displays.read(data)], ["Backglass"])

    def test_it_is_left_alone_whatever_is_written(self) -> None:
        data = with_displays(self.OTHER)

        for window, where in (("Backglass", Placement(1, 2, 3, 4)), ("ScoreView", None),
                              ("Backglass", None)):
            with self.subTest(window=window, where=where):
                out = displays.place(data, window, where)

                self.assertIn(self.OTHER, out)

    def test_a_dmd_for_the_backglass_is_added_beside_it(self) -> None:
        out = displays.place(with_displays(self.OTHER), "Backglass", Placement(1, 2, 3, 4))

        self.assertEqual(out.count(b"<DMD "), 2)


class NumberTests(unittest.TestCase):
    FLOATS = b'    <DMD Target="Backglass" X="10.0" Y="20.7" W="300" H="80.2" />\r\n'

    def test_a_value_that_is_the_same_whole_number_keeps_its_text(self) -> None:
        data = with_displays(self.FLOATS)

        out = displays.place(data, "Backglass", Placement(10, 20, 300, 80))

        self.assertEqual(out, data)

    def test_only_the_value_that_changed_is_rewritten(self) -> None:
        data = with_displays(self.FLOATS)

        out = displays.place(data, "Backglass", Placement(10, 21, 300, 80))

        self.assertEqual(out, data.replace(b'Y="20.7"', b'Y="21"'))

    def test_a_single_quoted_value_keeps_its_quote(self) -> None:
        data = with_displays(b"    <DMD X='1.5' Y='2' W='3' H='4' />\r\n")

        out = displays.place(data, "Backglass", Placement(2, 2, 3, 4))

        self.assertIn(b"X='2'", out)


class RangeTests(unittest.TestCase):
    def test_a_box_outside_the_limits_is_refused(self) -> None:
        for where in (Placement(-1, 0, 1, 1), Placement(0, -1, 1, 1), Placement(0, 0, 0, 1),
                      Placement(0, 0, 1, 0), Placement(displays.LIMIT + 1, 0, 1, 1),
                      Placement(0, 0, 1, displays.LIMIT + 1)):
            with self.subTest(where=where), self.assertRaises(displays.DisplaysError):
                displays.place(body(), "Backglass", where)

    def test_the_limits_themselves_are_allowed(self) -> None:
        where = Placement(0, displays.LIMIT, displays.LIMIT, 1)

        out = displays.place(body(), "Backglass", where)

        self.assertEqual(displays.placed(displays.read(out), "Backglass"), where)


if __name__ == "__main__":
    unittest.main()
