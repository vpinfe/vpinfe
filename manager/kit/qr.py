"""An address as a picture a camera can read."""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger("vpinfe.manager.kit.qr")


def svg(text: str) -> str:
    """`text` as an SVG QR. Empty where it cannot be drawn, which the caller shows the plain
    text for instead."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except Exception:
        return ""
    try:
        code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                             box_size=8, border=2)
        code.add_data(text)
        code.make(fit=True)
        held = BytesIO()
        code.make_image(image_factory=SvgPathImage).save(held)
        return held.getvalue().decode("utf-8")
    except Exception:
        logger.warning("manager: could not draw a QR", exc_info=True)
        return ""
