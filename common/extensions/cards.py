"""A card: an account's text drawn as a QR code, with the text hidden in the file too.

Hidden under `vpinplay`, the file is 2.x's VPinPlay card byte for byte. A card is read
from the text in the file and never from the picture.
"""

from __future__ import annotations

import json
import re
from html import escape, unescape
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

import qrcode
from qrcode.image.svg import SvgPathImage

from common import service_errors
from common.i18n import t

# What the hiding places are named for, which is an extension's name.
MARKER = re.compile(r"^[a-z][a-z0-9_]*$")

_COMMENT = re.compile(r"<!--\s*[A-Z][A-Z0-9_]*_PAYLOAD:(.*?)-->", re.DOTALL)
_HOLDERS = ("metadata", "desc")
_HOLDER_IDS = ("-payload", "-payload-desc")


def text_of(card: dict[str, Any]) -> str:
    """What a card says, as 2.x wrote it: compact, keys sorted."""
    return json.dumps(card, separators=(",", ":"), sort_keys=True)


def drawn(card: dict[str, Any], marker: str) -> str:
    """The card as an SVG file, its text hidden under `marker`."""
    said = text_of(card)
    return _hidden(_qr(said), said, marker)


def read(file_text: str) -> dict[str, Any]:
    """The card in a card file, or in the card's text on its own. Refuses anything else."""
    said = _found(str(file_text or "").strip())
    try:
        card = json.loads(said)
    except ValueError:
        card = None
    if not isinstance(card, dict) or not str(card.get("type") or "").strip():
        raise _unreadable()
    return card


def _qr(said: str) -> str:
    stream = BytesIO()
    code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                         box_size=8, border=2)
    code.add_data(said)
    code.make(fit=True)
    code.make_image(image_factory=SvgPathImage).save(stream)
    svg = stream.getvalue().decode("utf-8")
    start = svg.find("<svg")
    if start != -1 and "<rect" not in svg:
        end = svg.find(">", start)
        if end != -1:
            svg = f'{svg[:end + 1]}<rect width="100%" height="100%" fill="white"/>{svg[end + 1:]}'
    return svg


def _hidden(svg: str, said: str, marker: str) -> str:
    start = svg.find("<svg")
    end = svg.find(">", start) if start != -1 else -1
    if end == -1:
        return svg
    shown = escape(said)
    # A comment cannot hold `--`. JSON reads the escape back, so the text is unchanged.
    commented = said.replace("--", r"\u002d\u002d")
    return (f"{svg[:end + 1]}"
            f"<!--{marker.upper()}_PAYLOAD:{commented}-->"
            f'<metadata id="{marker}-payload" data-type="application/json">{shown}</metadata>'
            f'<desc id="{marker}-payload-desc">{shown}</desc>'
            f"{svg[end + 1:]}")


def _found(text: str) -> str:
    if text.startswith("{"):
        return text
    if "<svg" not in text:
        raise _unreadable()
    comment = _COMMENT.search(text)
    if comment:
        return unescape(comment.group(1).strip())
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise _unreadable() from exc
    for element in root.iter():
        named = str(element.attrib.get("id", "") or "").strip().lower()
        if (element.tag.split("}", 1)[-1] in _HOLDERS and named.endswith(_HOLDER_IDS)
                and element.text and element.text.strip()):
            return unescape(element.text.strip())
    raise _unreadable()


def _unreadable() -> service_errors.RefusedError:
    return service_errors.RefusedError(t("error.players.card_unreadable"))
