"""A wizard's frame is one size on every step, and the part that changes scrolls inside it.

Without a fixed height the card is as tall as its step, so the footer moves under the
pointer from one step to the next. The stylesheet is read because a height cannot be
measured without a browser, and a class swapped for another would lose it unseen.
"""

from __future__ import annotations

import unittest

from tests.support.stylesheet import declarations


class AWizardHoldsOneSize(unittest.TestCase):
    def test_the_card_has_a_height_of_its_own_not_only_a_ceiling(self) -> None:
        card = declarations(".manager-dialog--tall.q-card")
        self.assertIn("height", card)
        self.assertEqual("flex", card.get("display"))
        self.assertEqual("column", card.get("flex-direction"))

    def test_the_body_takes_what_is_left_and_scrolls(self) -> None:
        scroll = declarations(".manager-wizard-scroll")
        self.assertEqual("auto", scroll.get("overflow-y"))
        self.assertTrue(scroll.get("flex", "").startswith("1 1"))
        self.assertEqual("0", scroll.get("min-height"))

    def test_the_body_fills_the_card_s_width_whatever_the_card_aligns_to(self) -> None:
        self.assertEqual("stretch", declarations(".manager-wizard-scroll").get("align-self"))


if __name__ == "__main__":
    unittest.main()
