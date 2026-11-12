"""The token layer, checked. It had no test and it decayed.

In a few weeks the stylesheet grew four tokens nothing referenced and one that was
referenced and never defined - `var(--warn)`, which resolves to nothing, so a menu item
meant to read as destructive drew in the inherited color instead. Nothing failed,
because nothing was looking.

Three rules, and they are the seam the tokenizing pass needs under it: every `var()`
resolves, every token has a user, and a color is named rather than typed.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import unittest
from unittest import mock

from manager.kit import appearance

# Every block that ships, in one string - a token may be defined in one and used in
# another, and checking them apart would report both halves as broken.
STYLESHEET = appearance.palette_css() + appearance.base_css()

DEFINED = re.compile(r"^\s*(--[a-z0-9-]+)\s*:", re.MULTILINE)
USED = re.compile(r"var\(\s*(--[a-z0-9-]+)")
# A palette is also read from Python: the nine Quasar brand values are looked up with
# `_token("--x")` rather than through a rule, and a token only read that way still has a
# reader. Without this, naming one is punished for not being written in CSS.
FROM_PYTHON = re.compile(r'_token\(\s*"(--[a-z0-9-]+)"')
# Hex, or rgb()/rgba() with a literal triple. Not a var() inside one.
LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b"
                     r"|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\)")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# A mask is a stencil, not a paint: inside one, black and white are the opacity
# channel and mean "hide this" and "keep this". A mode never restates them, so
# counting them as colors would leave a number that can never reach zero.
MASK = re.compile(r"(?:-webkit-)?mask(?:-image)?\s*:[^;]*;")

# The same shape as a mask: a value that is not a design decision. A QR code is read by
# a camera, which reads contrast and knows nothing about a palette, so its ground is
# white in every mode and a token for it would be a token that can never differ.
NOT_A_PALETTE = re.compile(r"\.manager-qr\s*\{[^}]*\}", re.DOTALL)

# AG Grid reads its own palette off these, so they are written for it rather than for
# us: defined here, used by a stylesheet we do not ship.
FOREIGN_PREFIXES = ("--ag-", "--q-")

# Set on an element while the page runs, so the stylesheet reads them and never defines
# them: by the drag handlers - the workbench's, and where a row dragged from a grid opens
# the rail's collections - on an empty number box, from the words it shows, and on a
# list's grid or row, from its Artwork settings, and on a page's kinds, from how many
# rows the first column takes, and on Overview's top card, from the picture behind it, and
# on a high score row, from the longest entry in its group, and on a progress ring, from
# the percent done, and the dock's width, from where the person dragged it. `--axis` is the
# reverse: the stylesheet defines it and the drag handler reads it, so no rule uses it.
# `--fit` is read by the rule a backglass preview writes with its two windows' shapes in
# it, which is not in the stylesheet.
RUNTIME = {"--dock-h", "--dock-w", "--axis", "--rows", "--drops-top", "--drops-left", "--blank",
           "--art-h", "--sum", "--gaps", "--gap", "--chrome", "--cap", "--stack", "--tall", "--fit",
           "--kind-rows", "--hero-picture", "--hs-rank", "--hs-init", "--hs-player",
           "--hs-score", "--hs-unit", "--p"}


def _same_color(literal):
    """One color typed two ways is one color: `rgba(15,7,34,.75)` and
    `rgba(15, 7, 34, 0.75)` differ only in whitespace."""
    return "".join(literal.split()).lower()


def _colors_typed_in(css):
    """Comments first, because a comment naming the color it rejected is the reason
    the rule reads the way it does. Counting those made the number argue against
    writing them down. Masks after, for the reason above them."""
    return {_same_color(m) for m in LITERAL.findall(
        NOT_A_PALETTE.sub("", MASK.sub("", COMMENT.sub("", css))))}


def _ours(names):
    return {n for n in names
            if not n.startswith(FOREIGN_PREFIXES) and n not in RUNTIME}


class TokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defined = _ours(set(DEFINED.findall(STYLESHEET)))
        source = pathlib.Path(appearance.__file__).read_text(encoding="utf-8")
        self.used = _ours(set(USED.findall(STYLESHEET))
                          | set(FROM_PYTHON.findall(source)))

    def test_every_var_resolves_to_a_token_that_exists(self) -> None:
        """`var(--warn)` shipped for weeks. A missing custom property is not an error
        in CSS - the declaration is dropped and the element inherits, so the only
        symptom is a color quietly being the wrong one."""
        self.assertEqual(sorted(self.used - self.defined), [])

    def test_every_token_has_a_user(self) -> None:
        """A token nothing reads is a decision nobody can see. Four of them
        accumulated - and one, `--fs-subject`, read like a heading level the Manager had
        never actually had."""
        self.assertEqual(sorted(self.defined - self.used), [])


class PaletteTests(unittest.TestCase):
    """Every mode has to answer for every token.

    A mode that omits one does not fall back to another mode - there is no other mode in
    the page. The `var()` resolves to nothing, the declaration is dropped, and the element
    inherits whatever is above it. That is the same silent failure `--warn` shipped as,
    except a whole palette can carry it.
    """

    def test_every_palette_declares_the_same_tokens(self) -> None:
        declared = {mode: set(DEFINED.findall(block))
                    for mode, block in appearance.PALETTES.items()}
        base = declared[appearance.DEFAULT_MODE]
        for mode, names in declared.items():
            self.assertEqual(sorted(names ^ base), [],
                             f"{mode} does not declare the same tokens as "
                             f"{appearance.DEFAULT_MODE}")

    def test_every_mode_renders_a_block_that_resolves(self) -> None:
        """The stylesheet is one string per mode, so a mode is only real if the rules
        can read it."""
        rules = appearance.base_css()
        want = _ours(set(USED.findall(rules)))
        for mode in appearance.PALETTES:
            # Everything that ships in this mode - a few tokens are declared inside a
            # scoped rule rather than in the palette, and those resolve just as well.
            have = _ours(set(DEFINED.findall(appearance.palette_css(mode) + rules)))
            self.assertEqual(sorted(want - have), [], f"{mode} leaves these unresolved")


class DeliveryTests(unittest.TestCase):
    """How the rules reach the page, which is the half a stylesheet cannot check itself."""

    def test_the_url_carries_a_digest_of_what_is_behind_it(self) -> None:
        """The file is served with an hour's max-age, and a browser holding a copy does
        not ask again inside that hour whatever the etag says. Without a URL that moves
        with the bytes, an upgrade leaves the old stylesheet on screen - which the old
        inline delivery could not do, so the cache is a cost this introduced."""
        digest = hashlib.sha256(appearance.BASE_CSS.read_bytes()).hexdigest()[:12]
        self.assertEqual(appearance.BASE_HREF,
                         f"{appearance.BASE_MOUNT}/manager-base.css?v={digest}")

    def test_a_changed_rule_changes_the_url(self) -> None:
        """The property that matters, stated as itself rather than as the hash."""
        first = appearance._base_href()
        held = appearance.BASE_CSS.read_bytes()
        try:
            appearance.BASE_CSS.write_bytes(held + b"\n.manager-nothing { color: red; }\n")
            self.assertNotEqual(appearance._base_href(), first)
        finally:
            appearance.BASE_CSS.write_bytes(held)
        self.assertEqual(appearance._base_href(), first)

    def test_the_page_links_the_rules_rather_than_carrying_them(self) -> None:
        """Inlining them again would work and would quietly put 127KB back into every
        page load, which is the whole reason the file exists."""
        added = []
        with mock.patch.object(appearance.ui, "add_css",
                               side_effect=lambda css: added.append(css)), \
             mock.patch.object(appearance.ui, "add_head_html",
                               side_effect=lambda html: added.append(html)):
            appearance.apply_flair()
        self.assertTrue(any(appearance.BASE_HREF in one for one in added), "no link to the file")
        rules = appearance.base_css()
        self.assertFalse(any(rules in one for one in added), "the rules are in the page")


class DialogButtonIconTests(unittest.TestCase):
    def test_a_footer_button_draws_its_icon_at_the_named_size(self) -> None:
        """One rule in the frame sizes every dialog's buttons, so no dialog sets its own."""
        rule = re.search(r"\.manager-dialog-footer \.q-btn \.q-icon\s*\{([^}]*)\}",
                         appearance.base_css())

        self.assertIsNotNone(rule, "no rule sizes the icon of a dialog's footer button")
        assert rule is not None
        self.assertIn("font-size: var(--icon-button)", rule.group(1))


