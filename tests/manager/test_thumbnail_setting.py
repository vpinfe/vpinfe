"""Show, under Artwork: which picture sits beside a name in the Manager's lists."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from common.config import config_schema
from common.i18n import t
from common.settings import config_service
from manager.kit import panel
from manager.settings import settings

KINDS = ("wheel", "backglass", "playfield", "logo")
MANAGER = ("manager",)


def _blocks() -> list[dict]:
    return config_service.schema()["sections"]


def _option() -> dict[str, Any]:
    return next(option for block in _blocks() if block["name"] == "manager"
                for option in block["options"] if option["key"] == "thumbnail")


def _now(call: Any, *args: Any, **kwargs: Any) -> Any:
    return call(*args, **kwargs)


class TheSettingTests(unittest.TestCase):
    def test_it_offers_none_then_the_four_kinds_and_starts_on_the_wheel(self) -> None:
        option = config_schema.option("manager", "thumbnail")
        assert option is not None

        self.assertEqual(("none", *KINDS), option.choices)
        self.assertEqual("wheel", option.default)

    def test_it_leads_the_artwork_settings_after_the_appearance(self) -> None:
        options = [option for block in _blocks() if block["name"] == "manager"
                   for option in block["options"]]

        self.assertEqual([(option["key"], option.get("group")) for option in options[:5]],
                         [("appearance", "appearance"), ("thumbnail", "thumbnails"),
                          ("thumbnail_shape", "thumbnails"), ("thumbnail_height", "thumbnails"),
                          ("thumbnail_frame", "thumbnails")])

    def test_every_manager_setting_has_a_heading(self) -> None:
        """One setting without a group would be drawn under Other."""
        options = [option for block in _blocks() if block["name"] == "manager"
                   for option in block["options"]]

        self.assertEqual([option["key"] for option in options if not option.get("group")],
                         [])

    def test_each_kind_is_called_what_the_media_grid_calls_it(self) -> None:
        """One word per kind for a translator, so Playfield here is Playfield there."""
        option = config_schema.option("manager", "thumbnail")
        assert option is not None

        self.assertEqual(
            {"none": t("config.manager.thumbnail.choice.none"),
             **{kind: t(f"media.kind.{kind}.label") for kind in KINDS}},
            option.choice_labels)

    def test_its_own_words_are_in_the_catalog(self) -> None:
        for said in ("label", "description", "choice.none"):
            with self.subTest(said=said):
                key = f"config.manager.thumbnail.{said}"
                self.assertNotEqual(key, t(key))

    def test_the_editor_it_asks_for_is_one_the_manager_serves(self) -> None:
        option = config_schema.option("manager", "thumbnail")
        assert option is not None

        self.assertIn(option.editor, config_schema.EDITORS)
        self.assertIn(option.editor, settings.EDITORS)


class OfferedTests(unittest.TestCase):
    """What the select on the Manager page holds, given what Media Kinds has off."""

    def _offered(self, value: str, hidden: set[str] | None,
                 writable: bool = True) -> Mock:
        suggestions = ({} if hidden is None
                       else {config_schema.EDITOR_THUMBNAIL: hidden})
        self.save = AsyncMock(return_value=True)
        with patch.object(panel, "select") as select:
            settings.control_for(_option(), value, self.save, writable=writable,
                                 suggestions=suggestions)
        select.assert_called_once()
        return select

    def test_a_blank_stored_value_reads_as_the_default(self) -> None:
        """A settings file that carries the key empty must still draw the page."""
        select = self._offered("", set())

        self.assertEqual("wheel", select.call_args.args[1])

    def test_a_blank_shape_or_height_reads_as_the_default(self) -> None:
        block = next(block for block in _blocks() if block["name"] == "manager")

        with patch.object(panel, "select") as select:
            settings.section_rows(Mock(), "manager", block["options"],
                                  {"manager": {"thumbnail_shape": "", "thumbnail_height": ""}},
                                  True, lambda: None)

        sized = {tuple(one.args[0]): one.args[1] for one in select.call_args_list}
        self.assertEqual((sized[("auto", "square", "wide")], sized[("small", "medium", "large")]),
                         ("auto", "small"))

    def test_every_kind_is_offered_where_media_kinds_keeps_them_all(self) -> None:
        select = self._offered("wheel", set())

        self.assertEqual(["none", *KINDS], list(select.call_args.args[0]))
        self.assertEqual("wheel", select.call_args.args[1])

    def test_a_kind_switched_off_is_not_offered(self) -> None:
        select = self._offered("wheel", {"playfield", "logo"})

        self.assertEqual(["none", "wheel", "backglass"], list(select.call_args.args[0]))

    def test_the_current_value_is_offered_though_its_kind_is_switched_off(self) -> None:
        select = self._offered("playfield", {"playfield", "logo"})

        self.assertEqual(["none", "wheel", "backglass", "playfield"],
                         list(select.call_args.args[0]))
        self.assertEqual("playfield", select.call_args.args[1])

    def test_each_offered_choice_carries_its_word(self) -> None:
        select = self._offered("none", {"backglass"})

        self.assertEqual(
            {"none": t("config.manager.thumbnail.choice.none"),
             **{kind: t(f"media.kind.{kind}.label")
                for kind in ("wheel", "playfield", "logo")}},
            select.call_args.args[0])

    def test_without_the_library_s_answer_all_four_are_offered(self) -> None:
        """Another machine's page, or a policy that could not be read."""
        select = self._offered("logo", None)

        self.assertEqual(["none", *KINDS], list(select.call_args.args[0]))

    def test_a_pick_is_stored(self) -> None:
        select = self._offered("wheel", set())

        asyncio.run(select.call_args.args[2](SimpleNamespace(value="logo")))

        self.save.assert_awaited_once_with("logo")

    def test_a_page_that_cannot_be_written_draws_it_disabled(self) -> None:
        self.assertTrue(self._offered("wheel", set(), writable=False)
                        .call_args.kwargs["disabled"])


