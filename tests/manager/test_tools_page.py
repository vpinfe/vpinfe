"""Settings > VPinFE > Tools: what each Tool's discovery found, in its empty field."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi.testclient import TestClient

import httpapi
from common.config import paths
from common.host import tools, vpinos
from common.i18n import t
from manager.kit import panel
from manager.kit.data import Library
from manager.settings import settings
from tests.support.regions import Immediate

PAGE = next(page for _group, pages in settings.DEVICE_INDEX for page in pages
            if page[0] == "vpinfe.tools")


class Served:
    """The Manager's client, answered by this install's API in-process."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def _answered(self, path: str) -> Any:
        response = self._client.get(path)
        if response.is_error:
            raise RuntimeError(response.text)
        return response.json()

    def config_schema(self) -> list[dict]:
        return list(self._answered("/config/schema")["sections"])

    def config_values(self) -> dict:
        return dict(self._answered("/config/settings"))

    def config_path_checks(self) -> list[dict]:
        return list(self._answered("/config/paths")["checks"])

    def config_tools(self) -> list[dict]:
        return list(self._answered("/config/tools")["tools"])

    def put_config(self, changes: dict) -> dict:
        return dict(self._client.put("/config", json={"settings": changes}).json()["settings"])


@unittest.skipIf(sys.platform.startswith("win"), "the programs here are shell scripts")
class ToolsPageTests(unittest.IsolatedAsyncioTestCase):
    """A macOS device whose PATH is a folder of this test's, holding nothing yet, and
    whose programs answer as their file's name says: `broken` never runs."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.enterContext(patch.object(paths, "VPINFE_INI_PATH", self.root / "vpinfe.ini"))
        self.enterContext(patch.object(tools, "here", return_value=tools.DARWIN))
        self.enterContext(patch.object(vpinos, "detected", return_value=False))
        self.enterContext(patch.dict(os.environ, {"PATH": str(self.bin)}))
        self.enterContext(patch.dict(tools._PLACES, {tools.DARWIN: ()}))
        self.enterContext(patch.dict(tools._probes, clear=True))
        self.enterContext(patch.object(tools, "probed", side_effect=lambda _tool, path: (
            tools.Probe(False, reason=tools.FAILED) if "broken" in str(path)
            else tools.Probe(True, "7.1"))))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.served = Served(self.client)
        self.library = Library(self.served)  # type: ignore[arg-type]
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch.object(settings, "ui"))
        self.enterContext(patch.object(settings, "page_head"))
        self.enterContext(patch.object(panel, "region", new=Immediate))
        self.facts = self.enterContext(patch.object(panel, "facts"))
        self.field = self.enterContext(patch.object(panel, "field"))
        self.marked = self.enterContext(patch.object(panel, "value_state"))

    def _program(self, name: str, folder: Path | None = None) -> Path:
        path = (folder or self.bin) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    async def _fields(self) -> dict[str, dict[str, Any]]:
        """By label, what each Tool's field was drawn with: the default it stands for and
        whose, its placeholder and tooltip when nothing is found, and the mark beside it."""
        self.field.reset_mock()
        self.marked.reset_mock()
        await settings._draw_system_page(self.library, Mock(), MagicMock(), PAGE, {}, [])
        [drawn] = self.facts.call_args_list
        labels = [label for label, _value in drawn.args[1] if isinstance(label, str)]
        return {label: {"blank": call.kwargs.get("placeholder"),
                        "tooltip": call.kwargs.get("left_empty"),
                        "default": call.kwargs.get("default"),
                        "whose": call.kwargs.get("whose"),
                        "mark": marks.args}
                for label, call, marks in zip(labels, self.field.call_args_list,
                                              self.marked.call_args_list, strict=True)}

    def test_the_route_answers_every_tool(self) -> None:
        found = self.served.config_tools()

        self.assertEqual([row["id"] for row in found], [tool.id for tool in tools.TOOLS])
        self.assertEqual({row["id"]: row["state"] for row in found},
                         {"rar": "missing", "ffmpeg": "missing", "grim": "not_here",
                          "wf_recorder": "not_here", "gstreamer": "not_here",
                          "wtype": "not_here", "ydotool": "not_here",
                          "nvidia_smi": "not_here", "nvtop": "not_here"})
        self.assertEqual(found[0]["remedy"], {"key": "tools.rar.hint.darwin", "params":
                                              {"tool": "unar"}, "setting": "tools.rar_path",
                                              "get": None})
        self.assertEqual((found[1]["fix"], found[1]["remedy"]["get"]["source"]),
                         (tools.FIX_AUTO, "martin-riedl.de"))

    async def test_a_missing_ffmpeg_is_offered_above_the_paths(self) -> None:
        rows = await settings._tools_head(self.library, Mock(), {})

        self.assertEqual([kind for kind, _draw in rows], [panel.FULL])
        self._program("ffmpeg")
        self.assertEqual(await settings._tools_head(self.library, Mock(), {}), [])

    async def test_only_the_tools_this_platform_has_are_drawn(self) -> None:
        drawn = await self._fields()

        self.assertEqual(sorted(drawn), sorted([t("config.tools.rar_path.label"),
                                                t("config.tools.ffmpeg_path.label")]))

    async def test_an_empty_field_says_what_was_found_and_where(self) -> None:
        ffmpeg = self._program("ffmpeg")

        said = (await self._fields())[t("config.tools.ffmpeg_path.label")]

        self.assertEqual(said["default"], str(ffmpeg))
        self.assertEqual(said["whose"], t("manager.settings.tool_found_version", version="7.1"))

    async def test_a_set_program_leaves_the_default_what_would_be_found_without_it(self) -> None:
        found = self._program("ffmpeg")
        mine = self._program("ffmpeg", self.root / "mine")
        self.served.put_config({"tools": {"ffmpeg_path": str(mine)}})

        said = (await self._fields())[t("config.tools.ffmpeg_path.label")]

        self.assertEqual(said["default"], str(found))
        self.assertNotEqual(said["default"], str(mine))

    async def test_nothing_found_says_so_with_how_to_get_one(self) -> None:
        said = (await self._fields())[t("config.tools.rar_path.label")]

        self.assertEqual(said["blank"], t("word.not_found"))
        self.assertEqual(said["tooltip"], tools.hint(tools.RAR))

    async def test_a_found_program_that_does_not_run_says_why(self) -> None:
        broken = self._program("broken", self.root / "unar")
        self.enterContext(patch.dict(os.environ, {"PATH": str(broken.parent)}))
        self.enterContext(patch.dict(tools.RAR.names, {tools.DARWIN: ("broken",)}))

        said = (await self._fields())[t("config.tools.rar_path.label")]

        self.assertEqual(said["default"], str(broken))
        self.assertEqual(said["whose"], t("manager.settings.tool_found_here"))
        self.assertEqual(said["mark"], (panel.UNUSABLE, t(tools.FAILED)))

    async def test_a_set_program_that_does_not_run_is_marked_with_what_is_used(self) -> None:
        ffmpeg = self._program("ffmpeg")
        broken = self._program("broken", self.root / "mine")
        self.served.put_config({"tools": {"ffmpeg_path": str(broken)}})

        said = (await self._fields())[t("config.tools.ffmpeg_path.label")]

        self.assertEqual(said["mark"], (panel.UNUSABLE, t("manager.settings.tool_used_instead",
                                                          path=str(ffmpeg))))

    async def test_a_set_program_that_does_not_run_and_nothing_else_says_why(self) -> None:
        broken = self._program("broken", self.root / "mine")
        self.served.put_config({"tools": {"ffmpeg_path": str(broken)}})

        said = (await self._fields())[t("config.tools.ffmpeg_path.label")]

        self.assertEqual(said["mark"], (panel.UNUSABLE, t(tools.FAILED)))

    async def test_a_set_program_that_runs_keeps_the_path_check_s_mark(self) -> None:
        mine = self._program("ffmpeg", self.root / "mine")
        self.served.put_config({"tools": {"ffmpeg_path": str(mine)}})

        said = (await self._fields())[t("config.tools.ffmpeg_path.label")]

        self.assertEqual(said["mark"][0], "ok")

    async def test_the_page_draws_its_settings_when_discovery_cannot_be_read(self) -> None:
        with patch.object(self.served, "config_tools", side_effect=RuntimeError("gone")):
            drawn = await self._fields()

        self.assertIn(t("config.tools.grim_path.label"), drawn)
        self.assertEqual(drawn[t("config.tools.ffmpeg_path.label")]["blank"], "")


if __name__ == "__main__":
    unittest.main()
