"""A mobile device's Sending section: how a send goes, saved through the config route."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from common.i18n import t
from manager.devices import devices
from manager.kit import panel
from manager.settings import settings

SCHEMA = [{"name": "vpxmobile", "options": [
    {"key": "send_masked_config", "type": "bool", "default": "false"},
    {"key": "config_mask", "type": "string", "default": "mobile"},
    {"key": "chunk_size", "type": "int", "default": "1048576"},
]}]


async def _rows(values: dict[str, Any]) -> tuple[list[Any], Mock]:
    client = Mock()
    client.config_schema.return_value = SCHEMA
    client.config_values.return_value = {"vpxmobile": values}
    with patch.object(devices, "ApiClient", return_value=client), \
            patch.object(devices.offload, "io", AsyncMock(side_effect=lambda f, *a: f(*a))):
        return await devices._sending_rows(), client


class TheRows(unittest.IsolatedAsyncioTestCase):
    async def test_three_settings_each_with_the_sentence_that_says_what_it_does(self) -> None:
        rows, _client = await _rows({})

        labels = [label for label, _draw in rows if isinstance(label, str)]
        self.assertEqual(labels, [t("config.vpxmobile.send_masked_config.label"),
                                  t("config.vpxmobile.config_mask.label"),
                                  t("config.vpxmobile.chunk_size.label")])
        self.assertEqual(sum(label is panel.ASIDE for label, _draw in rows), 4)

    async def test_the_example_names_a_file_made_from_the_word(self) -> None:
        for typed, said in (("mobile", "Example: Attack from Mars.mobile.ini"),
                            (" .cab.ini ", "Example: Attack from Mars.cab.ini"),
                            ("", "Without a word, no mobile settings file is sent"),
                            (None, "Without a word, no mobile settings file is sent")):
            with self.subTest(typed=typed):
                self.assertEqual(devices._mobile_file_shape(typed), said)

    async def test_the_word_starts_as_mobile(self) -> None:
        shown: dict[str, Any] = {}
        with patch.object(devices.panel, "field",
                          lambda v, on, **_k: shown.setdefault("value", v)):
            await _rows({})

        self.assertEqual(shown["value"], "mobile")

    async def test_the_word_a_new_install_starts_with_is_mobile(self) -> None:
        from common.config import config_schema
        option = config_schema.option("vpxmobile", "config_mask")
        assert option is not None

        self.assertEqual(option.default, "mobile")

    async def test_the_page_and_the_sender_keep_the_same_word(self) -> None:
        from configparser import ConfigParser

        from common.games import mobile_ops
        for mask in ("mobile", ".mobile.", "cab.ini", ".cab.INI", " cab .ini "):
            parser = ConfigParser()
            parser.read_dict({"vpxmobile": {"send_masked_config": "true", "config_mask": mask}})
            with self.subTest(mask=mask), patch.object(mobile_ops, "get_ini_config",
                                                       return_value=parser):
                self.assertEqual(devices._mobile_word(mask), mobile_ops._sending()[1])

    async def test_the_labels_are_the_words_a_person_would_use(self) -> None:
        self.assertEqual(t("config.vpxmobile.send_masked_config.label"),
                         "Send Mobile Settings")
        self.assertEqual(t("config.vpxmobile.config_mask.label"), "Mobile INI Word")

    async def test_a_config_that_cannot_be_read_says_so_instead_of_drawing_nothing(self) -> None:
        client = Mock()
        client.config_schema.side_effect = RuntimeError("down")
        with patch.object(devices, "ApiClient", return_value=client), \
                patch.object(devices.offload, "io",
                             AsyncMock(side_effect=lambda f, *a: f(*a))):
            rows = await devices._sending_rows()

        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0][0], panel.ASIDE)


class SavingThem(unittest.IsolatedAsyncioTestCase):
    async def test_the_switch_writes_send_masked_config(self) -> None:
        written: dict[str, Any] = {}
        with patch.object(devices.settings_page, "saver",
                          lambda _s, section, key, *_r: (
                              AsyncMock(side_effect=lambda v: written.update(
                                  {f"{section}.{key}": v})))):
            controls: dict[str, Any] = {}
            with patch.object(devices.panel, "switch",
                              lambda v, on: controls.setdefault("switch", on)):
                await _rows({})
            await controls["switch"](MagicMock(value=True))

        self.assertEqual(written, {"vpxmobile.send_masked_config": True})

    async def test_the_word_is_saved_without_spaces_or_dots(self) -> None:
        written: dict[str, Any] = {}
        with patch.object(devices.settings_page, "saver",
                          lambda _s, section, key, *_r: (
                              AsyncMock(side_effect=lambda v: written.update(
                                  {f"{section}.{key}": v})))):
            controls: dict[str, Any] = {}
            with patch.object(devices.panel, "field",
                              lambda v, on, **_k: controls.setdefault("field", on)):
                await _rows({})
            await controls["field"](" .mobile. ")

        self.assertEqual(written, {"vpxmobile.config_mask": "mobile"})

    async def test_an_emptied_chunk_size_goes_back_to_the_default(self) -> None:
        written: dict[str, Any] = {}
        with patch.object(devices.settings_page, "saver",
                          lambda _s, section, key, *_r: (
                              AsyncMock(side_effect=lambda v: written.update(
                                  {f"{section}.{key}": v})))):
            controls: dict[str, Any] = {}
            with patch.object(devices.panel, "number",
                              lambda v, on, **_k: controls.setdefault("number", on)):
                await _rows({})
            await controls["number"](MagicMock(value=None))

        self.assertEqual(written, {"vpxmobile.chunk_size": 1048576})


class SettingsNoLongerHoldsIt(unittest.TestCase):
    def test_no_settings_page_is_named_for_vpx_mobile(self) -> None:
        pages = [page for _group, entries in settings.DEVICE_INDEX for page in entries]

        self.assertFalse([page for page in pages if "vpxmobile" in str(page[0])])


if __name__ == "__main__":
    unittest.main()
