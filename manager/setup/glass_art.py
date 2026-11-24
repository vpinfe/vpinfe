"""The two pictures beside Setup's playfield glass questions: which side is long and which
is short, and what angle is measured from.

Drawn as SVG that takes its colors from the page (see `.manager-glass-art` in
manager-base.css) and says its labels in the catalog's words, so a translation reaches the
picture too. Decorative: the question beside each one says everything it does.
"""

from __future__ import annotations

from html import escape

from common.i18n import t

_OPEN = ('<svg viewBox="0 0 170 104" fill="none" stroke-width="1.5" stroke-linecap="round" '
         'stroke-linejoin="round" aria-hidden="true">')


def _word(key: str, cls: str, *, at: str, anchor: str = "start") -> str:
    """`at` is the SVG's own placement: `x="70" y="45"`, or a `transform`."""
    return (f'<text {at} text-anchor="{anchor}" class="{cls}" font-size="10">'
            f'{escape(t(key))}</text>')


def size() -> str:
    """A screen with its long side marked underneath and its short side beside it."""
    return (
        f'{_OPEN}<rect x="14" y="10" width="112" height="64" rx="4" class="glass-wash glass-edge"/>'
        + _word("manager.setup.glass.art_picture", "glass-ink2", at='x="70" y="45"',
                anchor="middle")
        + '<path d="M14 88H126M14 84v8M126 84v8" class="glass-accent"/>'
        + _word("manager.screens.screen_long_side", "glass-ink2", at='x="70" y="101"',
                anchor="middle")
        + '<path d="M142 10V74M138 10h8M138 74h8" class="glass-accent"/>'
        + _word("manager.screens.screen_short_side", "glass-ink2",
                at='transform="translate(157 42) rotate(90)"', anchor="middle")
        + "</svg>")


def angle() -> str:
    """A screen sloping up from a level line, the player at the low end."""
    return (
        f'{_OPEN}<path d="M12 74H158" stroke-dasharray="4 4" class="glass-level"/>'
        '<path d="M12 74L150 42" stroke-width="4" class="glass-accent"/>'
        '<path d="M62 74a50 50 0 0 0 -2.8 -11.6" class="glass-edge"/>'
        + _word("manager.setup.glass.art_angle", "glass-ink2", at='x="70" y="70"')
        + _word("manager.setup.glass.art_level", "glass-ink3", at='x="104" y="92"',
                anchor="middle")
        + _word("manager.setup.glass.art_player", "glass-ink3", at='x="12" y="92"')
        + _word("manager.setup.glass.art_back", "glass-ink3", at='x="158" y="92"', anchor="end")
        + _word("manager.setup.glass.art_screen", "glass-ink2", at='x="112" y="28"')
        + "</svg>")
