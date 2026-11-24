"""Settings > Media, Wheel Set: a pick from the sets the library has, not a name to remember."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from common.config import config_schema
from common.i18n import t
from manager.kit import offload
from manager.settings import settings

HERE = [{"name": "media", "options": [
    {"key": "wheelset", "suggest": config_schema.SUGGEST_WHEEL_SETS}]}]
ELSEWHERE = [{"name": "manager", "options": [{"key": "x"}]}]


def _asked(library: MagicMock, blocks: list[dict], sections: tuple[str, ...]):
    with patch.object(offload, "io", new=AsyncMock(side_effect=lambda call, *args: call(*args))):
        return settings._suggestions(library, blocks, sections)


class WheelSetSettingTests(unittest.IsolatedAsyncioTestCase):
    def test_the_setting_declares_that_it_offers_the_sets(self) -> None:
        option = next(option for option in config_schema.CONFIG_OPTIONS
                      if option.key == "wheelset")

        self.assertEqual(config_schema.SUGGEST_WHEEL_SETS, option.suggest)

    async def test_each_folder_is_offered_by_its_name_and_logo_in_words(self) -> None:
        library = MagicMock()
        library.wheel_sets.return_value = ["Classic", "logo"]

        offered = await _asked(library, HERE, ("media",))

        self.assertEqual({"Classic": "Classic", "logo": t("manager.settings.wheel_set_logo")},
                         offered[config_schema.SUGGEST_WHEEL_SETS])

    async def test_a_library_that_cannot_be_read_still_leaves_the_field_usable(self) -> None:
        library = MagicMock()
        library.wheel_sets.side_effect = RuntimeError("down")

        offered = await _asked(library, HERE, ("media",))

        self.assertEqual({}, offered[config_schema.SUGGEST_WHEEL_SETS])

    async def test_a_page_that_does_not_ask_does_not_look(self) -> None:
        library = MagicMock()

        offered = await _asked(library, ELSEWHERE, ("manager",))

        self.assertNotIn(config_schema.SUGGEST_WHEEL_SETS, offered)
        library.wheel_sets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