class ReadForThePageTests(unittest.IsolatedAsyncioTestCase):
    """The library's answer, read again each time the page opens."""

    def setUp(self) -> None:
        self.enterContext(patch.object(settings.run, "io_bound",
                                       new=AsyncMock(side_effect=_now)))

    async def test_the_manager_page_reads_what_media_kinds_has_off(self) -> None:
        for stored in (["playfield", "logo"], "playfield, logo"):
            with self.subTest(stored=stored):
                library = Mock()
                library.library_policy.return_value = {"hidden_media_kinds": stored}

                offered = await settings._suggestions(library, _blocks(), MANAGER)

                self.assertEqual({"playfield", "logo"},
                                 offered[config_schema.EDITOR_THUMBNAIL])

    async def test_the_select_on_the_page_leaves_out_a_kind_switched_off(self) -> None:
        library = Mock()
        library.library_policy.return_value = {"hidden_media_kinds": ["backglass"]}
        offered = await settings._suggestions(library, _blocks(), MANAGER)
        block = next(block for block in _blocks() if block["name"] == "manager")

        with patch.object(panel, "select") as select:
            settings.section_rows(library, "manager", block["options"],
                                  {"manager": {"thumbnail": "logo"}}, True, lambda: None,
                                  suggestions=offered)

        art = next(one for one in select.call_args_list if "none" in one.args[0])
        self.assertEqual(["none", "wheel", "playfield", "logo"], list(art.args[0]))
        self.assertEqual("logo", art.args[1])

    async def test_a_policy_that_cannot_be_read_still_draws_the_page(self) -> None:
        library = Mock()
        library.library_policy.side_effect = OSError("the library did not answer")

        with self.assertLogs("vpinfe.manager.settings", "WARNING"):
            offered = await settings._suggestions(library, _blocks(), MANAGER)

        self.assertNotIn(config_schema.EDITOR_THUMBNAIL, offered)

    async def test_a_page_without_the_setting_does_not_ask(self) -> None:
        library = Mock()

        await settings._suggestions(library, _blocks(), ("logger",))

        library.library_policy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
