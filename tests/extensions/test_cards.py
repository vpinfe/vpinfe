"""A card is 2.x's VPinPlay card, byte for byte, and each version reads the other's."""

from __future__ import annotations

import json
import re
import unittest
from html import escape, unescape
from io import BytesIO
from xml.etree import ElementTree

from common import service_errors
from common.extensions import cards

# -- 2.6.3 -----------------------------------------------------------------------


def _build_vpinplay_qr_payload(user_id: str, initials: str, machine_id: str) -> str:
    payload = {
        "type": "vpinplay_identity",
        "version": 1,
        "userId": (user_id or "").strip(),
        "initials": (initials or "").strip().upper(),
        "machineId": (machine_id or "").strip(),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _build_qr_svg(value: str) -> str:
    import qrcode
    from qrcode.image.svg import SvgPathImage

    stream = BytesIO()
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(value)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    image.save(stream)
    svg = stream.getvalue().decode("utf-8")
    svg_tag_start = svg.find("<svg")
    if svg_tag_start != -1 and "<rect" not in svg:
        svg_tag_end = svg.find(">", svg_tag_start)
        if svg_tag_end != -1:
            svg = (
                f"{svg[:svg_tag_end + 1]}"
                '<rect width="100%" height="100%" fill="white"/>'
                f"{svg[svg_tag_end + 1:]}"
            )
    return svg


def _embed_payload_in_svg(svg: str, payload: str) -> str:
    svg_tag_start = svg.find("<svg")
    if svg_tag_start == -1:
        return svg

    svg_tag_end = svg.find(">", svg_tag_start)
    if svg_tag_end == -1:
        return svg

    escaped_payload = escape(payload)
    comment_payload = payload.replace("--", r"\u002d\u002d")
    embedded = (
        f"{svg[:svg_tag_end + 1]}"
        f"<!--VPINPLAY_PAYLOAD:{comment_payload}-->"
        f'<metadata id="vpinplay-payload" data-type="application/json">{escaped_payload}</metadata>'
        f'<desc id="vpinplay-payload-desc">{escaped_payload}</desc>'
        f"{svg[svg_tag_end + 1:]}"
    )
    return embedded


_PAYLOAD_COMMENT_RE = re.compile(r"<!--\s*VPINPLAY_PAYLOAD:(.*?)-->", re.DOTALL)


def _extract_payload_text_from_svg(svg_text: str) -> str:
    comment_match = _PAYLOAD_COMMENT_RE.search(svg_text)
    if comment_match:
        return unescape(comment_match.group(1).strip())

    root = ElementTree.fromstring(svg_text)
    for element in root.iter():
        tag_name = element.tag.split("}", 1)[-1]
        if tag_name not in {"metadata", "desc"}:
            continue
        element_id = str(element.attrib.get("id", "") or "").strip().lower()
        if element_id not in {"vpinplay-payload", "vpinplay-payload-desc"}:
            continue
        if element.text and element.text.strip():
            return unescape(element.text.strip())

    raise ValueError("Could not find embedded VPinPlay payload in the uploaded SVG.")

# -- end of 2.6.3 ----------------------------------------------------------------


KEY = "Kq7" * 21 + "x"


def _2x_card(user_id: str = "jordan", initials: str = "abc",
             machine_id: str = KEY) -> tuple[str, str]:
    """(the card's text, the file 2.x's Download QR Code saves)."""
    said = _build_vpinplay_qr_payload(user_id, initials, machine_id)
    return said, _embed_payload_in_svg(_build_qr_svg(said), said)


class TheFileIs2x(unittest.TestCase):
    def test_a_vpinplay_card_is_the_file_2x_saved(self) -> None:
        said, saved = _2x_card()

        self.assertEqual(cards.drawn(json.loads(said), "vpinplay"), saved)

    def test_text_a_comment_or_the_markup_would_break_is_still_the_same_file(self) -> None:
        said, saved = _2x_card(user_id="a--b<&>\"c'")

        self.assertEqual(cards.drawn(json.loads(said), "vpinplay"), saved)

    def test_a_card_whose_text_holds_two_hyphens_is_well_formed(self) -> None:
        said, _ = _2x_card(user_id="a--b")

        drawn = ElementTree.fromstring(cards.drawn(json.loads(said), "vpinplay"))

        self.assertEqual(drawn.tag.split("}", 1)[-1], "svg")

    def test_its_text_is_2x_s_compact_sorted_form(self) -> None:
        said, _ = _2x_card()

        self.assertEqual(cards.text_of(json.loads(said)), said)

    def test_the_hiding_places_are_named_for_the_extension(self) -> None:
        drawn = cards.drawn({"type": "sample_card", "handle": "h"}, "sample")

        self.assertIn("<!--SAMPLE_PAYLOAD:", drawn)
        self.assertIn('<metadata id="sample-payload"', drawn)
        self.assertIn('<desc id="sample-payload-desc">', drawn)


class EachReadsTheOther(unittest.TestCase):
    def test_2x_reads_a_card_this_version_made(self) -> None:
        said, _ = _2x_card(user_id="a--b<&>")

        drawn = cards.drawn(json.loads(said), "vpinplay")

        self.assertEqual(json.loads(_extract_payload_text_from_svg(drawn)), json.loads(said))

    def test_this_version_reads_a_card_2x_made(self) -> None:
        said, saved = _2x_card(user_id="a--b<&>")

        self.assertEqual(cards.read(saved), json.loads(said))

    def test_the_metadata_is_read_when_the_comment_is_gone(self) -> None:
        said, saved = _2x_card()
        stripped = re.sub(r"<!--.*?-->", "", saved, flags=re.DOTALL)

        self.assertEqual(cards.read(stripped), json.loads(said))

    def test_the_description_is_read_when_it_is_all_that_is_left(self) -> None:
        said, saved = _2x_card()
        stripped = re.sub(r"<!--.*?-->|<metadata.*?</metadata>", "", saved, flags=re.DOTALL)

        self.assertEqual(cards.read(stripped), json.loads(said))

    def test_the_card_text_on_its_own_is_read(self) -> None:
        """2.x's upload took a file that starts with a brace as the text itself."""
        said, _ = _2x_card()

        self.assertEqual(cards.read(f"  {said}\n"), json.loads(said))


class WhatIsNotACard(unittest.TestCase):
    def assert_refused(self, text: str) -> None:
        with self.assertRaises(service_errors.RefusedError):
            cards.read(text)

    def test_plain_text_is_refused(self) -> None:
        self.assert_refused("jordan abc")

    def test_a_picture_with_nothing_hidden_in_it_is_refused(self) -> None:
        """The QR code is never decoded: a card is read from its text or not at all."""
        said, _ = _2x_card()
        self.assert_refused(_build_qr_svg(said))

    def test_text_that_says_no_kind_of_card_is_refused(self) -> None:
        self.assert_refused('{"userId": "jordan"}')

    def test_markup_that_does_not_parse_is_refused(self) -> None:
        self.assert_refused("<svg><metadata id='x-payload'>")


if __name__ == "__main__":
    unittest.main()
