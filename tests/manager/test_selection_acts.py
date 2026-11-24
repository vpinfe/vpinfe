"""What a selection of players, launchers, themes or devices offers: the verbs that apply to
any of them, over the ones they apply to."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from common.i18n import t
from common.runtime import lifecycle
from manager.devices import devices
from manager.extensions import extensions
from manager.frontend import launchers, players, themes
from tests.support.clicks import press


def _player(player_id: str, **more: Any) -> dict[str, Any]:
    return {"id": player_id, "name": player_id, "initials": "", "owner": False,
            "guest": False, "up": False, "shares_initials_with": [], **more}


def _theme(key: str, **more: Any) -> dict[str, Any]:
    return {"key": key, "name": key, "installed": True, "active": False,
            "update_available": False, **more}


def _launcher(launcher_id: str) -> dict[str, Any]:
    return {"launcher_id": launcher_id, "display_name": launcher_id}


class Players(unittest.TestCase):
    def _acts(self, chosen: list[dict[str, Any]]) -> list:
        return players.acts(Mock(), {}, chosen, lambda: None)

    def test_guests_and_kept_players_together_offer_both_sign_out_and_remove(self) -> None:
        made = self._acts([_player("g", guest=True), _player("k")])

        self.assertEqual([one.label for one in made],
                         [t("manager.players.sign_out"), t("word.remove")])

    def test_the_owner_among_others_is_left_out_of_remove(self) -> None:
        made = self._acts([_player("o", owner=True), _player("k")])

        self.assertEqual([one.label for one in made], [t("word.remove")])
        self.assertIsNotNone(made[0].run)

    def test_the_owner_alone_is_refused_with_why(self) -> None:
        (verb,) = self._acts([_player("o", owner=True)])

        self.assertIsNone(verb.run)
        self.assertTrue(verb.hint)


class RemovingSeveralPlayers(unittest.IsolatedAsyncioTestCase):
    async def test_the_owner_is_not_among_those_removed(self) -> None:
        made = players.acts(Mock(), {}, [_player("o", owner=True), _player("k")], lambda: None)
        remove = made[0]

        with patch.object(players, "remove", new=AsyncMock()) as removed:
            assert remove.run is not None
            await press(remove.run)

        assert removed.await_args is not None
        self.assertEqual([one["id"] for one in removed.await_args.args[3]], ["k"])


class Themes(unittest.TestCase):
    def test_each_verb_shows_when_it_applies_to_any_of_them(self) -> None:
        chosen = [_theme("a", installed=False), _theme("b", update_available=True),
                  _theme("c")]

        made = themes.acts(Mock(), chosen, Mock())

        self.assertEqual([one.label for one in made],
                         [t("manager.themes.install"), t("manager.themes.update"),
                          t("word.make_active"), t("word.uninstall")])

    def test_make_active_is_for_one_at_a_time(self) -> None:
        made = themes.acts(Mock(), [_theme("a"), _theme("b")], Mock())

        self.assertEqual([one.one for one in made], [True, False])


class UninstallingSeveralThemes(unittest.IsolatedAsyncioTestCase):
    async def test_the_active_theme_is_left_out(self) -> None:
        made = themes.acts(Mock(), [_theme("a", active=True), _theme("b")], AsyncMock())
        uninstall = next(one for one in made if one.label == t("word.uninstall"))

        with patch.object(themes.confirm, "ask", new=AsyncMock(return_value=True)), \
                patch.object(themes.run, "io_bound", new=AsyncMock()) as removed, \
                patch.object(themes.ui, "notify"):
            assert uninstall.run is not None
            await press(uninstall.run)

        self.assertEqual([call.args[1] for call in removed.await_args_list], ["b"])


class Launchers(unittest.TestCase):
    def _acts(self, chosen: list[dict[str, Any]], count: int) -> list:
        return launchers.acts(Mock(), {}, chosen, count, lambda: None)

    def test_remove_is_refused_when_it_would_take_every_launcher(self) -> None:
        made = self._acts([_launcher("a"), _launcher("b")], 2)
        remove = next(one for one in made if one.label == t("word.remove"))

        self.assertIsNone(remove.run)
        self.assertEqual(remove.hint, t("manager.launchers.keep_one"))

    def test_remove_is_offered_when_one_is_left_over(self) -> None:
        made = self._acts([_launcher("a"), _launcher("b")], 3)
        remove = next(one for one in made if one.label == t("word.remove"))

        self.assertIsNotNone(remove.run)
        self.assertTrue(remove.danger)


class Devices(unittest.TestCase):
    def _entry(self, scope: str, action: str, **more: Any) -> dict[str, Any]:
        return {"scope": scope, "action": action, "label": lifecycle.label(scope, action),
                "available": True, "reason": "", "reason_key": "", **more}

    def _target(self, name: str, *entries: dict[str, Any],
                forgettable: bool = True) -> devices.Target:
        return devices.Target({"device_id": name, "display_name": name}, Mock(),
                              list(entries), forgettable)

    def test_a_verb_shows_when_any_device_offers_it(self) -> None:
        restart = self._entry("vpinfe", "restart")
        stop = self._entry("vpinfe", "stop")

        made = devices.acts(Mock(), [self._target("a", restart),
                                     self._target("b", restart, stop)], None)

        self.assertEqual([one.label for one in made],
                         [t("action.vpinfe.restart"), t("action.vpinfe.stop"),
                          t("word.forget")])

    def test_forget_leaves_out_the_device_that_cannot_be_forgotten(self) -> None:
        made = devices.acts(Mock(), [self._target("a", forgettable=False)], None)

        self.assertEqual(made, [])

    def test_a_verb_every_device_refuses_is_refused_with_the_reason(self) -> None:
        busy = self._entry("vpinfe", "restart", available=False, reason="Updating")

        (verb, _forget) = devices.acts(Mock(), [self._target("a", busy),
                                                self._target("b", busy)], None)

        self.assertIsNone(verb.run)
        self.assertEqual(verb.hint, "Updating")

    def test_a_routed_handler_applies_to_a_single_device_only(self) -> None:
        restart = self._entry("vpinfe", "restart")
        handler = Mock()
        routed: dict[tuple[str, str], Callable[[], Any]] = {
            (lifecycle.VPINFE, lifecycle.RESTART): handler}

        (one,) = devices.acts(Mock(), [self._target("a", restart, forgettable=False)], None,
                              routed=routed)[:1]
        several = devices.acts(Mock(), [self._target("a", restart, forgettable=False),
                                        self._target("b", restart, forgettable=False)], None,
                               routed=routed)[0]

        self.assertIs(one.run, handler)
        self.assertIsNot(several.run, handler)


class SeveralDevices(unittest.IsolatedAsyncioTestCase):
    async def test_each_device_is_asked_once_and_the_question_names_how_many(self) -> None:
        entry = {"scope": "vpinfe", "action": "stop", "available": True, "reason": ""}
        targets = []
        for name in ("a", "b"):
            client = Mock()
            client.perform_action = Mock(return_value={"performed": True})
            targets.append(devices.Target({"device_id": name}, client, [entry], False))
        verb = devices.acts(Mock(), targets, None)[0]
        ask = AsyncMock(return_value=True)

        with patch.object(devices.confirm, "ask", new=ask), \
                patch.object(devices.offload, "io",
                             new=AsyncMock(side_effect=lambda call, *a: call(*a))), \
                patch.object(devices.ui, "notify"):
            assert verb.run is not None
            await press(verb.run)

        ask.assert_awaited_once()
        self.assertEqual(ask.call_args.args[0], t("manager.devices.do_to_many",
                                                  label=verb.label, count=2))
        for target in targets:
            target.client.perform_action.assert_called_once_with("vpinfe", "stop")


class TheLocalDeviceGoesLast(unittest.IsolatedAsyncioTestCase):
    async def test_this_machine_is_asked_after_the_others(self) -> None:
        entry = {"scope": "vpinfe", "action": "stop", "available": True, "reason": ""}
        library = Mock()
        order: list[str] = []
        targets = []
        for name, client in (("here", library), ("there", Mock())):
            client.perform_action = Mock(
                side_effect=lambda *_, name=name: order.append(name) or {"performed": True})  # type: ignore[func-returns-value]
            targets.append(devices.Target({"device_id": name}, client, [entry], False))
        verb = devices.acts(library, targets, None)[0]

        with patch.object(devices.confirm, "ask", new=AsyncMock(return_value=True)), \
                patch.object(devices.offload, "io",
                             new=AsyncMock(side_effect=lambda call, *a: call(*a))), \
                patch.object(devices.ui, "notify"):
            assert verb.run is not None
            await press(verb.run)

        self.assertEqual(order, ["there", "here"])



def _extension(name: str, **record: Any) -> dict[str, Any]:
    return {"name": name, "record": {"display_name": name, "origin": "official",
                                     "state": "loaded", **record},
            "listed": None, "list": "", "checked": False}


class Extensions(unittest.TestCase):
    def _labels(self, chosen: list[dict[str, Any]]) -> list[str]:
        return [one.label for one in extensions.acts(Mock(), chosen, lambda: None)]

    def test_one_installed_from_the_list_is_uninstalled(self) -> None:
        chosen = [_extension("tool", from_list=True)]

        self.assertEqual(self._labels(chosen), [t("word.uninstall")])

    def test_an_update_over_the_one_that_came_with_vpinfe_goes_back_instead(self) -> None:
        chosen = [_extension("tool", from_list=True, comes_with_vpinfe=True)]

        self.assertEqual(self._labels(chosen), [t("manager.extensions.use_shipped")])

    def test_the_one_still_as_shipped_offers_neither(self) -> None:
        chosen = [_extension("tool", bundled=True, comes_with_vpinfe=True)]

        self.assertEqual(self._labels(chosen), [])

    def test_a_mixed_selection_offers_each_to_the_ones_it_applies_to(self) -> None:
        chosen = [_extension("a", from_list=True, comes_with_vpinfe=True),
                  _extension("b", from_list=True)]

        self.assertEqual(self._labels(chosen),
                         [t("manager.extensions.use_shipped"), t("word.uninstall")])


class GoingBackToTheExtensionThatCameWithVPinFE(unittest.IsolatedAsyncioTestCase):
    async def test_it_removes_the_updated_copy_and_says_the_settings_stay(self) -> None:
        library = Mock()
        chosen = [_extension("tool", from_list=True, comes_with_vpinfe=True)]
        asked: list[str] = []

        async def ask(question: str, **kept: Any) -> bool:
            asked.append(kept["detail"])
            return True

        with patch.object(extensions.confirm, "ask", new=ask), \
                patch.object(extensions.run, "io_bound", new=AsyncMock()) as removed, \
                patch.object(extensions.ui, "notify"):
            made = extensions.acts(library, chosen, AsyncMock())
            assert made[0].run is not None
            await press(made[0].run)

        self.assertEqual([call.args for call in removed.await_args_list],
                         [(library.remove_extension, "tool")])
        self.assertEqual(asked, [t("manager.extensions.use_shipped_detail", count=1)])


class TheUpdatesRowOfOneThatCameWithVPinFE(unittest.TestCase):
    def _value(self, entry: dict[str, Any]) -> Any:
        return extensions._updates_value(Mock(), entry, lambda: None, lambda: None)

    def test_it_can_be_checked_like_any_other(self) -> None:
        entry = _extension("tool", bundled=True, comes_with_vpinfe=True)

        self.assertTrue(callable(self._value(entry)))

    def test_it_says_it_is_up_to_date_once_the_list_has_it_with_no_update(self) -> None:
        entry = {**_extension("tool", bundled=True, comes_with_vpinfe=True),
                 "listed": {"name": "tool", "version": "1.0.0"}, "checked": True}

        self.assertEqual(self._value(entry), t("manager.extensions.current"))

    def test_the_trust_line_still_says_it_comes_with_vpinfe_after_an_update(self) -> None:
        entry = _extension("tool", from_list=True, comes_with_vpinfe=True)

        self.assertEqual(extensions._trust_value(entry), t("manager.extensions.trust_shipped"))


if __name__ == "__main__":
    unittest.main()