class LiteralTests(unittest.TestCase):
    """A ceiling on colors typed by hand, not a ban on them.

    The colors that already had a token are named. What is left has none, and each
    needs a name invented for it rather than a substitution - so the pass lands in
    pieces. This holds the direction while it does: the count comes down and never up.

    Lower CEILING as it falls. Raising it is the thing to notice - and it has happened
    twice, both times because the check started seeing more rather than because more was
    written:

    - 18 -> 61, when the pattern started matching the rgb()/rgba() it had always claimed
      to match. Fifty colors were there the whole time.
    - 0 -> 1 -> 0, when the rules moved to one file and this began reading all five
      blocks instead of two. `rgba(255, 255, 255, 0.02)` on the remote's action button
      had never been counted, and it was a real one: over Light's ground it composited to
      exactly the ground, so that button was an outline with nothing inside it. It takes
      `--surface-lift` now, which is the token invented for that failure.
    """

    CEILING = 0

    def test_no_new_color_is_typed_rather_than_named(self) -> None:
        found = _colors_typed_in(appearance.base_css())

        self.assertLessEqual(
            len(found), self.CEILING,
            f"{len(found)} distinct unnamed colors, ceiling {self.CEILING}. "
            "Name it in the palette and use var().")

    def test_the_token_block_is_where_a_color_is_named(self) -> None:
        """The other half: if the palette held no hex either, the count above would
        be passing because it is reading the wrong string."""
        self.assertTrue(LITERAL.findall(appearance.palette_css()))


if __name__ == "__main__":
    unittest.main()
