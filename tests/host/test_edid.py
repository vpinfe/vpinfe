from __future__ import annotations

import unittest

from common.host import edid
from tests.support import edids


def _parse(raw: bytes) -> edid.Edid:
    found = edid.parse(raw)
    assert found is not None
    return found


class ModelNameTests(unittest.TestCase):
    def test_a_name_is_read_without_its_terminator_or_padding(self) -> None:
        self.assertEqual(_parse(edids.load("dell_p2419h_a")).name, "DELL P2419H")

    def test_a_name_filling_its_field_has_no_terminator_to_stop_at(self) -> None:
        self.assertEqual(_parse(edids.load("name_fills_its_field")).name, "DELL S2721DGF")

    def test_the_name_is_found_in_whichever_descriptor_holds_it(self) -> None:
        # The second descriptor in one fixture and the third in the other.
        self.assertEqual(_parse(edids.load("name_fills_its_field")).name, "DELL S2721DGF")
        self.assertEqual(_parse(edids.load("television_with_extension")).name, "SAMSUNG")

    def test_a_panel_that_only_has_unspecified_text_has_no_name(self) -> None:
        self.assertEqual(_parse(edids.load("no_model_name")).name, "")

    def test_an_extension_block_after_the_first_is_not_in_the_way(self) -> None:
        block = edids.load("television_with_extension")

        self.assertEqual(len(block), 256)
        self.assertEqual(_parse(block).name, "SAMSUNG")

    def test_two_panels_of_one_model_are_named_alike(self) -> None:
        self.assertEqual(_parse(edids.load("dell_p2419h_a")).name,
                         _parse(edids.load("dell_p2419h_b")).name)

    def test_a_block_failing_its_checksum_still_names_the_monitor(self) -> None:
        damaged = bytearray(edids.load("dell_p2419h_a"))
        damaged[127] ^= 0xFF

        self.assertEqual(_parse(bytes(damaged)).name, "DELL P2419H")

    def test_a_name_with_an_unprintable_byte_keeps_only_what_prints(self) -> None:
        marked = bytearray(edids.load("dell_p2419h_a"))
        at = marked.index(b"DELL P2419H") + 4
        marked[at] = 0x01

        self.assertEqual(_parse(bytes(marked)).name, "DELLP2419H")


class PhysicalSizeTests(unittest.TestCase):
    def test_the_size_is_in_centimeters_as_the_block_holds_it(self) -> None:
        found = _parse(edids.load("dell_p2419h_a"))

        self.assertEqual((found.width_cm, found.height_cm), (53, 30))


class NotAnEdidTests(unittest.TestCase):
    def test_bytes_without_the_header_are_refused(self) -> None:
        self.assertIsNone(edid.parse(bytes(128)))

    def test_a_block_cut_short_is_refused(self) -> None:
        self.assertIsNone(edid.parse(edids.load("dell_p2419h_a")[:100]))

    def test_nothing_is_refused(self) -> None:
        self.assertIsNone(edid.parse(b""))


if __name__ == "__main__":
    unittest.main()
