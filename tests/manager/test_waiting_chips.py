"""The settings page's side of what is waiting: a chip beside a setting that is saved and
not yet in effect, a port refused at its field, and the question before a change that
cuts the Manager off from the device making it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from common.i18n import t
from common.settings import config_service
from manager.frontend import themes
from manager.kit.api import ApiError
from manager.settings import settings
from tests.support.clicks import press

PORT = "network.http_port"


def _waiting(*entries: tuple[str, str]) -> dict[str, Any]:
    return {"waiting": [{"kind": "setting", "section": section, "key": key,
                         "waits_for": waits}
                        for (section, key), waits in
                        ((one.split(".", 1), waits) for one, waits in entries)]}


def _option(section: str, key: str) -> dict[str, Any]:
    [one] = [option for block in config_service.schema()["sections"]
             for option in block["options"]
             if (option["section"], option["key"]) == (section, key)]
    return one


class _Source:
    def __init__(self) -> None:
        self.answers: list[dict[str, Any]] = [{}]

    def config_waiting(self) -> dict[str, Any]:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _OnAPage(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.ui = self.enterContext(patch.object(settings, "ui"))
        self.enterContext(patch.object(
            settings.offload, "io",
            new=AsyncMock(side_effect=lambda call, *args: call(*args))))


class TheChipTests(_OnAPage):
    def _drawn(self, waiting: settings.Waiting, option: dict[str, Any]) -> Any:
        chip = self.ui.label.return_value.classes.return_value
        chip.is_deleted = False
        waiting.beside(Mock(), option)()
        return chip

    def test_a_setting_that_waits_on_a_restart_says_so(self) -> None:
        waiting = settings.Waiting(_Source(), _waiting((PORT, "restart")))

        chip = self._drawn(waiting, _option("network", "http_port"))

        chip.set_visibility.assert_called_with(True)
        chip.set_text.assert_called_with(t("manager.settings.chip_restart"))

    def test_a_setting_that_waits_only_on_a_reopen_says_that(self) -> None:
        waiting = settings.Waiting(_Source(), _waiting(("chromium.path", "reopen")))

        chip = self._drawn(waiting, _option("chromium", "path"))

        chip.set_text.assert_called_with(t("manager.settings.chip_reopen"))

    def test_a_setting_that_is_not_waiting_draws_its_chip_hidden(self) -> None:
        waiting = settings.Waiting(_Source(), _waiting())

        chip = self._drawn(waiting, _option("network", "http_port"))

        chip.set_visibility.assert_called_with(False)
        chip.set_text.assert_not_called()

    def test_an_extension_waiting_for_a_restart_is_not_a_setting_s_chip(self) -> None:
        found = {"waiting": [{"kind": "extension", "section": "extensions",
                              "key": "vpinplay", "waits_for": "restart"}]}

        self.assertEqual(settings.Waiting(_Source(), found).entries, {})

    async def test_the_chip_appears_after_a_save_and_clears_when_changed_back(self) -> None:
        source = _Source()
        source.answers = [_waiting((PORT, "restart")), _waiting()]
        waiting = settings.Waiting(source, _waiting())
        chip = self._drawn(waiting, _option("network", "http_port"))
        saved = AsyncMock(return_value=True)

        await waiting.after(saved)(9001)

        chip.set_visibility.assert_called_with(True)
        chip.set_text.assert_called_with(t("manager.settings.chip_restart"))

        await waiting.after(saved)(8001)

        chip.set_visibility.assert_called_with(False)

    async def test_a_save_that_failed_asks_nothing(self) -> None:
        source = Mock()
        waiting = settings.Waiting(source)

        done = await waiting.after(AsyncMock(return_value=False))(9001)

        self.assertFalse(done)
        source.config_waiting.assert_not_called()


class ARefusedPortTests(_OnAPage):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args: call(*args))))

    async def _write(self, library: Mock, refusal: settings.Refusal) -> bool:
        return await press(settings._write, library, "network", "http_port", 70000, refusal)

    async def test_a_refusal_is_said_at_the_field_and_not_in_a_toast(self) -> None:
        library = Mock()
        library.put_config.side_effect = ApiError(
            "Use a port", {"settings": {PORT: "Use a port from 1 to 65535"}})
        refusal = settings.Refusal()
        control = MagicMock(is_deleted=False)
        refusal.attach(control)

        done = await self._write(library, refusal)

        self.assertFalse(done)
        self.assertEqual(control.props.__setitem__.call_args_list[-1].args,
                         ("error-message", "Use a port from 1 to 65535"))
        self.ui.notify.assert_not_called()

    async def test_a_failure_that_is_not_about_the_port_is_a_toast(self) -> None:
        library = Mock()
        library.put_config.side_effect = ApiError("It did not answer")
        refusal = settings.Refusal()
        control = MagicMock(is_deleted=False)
        refusal.attach(control)

        await self._write(library, refusal)

        self.ui.notify.assert_called_once()
        control.update.assert_not_called()

    async def test_a_saved_port_clears_the_refusal(self) -> None:
        refusal = settings.Refusal()
        control = MagicMock(is_deleted=False)
        refusal.attach(control)

        done = await self._write(Mock(), refusal)

        self.assertTrue(done)
        control.props.assert_called_with(remove="error error-message")

    def test_only_a_port_gets_a_place_to_say_why(self) -> None:
        drawn: dict[str, Any] = {}

        def control_for(option: dict, value: Any, save: Any, **kwargs: Any) -> Any:
            drawn[option["key"]] = kwargs.get("refusal")
            return Mock()

        with patch.object(settings, "control_for", side_effect=control_for):
            settings.section_rows(
                Mock(), "network",
                [_option("network", "http_port"), _option("network", "http_bind")],
                {}, True, Mock())

        self.assertIsInstance(drawn["http_port"], settings.Refusal)
        self.assertIsNone(drawn["http_bind"])


class TheAddressQuestionTests(_OnAPage):
    def _save(self, *, elsewhere: bool) -> Any:
        """The save handed to the control for the HTTP address."""
        handed: list[Any] = []
        self.refill = Mock()
        self.saved = AsyncMock(return_value=True)

        def control_for(option: dict, value: Any, save: Any, **kwargs: Any) -> Any:
            handed.append(save)
            return Mock()

        with patch.object(settings, "control_for", side_effect=control_for), \
                patch.object(settings, "saver", return_value=self.saved):
            settings.section_rows(Mock(), "network", [_option("network", "http_bind")],
                                  {}, True, self.refill, from_elsewhere=elsewhere)
        return handed[0]

    async def test_a_browser_on_another_device_is_asked_first(self) -> None:
        save = self._save(elsewhere=True)

        with patch.object(settings.confirm, "ask", AsyncMock(return_value=True)) as ask:
            self.assertTrue(await save("127.0.0.1"))

        ask.assert_awaited_once()
        assert ask.await_args is not None
        self.assertEqual(ask.await_args.kwargs["detail"],
                         t("manager.settings.bind_detail"))
        self.saved.assert_awaited_once_with("127.0.0.1")

    async def test_saying_no_saves_nothing_and_puts_the_old_address_back(self) -> None:
        save = self._save(elsewhere=True)

        with patch.object(settings.confirm, "ask", AsyncMock(return_value=False)):
            self.assertFalse(await save("127.0.0.1"))

        self.saved.assert_not_awaited()
        self.refill.assert_called_once()

    async def test_going_back_to_every_interface_is_not_asked(self) -> None:
        save = self._save(elsewhere=True)
        default = _option("network", "http_bind")["default"]

        with patch.object(settings.confirm, "ask", AsyncMock()) as ask:
            await save(default)

        ask.assert_not_awaited()
        self.saved.assert_awaited_once_with(default)

    async def test_a_browser_on_this_machine_is_not_asked(self) -> None:
        save = self._save(elsewhere=False)

        with patch.object(settings.confirm, "ask", AsyncMock()) as ask:
            await save("127.0.0.1")

        ask.assert_not_awaited()
        self.saved.assert_awaited_once_with("127.0.0.1")


class MakeActiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_saves_without_asking(self) -> None:
        library = Mock()
        again = AsyncMock()
        with patch.object(themes, "ui"), \
                patch.object(themes.run, "io_bound",
                             new=AsyncMock(side_effect=lambda call, *args: call(*args))), \
                patch.object(themes.confirm, "ask", AsyncMock()) as ask:
            await press(themes._activate, library, {"key": "k", "name": "Name"}, again)

        ask.assert_not_awaited()
        library.activate_theme.assert_called_once_with("k")
        again.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
