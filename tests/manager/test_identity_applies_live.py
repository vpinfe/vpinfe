"""A feature switch is applied where the Manager stands: the shell follows, nothing reloads."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from common.devices import install_identity
from manager.devices import devices
from manager.kit import panel
from manager.settings import settings


class FeatureSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.library = MagicMock()
        self.library.config_values.return_value = {
            "device": {"features": [install_identity.LIBRARY]}}
        self.calls: list[str] = []
        self.written = self.enterContext(patch.object(
            settings, "_write", new=AsyncMock(return_value=True)))
        self.enterContext(patch.object(
            settings.offload, "io",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(patch.object(settings, "ui"))
        self.enterContext(patch.object(settings.config_schema, "option", return_value=None))
        self.version_rows = self.enterContext(patch.object(
            devices, "version_rows", new=AsyncMock(return_value=[("Version", "v3.0.0")])))
        for name in ("field", "note", "intro", "action"):
            self.enterContext(patch.object(panel, name))
        self.facts = self.enterContext(patch.object(panel, "facts"))
        self.region = self.enterContext(patch.object(panel, "region"))
        self.region.return_value = Mock(refilled=AsyncMock(), refill=Mock())
        self.select = self.enterContext(patch.object(panel, "select"))
        self.switch = self.enterContext(patch.object(panel, "switch"))
        self.state: dict[str, Any] = {
            "refresh_shell": AsyncMock(side_effect=lambda: self.calls.append("shell")),
            "reindex_settings": Mock(side_effect=lambda: self.calls.append("index"))}

    async def _draw(self, install_id: str = "") -> None:
        """The page, and the part of it a region draws and draws again."""
        await settings._identity_page(self.library, "host", Mock(), self.state, install_id)
        await self.region.call_args.args[0]()

    def _version_context(self) -> dict[str, Any]:
        """What the Version rows were asked with."""
        call = self.version_rows.await_args
        assert call is not None
        context: dict[str, Any] = call.args[0]
        return context

    async def _page(self) -> dict[str, Any]:
        """The feature switches the page drew, by feature."""
        await self._draw()
        names = list(install_identity.FEATURES)
        return {name: call.args[1] for name, call in zip(
            names, self.switch.call_args_list[-len(names):], strict=True)}

    async def test_a_switch_is_written_in_the_order_the_install_declares_them(self) -> None:
        switches = await self._page()

        await switches[install_identity.DEVICES](SimpleNamespace(value=True))

        self.written.assert_awaited_once_with(
            self.library, "device", "features",
            [install_identity.LIBRARY, install_identity.DEVICES])

    async def test_the_shell_reads_the_install_again_and_then_the_index_follows(self) -> None:
        switches = await self._page()

        await switches[install_identity.FRONTEND](SimpleNamespace(value=True))

        self.assertEqual(self.calls, ["shell", "index"])
        self.ui.navigate.reload.assert_not_called()

    async def test_a_refused_write_leaves_the_shell_as_it_was(self) -> None:
        self.written.return_value = False
        switches = await self._page()

        await switches[install_identity.FRONTEND](SimpleNamespace(value=True))

        self.assertEqual(self.calls, [])

    async def test_a_second_switch_builds_on_the_first(self) -> None:
        switches = await self._page()
        first, second = install_identity.DEVICES, install_identity.FRONTEND

        await switches[first](SimpleNamespace(value=True))
        await switches[second](SimpleNamespace(value=True))

        self.assertEqual(self.written.call_args.args[3],
                         [install_identity.LIBRARY, install_identity.FRONTEND,
                          install_identity.DEVICES])

    async def test_a_language_is_saved_and_its_restart_chip_drawn_without_a_reload(self) -> None:
        option = SimpleNamespace(label="Language", key="language", section="device",
                                 choices=("auto", "en"), choice_labels={},
                                 default="auto", description="")
        chips = self.enterContext(patch.object(settings, "Waiting"))
        chips.return_value.refresh = AsyncMock()
        self.enterContext(patch.object(settings, "_waiting", new=AsyncMock(return_value={})))
        self.enterContext(patch.object(
            settings.config_schema, "option",
            side_effect=lambda _section, key: option if key == "language" else None))

        await self._draw()
        picked = self.select.call_args.args[2]
        await picked(SimpleNamespace(value="en"))

        self.written.assert_awaited_once_with(self.library, "device", "language", "en")
        chips.return_value.refresh.assert_awaited_once()
        self.ui.navigate.reload.assert_not_called()

    async def test_a_language_set_outside_the_list_is_still_offered(self) -> None:
        option = SimpleNamespace(label="Language", key="language", section="device",
                                 choices=("auto", "en"), choice_labels={},
                                 default="auto", description="")
        self.library.config_values.return_value = {"device": {"language": "xx"}}
        self.enterContext(patch.object(settings, "Waiting"))
        self.enterContext(patch.object(settings, "_waiting", new=AsyncMock(return_value={})))
        self.enterContext(patch.object(
            settings.config_schema, "option",
            side_effect=lambda _section, key: option if key == "language" else None))

        await self._draw()

        self.assertEqual(list(self.select.call_args.args[0]), ["auto", "en", "xx"])
        self.assertEqual(self.select.call_args.args[1], "xx")

    def _channel(self) -> SimpleNamespace:
        option = SimpleNamespace(label="Channel", key="update_channel", section="device",
                                 choices=("stable", "beta"), choice_labels={},
                                 default="stable", description="")
        self.enterContext(patch.object(
            settings.config_schema, "option",
            side_effect=lambda _section, key: option if key == "update_channel" else None))
        return option

    async def test_the_update_channel_is_saved_as_picked_and_the_part_draws_again(self) -> None:
        self._channel()

        await self._draw()
        self.assertEqual(self.select.call_args.args[1], "stable")
        await self.select.call_args.args[2](SimpleNamespace(value="beta"))

        self.written.assert_awaited_once_with(self.library, "device", "update_channel", "beta")
        self.region.return_value.refill.assert_called_once_with()

    async def test_the_channel_shown_after_a_change_is_the_one_picked(self) -> None:
        self._channel()

        await self._draw()
        await self.select.call_args.args[2](SimpleNamespace(value="beta"))
        await self.region.call_args.args[0]()

        self.assertEqual(self.select.call_args.args[1], "beta")

    async def test_a_refused_channel_write_draws_the_saved_channel_again(self) -> None:
        self._channel()
        self.written.return_value = False

        await self._draw()
        await self.select.call_args.args[2](SimpleNamespace(value="beta"))
        await self.region.call_args.args[0]()

        self.region.return_value.refill.assert_called_once_with()
        self.assertEqual(self.select.call_args.args[1], "stable")

    async def test_a_slow_answer_from_github_does_not_hold_the_page_back(self) -> None:
        self._channel()
        release = asyncio.Event()

        async def slow(context: dict[str, Any]) -> list[tuple[Any, Any]]:
            await release.wait()
            return [("Version", "v9")]

        self.version_rows.side_effect = slow
        with patch.object(settings, "VERSION_WAIT", 0.01):
            await self._draw()

        entries = self.facts.call_args.args[1]
        self.assertIn(("Channel"), [entry[0] for entry in entries])
        self.region.return_value.refill.assert_not_called()
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.region.return_value.refill.assert_called_once_with()

    async def test_the_version_section_leads_the_channel_and_is_asked_of_this_install(self) -> None:
        self._channel()
        self.library.config_values.return_value = {"device": {"name": "Cab"}}

        await self._draw("inst-1")

        entries = self.facts.call_args.args[1]
        heading = next(i for i, entry in enumerate(entries)
                       if entry[0] is panel.HEADING and entry[1] == "Version")
        self.assertEqual(entries[heading + 1], ("Version", "v3.0.0"))
        self.assertEqual(entries[heading + 2][0], "Channel")
        context = self._version_context()
        self.assertEqual(context["local_device_id"], "inst-1")
        self.assertEqual(context["device"], {"device_id": "inst-1", "display_name": "Cab"})
        self.assertIs(context["library"], self.library)

    async def test_check_now_draws_the_part_again(self) -> None:
        await self._draw("inst-1")
        await self._version_context()["rebuild"]()

        self.region.return_value.refill.assert_called_once_with()

    async def test_a_page_with_no_shell_to_tell_still_writes(self) -> None:
        self.state.clear()
        switches = await self._page()

        await switches[install_identity.FRONTEND](SimpleNamespace(value=True))

        self.written.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
