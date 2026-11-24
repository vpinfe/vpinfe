"""What an update asks a person to set up, and the ones that stop play until they do."""

from __future__ import annotations

import unittest

from common.config.config_store import ConfigStore
from common.release import upgrade_needs
from common.release.upgrade_needs import Need
from tests.support.library import TempTree


class NeedsTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.config = ConfigStore(str(self.root / "vpinfe.ini"))
        self.answered = False
        self.needs = (
            Need("pick_thing", "v3.1.0", lambda config: self.answered),
            Need("old_thing", "v3.0.0", lambda config: False),
            Need("must_thing", "v3.1.0", lambda config: False,
                 blocks_play=True),
        )

    def open_keys(self, since: str) -> list[str]:
        return [n.key for n in upgrade_needs.open_for(self.config, since, self.needs)]

    def test_a_need_introduced_after_the_last_version_is_open(self) -> None:
        self.assertEqual(self.open_keys("v3.0.0"), ["pick_thing", "must_thing"])

    def test_a_need_introduced_at_or_before_the_last_version_is_not_asked_again(self) -> None:
        self.assertNotIn("old_thing", self.open_keys("v3.0.0"))

    def test_a_done_step_never_shows(self) -> None:
        self.answered = True

        self.assertEqual(self.open_keys("v3.0.0"), ["must_thing"])

    def test_nothing_recorded_as_the_last_version_asks_for_every_open_need(self) -> None:
        self.assertEqual(self.open_keys(""), ["pick_thing", "old_thing", "must_thing"])

    def test_blocking_is_the_open_needs_that_stop_play_whatever_the_last_version(self) -> None:
        self.assertEqual([n.key for n in upgrade_needs.blocking(self.config, self.needs)],
                         ["must_thing"])

    def test_a_blocking_need_that_is_done_blocks_nothing(self) -> None:
        needs = (Need("must_thing", "v3.1.0", lambda config: True,
                      blocks_play=True),)

        self.assertEqual(upgrade_needs.blocking(self.config, needs), [])

    def test_a_done_check_that_raises_counts_as_not_done(self) -> None:
        def broken(config) -> bool:
            raise RuntimeError("no")

        needs = (Need("pick_thing", "v3.1.0", broken),)

        self.assertEqual([n.key for n in upgrade_needs.open_for(self.config, "v3.0.0", needs)],
                         ["pick_thing"])

    def test_the_catalog_ships_with_no_step_that_blocks_play(self) -> None:
        self.assertEqual(upgrade_needs.blocking(self.config), [])


if __name__ == "__main__":
    unittest.main()
