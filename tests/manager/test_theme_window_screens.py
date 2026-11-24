from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi.testclient import TestClient
from screeninfo import Monitor

import httpapi
from common.config import config_schema, paths
from common.config.config_access import DisplayConfig
from common.config.config_store import ConfigStore
from common.host import display_service
from common.i18n import t
from frontend import theme_windows
from manager.kit import panel
from manager.kit.data import Library
from manager.settings import settings
from tests.support.regions import Immediate

WIDE = Monitor(x=0, y=0, width=2560, height=1440, width_mm=597, height_mm=336,
               name="DP-1", is_primary=True)
SMALL = Monitor(x=0, y=1440, width=1280, height=390, width_mm=300, height_mm=90,
                name="HDMI-1", is_primary=False)

DISPLAYS = next(page for _group, pages in settings.DEVICE_INDEX for page in pages
                if page[0] == "hardware.displays")


class Served:
    """The Manager's client, answered by this install's API in-process."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    @staticmethod
    def _answered(response: Any) -> Any:
        if response.is_error:
            raise RuntimeError(response.text)
        return response.json()

    def config_schema(self) -> list[dict]:
        return list(self._answered(self._client.get("/config/schema"))["sections"])

    def config_values(self) -> dict:
        return dict(self._answered(self._client.get("/config/settings")))

    def config_path_checks(self) -> list[dict]:
        return list(self._answered(self._client.get("/config/paths"))["checks"])

    def put_config(self, changes: dict) -> dict:
        sent = self._client.put("/config", json={"settings": changes})
        return dict(self._answered(sent)["settings"])


class ATheme(unittest.TestCase):
    """An install whose active theme's manifest declares a topper, and whose file still
    holds a screen for an apron an earlier theme declared."""

    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.themes = root / "themes"
        self._theme("Towers", ["playfield", "backglass", "scoreview", "topper"])
        self._theme("Plain", None)
        self.ini = root / "vpinfe.ini"
        self.enterContext(patch.object(paths, "VPINFE_INI_PATH", self.ini))
        self.enterContext(patch("frontend.theme_api.THEMES_DIR", self.themes))
        store = ConfigStore(self.ini)
        store.set_value("themes", "active", "Towers")
        store.set_value("windows.apron", "screen_id", "2")
        store.save()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _theme(self, name: str, windows: list[str] | None) -> None:
        folder = self.themes / name
        folder.mkdir(parents=True)
        manifest: dict[str, Any] = {"name": name, "min_vpinfe": "3.0"}
        if windows is not None:
            manifest[theme_windows.MANIFEST_KEY] = windows
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def _activate(self, name: str) -> None:
        store = ConfigStore(self.ini)
        store.set_value("themes", "active", name)
        store.save()

    def _stored(self, section: str) -> str:
        return ConfigStore(self.ini).config.get(section, "screen_id", fallback="")

    def _sections(self) -> dict[str, dict]:
        return {block["name"]: block
                for block in self.client.get("/config/schema").json()["sections"]}


class ServedAsSettingsTests(ATheme):
    def test_its_own_window_is_served_after_the_three_every_theme_has(self) -> None:
        names = list(self._sections())

        self.assertEqual(names[names.index("windows.scoreview") + 1], "windows.topper")
        self.assertEqual([name for name in names if name.startswith("windows.")],
                         ["windows.playfield", "windows.backglass", "windows.scoreview",
                          "windows.topper"])

    def test_its_screen_is_picked_from_the_screens_like_the_others(self) -> None:
        sections = self._sections()
        [topper] = sections["windows.topper"]["options"]
        [scoreview] = [option for option in sections["windows.scoreview"]["options"]
                        if option["key"] == "screen_id"]

        self.assertEqual(
            {field: topper[field] for field in ("key", "type", "default", "suggest", "label")},
            {"key": "screen_id", "type": "int", "default": "",
             "suggest": config_schema.SUGGEST_SCREENS, "label": scoreview["label"]})
        self.assertTrue(sections["windows.topper"]["writable"])

    def test_a_screen_set_for_it_is_where_the_frontend_reads_it(self) -> None:
        body = {"settings": {"windows.topper": {"screen_id": 1}}}
        response = self.client.put("/config", json=body)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settings"]["windows.topper"], {"screen_id": 1})
        self.assertEqual(
            DisplayConfig.from_config(ConfigStore(self.ini)).window_screen_id(
                theme_windows.screen_key("topper")),
            "1")

    def test_a_window_the_theme_does_not_declare_is_neither_served_nor_set(self) -> None:
        body = {"settings": {"windows.apron": {"screen_id": 1}}}
        response = self.client.put("/config", json=body)

        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("windows.apron", self._sections())
        self.assertNotIn("windows.apron", self.client.get("/config/settings").json())

    def test_the_screen_stored_for_a_window_no_longer_declared_is_kept(self) -> None:
        self.client.put("/config", json={"settings": {"windows.topper": {"screen_id": 1}}})

        self.assertEqual(self._stored("windows.apron"), "2")

    def test_the_topper_is_served_to_a_theme_that_declares_no_windows(self) -> None:
        self._activate("Plain")

        self.assertEqual([name for name in self._sections() if name.startswith("windows.")],
                         ["windows.playfield", "windows.backglass", "windows.scoreview",
                          "windows.topper"])


class DrawnOnDisplaysTests(ATheme, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.served = Served(self.client)
        self.library = Library(self.served)  # type: ignore[arg-type]
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch.object(display_service, "get_display_monitors",
                                       return_value=[WIDE, SMALL]))
        self.enterContext(patch.object(display_service, "get_display_names",
                                       return_value=["", ""]))
        self.enterContext(patch.object(settings, "ui"))
        self.enterContext(patch.object(settings, "page_head"))
        self.enterContext(patch.object(
            panel, "select",
            side_effect=lambda offered, value, pick, **_: SimpleNamespace(
                offered=offered, value=value, pick=pick)))
        self.enterContext(patch.object(panel, "region", new=Immediate))
        self.facts = self.enterContext(patch.object(panel, "facts"))

    async def _drawn(self) -> list[tuple[Any, Any]]:
        self.facts.reset_mock()
        await settings._draw_system_page(self.library, Mock(), MagicMock(), DISPLAYS, {}, [])
        return list(self.facts.call_args.args[1])

    @staticmethod
    def _headings(entries: list[tuple[Any, Any]]) -> list[str]:
        return [str(value) for label, value in entries if label is panel.HEADING]

    def _label_of(self, section: str, key: str) -> str:
        [one] = [option for block in self.served.config_schema() if block["name"] == section
                 for option in block["options"] if option["key"] == key]
        return str(one["label"])

    async def test_the_topper_is_drawn_by_the_picture_and_has_no_section_of_its_own(
            self) -> None:
        entries = await self._drawn()

        self.assertEqual(self._headings(entries),
                         [t("manager.settings.section_screens"),
                          t("manager.settings.section_backglass"),
                          t("manager.settings.section_scoreview"),
                          t("manager.settings.section_real_dmd")])
        self.assertEqual(entries[1][0], panel.FULL)

    async def test_what_the_picture_draws_is_not_a_field_beside_it(self) -> None:
        entries = await self._drawn()
        before_dmd = entries[:entries.index(
            (panel.HEADING, t("manager.settings.section_real_dmd")))]
        fields = [label for label, _ in before_dmd if isinstance(label, str)]

        self.assertEqual(fields, [self._label_of("windows.backglass", "override"),
                                  self._label_of("windows.scoreview", "override")])

    async def test_a_window_override_and_the_real_dmd_stay_as_fields(self) -> None:
        labels = [label for label, _ in await self._drawn() if isinstance(label, str)]

        self.assertEqual(labels.count(self._label_of("windows.backglass", "override")), 2)
        self.assertIn(self._label_of("real_dmd", "enabled"), labels)

    async def test_a_window_no_longer_declared_is_not_drawn(self) -> None:
        self.assertNotIn("Apron", self._headings(await self._drawn()))

    async def test_choosing_a_theme_that_declares_no_topper_changes_nothing(self) -> None:
        before = self._headings(await self._drawn())
        self._activate("Plain")

        self.assertEqual(self._headings(await self._drawn()), before)


if __name__ == "__main__":
    unittest.main()
