"""Setup's Visual Pinball step: what the program page offers and gates, what each page
writes, and which of the glass's values are kept, offered or typed over."""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from common.games.launchers import Launcher
from manager.settings import vpx_find
from manager.settings.vpx_find import Find
from manager.setup import setup, setup_vpx

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()

NEW = "/apps/VPinballX_BGFX"
OLD = "/old/VPinballX64.exe"
LAUNCHER = Launcher(launcher_id="vpx-1", app="vpx", display_name="Visual Pinball X",
                    settings={"bin_path": OLD})


def _ran(coro: Any) -> Any:
    return asyncio.run(coro)


def _client(held: dict[str, Any] | None = None) -> mock.Mock:
    client = mock.Mock()
    client.launcher_config.return_value = {"values": held or {}}
    client.config_values.return_value = {"manager": {"units": "cm"}}
    return client


def _one(path: str = NEW, source: str = vpx_find.USUAL, updated: float = 0) -> Find:
    return Find(path, source, updated)


class AskProgramTests(unittest.TestCase):
    def _ask(self, vpx: setup_vpx.Vpx) -> dict[str, Any]:
        with mock.patch.object(setup_vpx, "ApiClient", return_value=_client()):
            return _ran(setup_vpx.ask_program(vpx))

    def _field(self, step: dict[str, Any]) -> dict[str, Any]:
        (field,) = step["fields"]
        return field

    def test_each_find_then_somewhere_else_then_not_installed_yet(self) -> None:
        step = self._ask(setup_vpx.Vpx(finds=[_one(NEW), _one(OLD)]))

        keys = [choice[0] for choice in self._field(step)["choices"]]
        self.assertEqual(["0", "1", setup_vpx.SOMEWHERE_ELSE, setup_vpx.NOT_INSTALLED], keys)

    def test_the_newest_find_is_chosen_and_one_find_says_nothing_of_its_date(self) -> None:
        one = self._field(self._ask(setup_vpx.Vpx(finds=[_one(updated=1.0)])))

        self.assertEqual("0", one["value"])
        self.assertNotIn("updated", one["choices"][0][2])

    def test_several_finds_each_say_when_they_were_updated(self) -> None:
        several = self._field(self._ask(setup_vpx.Vpx(
            finds=[_one(NEW, updated=1_760_000_000), _one(OLD, updated=1_700_000_000)])))

        self.assertEqual("0", several["value"])
        self.assertTrue(all("updated" in choice[2] for choice in several["choices"][:2]))

    def test_nothing_found_says_so_above_the_rows_and_chooses_somewhere_else(self) -> None:
        step = self._ask(setup_vpx.Vpx())

        self.assertIn("before", step)
        self.assertEqual(setup_vpx.SOMEWHERE_ELSE, self._field(step)["value"])

    def test_a_find_leaves_nothing_to_say_above_the_rows(self) -> None:
        self.assertNotIn("before", self._ask(setup_vpx.Vpx(finds=[_one()])))

    def test_the_program_the_launcher_names_is_the_pick_when_it_is_found(self) -> None:
        vpx = setup_vpx.Vpx(finds=[_one(NEW), _one(OLD)], stored=OLD)

        self.assertEqual("1", self._field(self._ask(vpx))["value"])

    def test_the_executable_inside_a_found_bundle_is_that_find(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bundle = pathlib.Path(folder) / "VPinballX_BGFX.app"
            inside = bundle / "Contents" / "MacOS" / "VPinballX_BGFX"
            inside.parent.mkdir(parents=True)
            inside.touch()
            vpx = setup_vpx.Vpx(finds=[_one(str(bundle))], stored=str(inside))

            self.assertEqual("0", self._field(self._ask(vpx))["value"])

    def test_a_program_the_launcher_names_that_is_not_found_is_kept_under_somewhere_else(
            self) -> None:
        vpx = setup_vpx.Vpx(finds=[_one(NEW)], stored="/mine/vpx")

        self.assertEqual(setup_vpx.SOMEWHERE_ELSE, self._field(self._ask(vpx))["value"])

    def test_next_waits_on_somewhere_else_until_a_program_is_given(self) -> None:
        gate = self._ask(setup_vpx.Vpx())["gate"]

        self.assertFalse(gate({setup_vpx.PROGRAM: setup_vpx.SOMEWHERE_ELSE,
                               setup_vpx.SOMEWHERE_ELSE: "  "}))
        self.assertTrue(gate({setup_vpx.PROGRAM: setup_vpx.SOMEWHERE_ELSE,
                              setup_vpx.SOMEWHERE_ELSE: "/mine/vpx",
                              setup_vpx.CHECKED: ("/mine/vpx", "ok")}))
        self.assertTrue(gate({setup_vpx.PROGRAM: setup_vpx.NOT_INSTALLED}))

    def test_next_waits_for_the_path_in_the_field_to_be_the_one_checked_ok(self) -> None:
        gate = self._ask(setup_vpx.Vpx())["gate"]
        chosen = {setup_vpx.PROGRAM: setup_vpx.SOMEWHERE_ELSE}

        # Typed over since the last check, a folder, a path that is not there.
        self.assertFalse(gate({**chosen, setup_vpx.SOMEWHERE_ELSE: "/new",
                               setup_vpx.CHECKED: ("/old", "ok")}))
        self.assertFalse(gate({**chosen, setup_vpx.SOMEWHERE_ELSE: "/dir",
                               setup_vpx.CHECKED: ("/dir", "wrong_kind")}))
        self.assertFalse(gate({**chosen, setup_vpx.SOMEWHERE_ELSE: "/gone"}))

    def test_a_pick_not_yet_made_is_the_preselected_one_for_the_gate(self) -> None:
        self.assertTrue(self._ask(setup_vpx.Vpx(finds=[_one()]))["gate"]({}))
        self.assertFalse(self._ask(setup_vpx.Vpx())["gate"]({}))

    def test_somewhere_else_and_not_installed_yet_each_draw_their_own_detail(self) -> None:
        details = self._field(self._ask(setup_vpx.Vpx()))["details"]

        self.assertEqual({setup_vpx.SOMEWHERE_ELSE, setup_vpx.NOT_INSTALLED}, set(details))

    def test_not_installed_yet_names_where_to_get_it_and_the_preview_and_what_waits(self) -> None:
        values: dict[str, Any] = {}
        with _PAGE, mock.patch.object(setup_vpx.panel, "link_out") as link:
            link.return_value = lambda: None
            holder = ui.element("div")
            with holder:
                setup_vpx._none_detail(values, lambda: None)

        self.assertEqual([setup_vpx.RELEASES, setup_vpx.PREVIEW_BUILDS],
                         [call.kwargs["to"] for call in link.call_args_list])
        self.assertIn("sign in", link.call_args_list[1].kwargs["hint"])
        texts = [one.text for one in holder.descendants() if isinstance(one, ui.label)]
        self.assertEqual(["The rest of this step waits for it, in Launchers › Visual Pinball X"],
                         texts)


class AnswerProgramTests(unittest.TestCase):
    def _answer(self, vpx: setup_vpx.Vpx, values: dict[str, Any], *, cabinet: bool = False,
                held: dict[str, Any] | None = None) -> mock.Mock:
        client = _client(held)
        with mock.patch.object(setup_vpx, "ApiClient", return_value=client):
            _ran(setup_vpx.answer_program(vpx, values, cabinet=cabinet))
        return client

    def test_a_find_becomes_the_launchers_program_with_the_rest_of_it_kept(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(NEW)], stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: "0"})

        launcher_id, body = client.put_launcher.call_args.args
        self.assertEqual("vpx-1", launcher_id)
        self.assertEqual(NEW, body["settings"]["bin_path"])
        self.assertEqual("Visual Pinball X", body["display_name"])
        self.assertEqual(NEW, vpx.chosen)
        self.assertTrue(vpx.answered)

    def test_somewhere_else_takes_the_path_typed(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: setup_vpx.SOMEWHERE_ELSE,
                                    setup_vpx.SOMEWHERE_ELSE: " /mine/vpx "})

        self.assertEqual("/mine/vpx", client.put_launcher.call_args.args[1]["settings"]["bin_path"])

    def test_with_no_launcher_one_is_made(self) -> None:
        vpx = setup_vpx.Vpx(finds=[_one(NEW)])

        client = self._answer(vpx, {setup_vpx.PROGRAM: "0"})

        _launcher_id, body = client.put_launcher.call_args.args
        self.assertEqual("vpx", body["app"])
        self.assertEqual(NEW, body["settings"]["bin_path"])
        self.assertIsNotNone(vpx.launcher)

    def test_the_program_already_named_is_not_put_again(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(OLD)], stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: "0"})

        client.put_launcher.assert_not_called()

    def test_not_installed_yet_writes_nothing_and_leaves_what_was_stored(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: setup_vpx.NOT_INSTALLED}, cabinet=True)

        client.put_launcher.assert_not_called()
        client.write_launcher_config.assert_not_called()
        self.assertEqual(("", OLD, True), (vpx.chosen, vpx.stored, vpx.answered))

    def test_a_cabinet_writes_the_backglass_setting_where_the_ini_holds_another(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(OLD)], stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: "0"}, cabinet=True,
                              held={setup_vpx.BGSET_KEY: {"value": "0"}})

        client.write_launcher_config.assert_called_once_with(
            "vpx-1", {setup_vpx.BGSET_KEY: "1"}, create=True)

    def test_a_never_run_program_has_its_file_made_for_the_setting(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(OLD)], stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: "0"}, cabinet=True, held={})

        self.assertTrue(client.write_launcher_config.call_args.kwargs["create"])

    def test_a_setting_the_ini_already_holds_is_not_written_again(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(OLD)], stored=OLD, chosen=OLD)

        client = self._answer(vpx, {setup_vpx.PROGRAM: "0"}, cabinet=True,
                              held={setup_vpx.BGSET_KEY: {"value": "1"}})

        client.write_launcher_config.assert_not_called()

    def test_an_answer_to_how_you_play_that_stood_leaves_the_backglass_setting_alone(
            self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(OLD)], stored=OLD, chosen=OLD)
        client = _client({setup_vpx.BGSET_KEY: {"value": "1"}})

        with mock.patch.object(setup_vpx, "ApiClient", return_value=client):
            _ran(setup_vpx.answer_program(vpx, {setup_vpx.PROGRAM: "0"}, cabinet=False,
                                          write_bgset=False))

        client.write_launcher_config.assert_not_called()

    def test_a_write_that_fails_does_not_stop_setup(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, finds=[_one(OLD)], stored=OLD, chosen=OLD)
        client = _client()
        client.write_launcher_config.side_effect = RuntimeError("no")

        with mock.patch.object(setup_vpx, "ApiClient", return_value=client), \
             self.assertLogs("vpinfe.manager.setup.setup_vpx", level="WARNING"):
            _ran(setup_vpx.answer_program(vpx, {setup_vpx.PROGRAM: "0"}, cabinet=True))


class WhichPagesApplyTests(unittest.TestCase):
    def test_before_the_program_page_is_answered_the_pages_after_it_are_taken_to_apply(
            self) -> None:
        self.assertTrue(setup_vpx.expects_program(setup_vpx.Vpx()))

    def test_a_program_chosen_keeps_them(self) -> None:
        self.assertTrue(setup_vpx.expects_program(setup_vpx.Vpx(chosen=NEW, answered=True)))

    def test_not_installed_yet_drops_them(self) -> None:
        self.assertFalse(setup_vpx.expects_program(setup_vpx.Vpx(answered=True)))

    def test_the_glass_applies_to_a_cabinet_alone(self) -> None:
        session = setup.Session(again=False, vpx=setup_vpx.Vpx(chosen=NEW, answered=True))
        every = [setup.HOW, setup.APPEARANCE, setup.SCREENS, setup.CONTROLS, setup.VPX, setup.GLASS,
                 setup.VPX_KEYS, setup.TABLES]
        desktop = [one for one in every if one not in (setup.SCREENS, setup.GLASS)]
        for cabinet, keys in ((True, every), (False, desktop)):
            with self.subTest(cabinet=cabinet), \
                 mock.patch.object(setup, "_cabinet", return_value=cabinet):
                self.assertEqual(keys, [one.key for one in setup._applicable(session)])

    def test_three_pages_are_one_name_in_the_row_and_done_when_all_are(self) -> None:
        session = setup.Session(again=False)
        with mock.patch.object(setup, "_cabinet", return_value=True), \
             mock.patch.object(setup.install_setup, "answered",
                               return_value=[setup.HOW, setup.APPEARANCE, setup.SCREENS,
                                             setup.CONTROLS, setup.VPX, setup.GLASS]):
            row = setup._row(session, setup._applicable(session))

        self.assertEqual([setup.HOW, setup.APPEARANCE, setup.SCREENS, setup.CONTROLS, setup.VPX,
                          setup.TABLES],
                         [segment["key"] for segment in row])
        self.assertEqual([setup.VPX, setup.GLASS, setup.VPX_KEYS], row[4]["holds"])
        self.assertEqual([True, True, True, True, False, False],
                         [segment["done"] for segment in row])


class GlassTests(unittest.TestCase):
    """What the glass starts from, and what it writes."""

    def _ask(self, held: dict[str, Any], reported: tuple[float, float] | None = (34.4, 21.6),
             units: str = "cm") -> dict[str, Any]:
        client = _client(held)
        client.config_values.return_value = {"manager": {"units": units}}
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, chosen=OLD, answered=True)
        values: dict[str, Any] = {}
        with mock.patch.object(setup_vpx, "ApiClient", return_value=client), \
             mock.patch.object(setup_vpx, "_reported_cm",
                               mock.AsyncMock(return_value=reported)):
            step = _ran(setup_vpx.ask_glass(vpx, mock.Mock()))
        with _PAGE:
            step["draw"](values)
        return values[setup_vpx.GLASS]

    def test_the_size_comes_from_the_screen_and_the_angle_is_a_typical_cabinets(self) -> None:
        glass = self._ask({})

        self.assertEqual(([34.4, 21.6], setup_vpx.FROM_SCREEN), (glass["size"], glass["size_from"]))
        self.assertEqual((setup_vpx.TYPICAL_ANGLE, setup_vpx.TYPICAL),
                         (glass["angle"], glass["angle_from"]))

    def test_a_screen_that_will_not_say_leaves_the_size_to_be_measured(self) -> None:
        glass = self._ask({}, reported=None)

        self.assertEqual(setup_vpx.MEASURE, glass["size_from"])

    def test_values_the_ini_holds_are_kept_over_the_screens(self) -> None:
        held = {"Player.ScreenWidth": {"value": "50", "set_here": True},
                "Player.ScreenHeight": {"value": "30", "set_here": True},
                setup_vpx.ANGLE_KEY: {"value": "9", "set_here": True}}

        glass = self._ask(held)

        self.assertEqual(([50.0, 30.0], setup_vpx.KEPT, 9.0, setup_vpx.KEPT),
                         (glass["size"], glass["size_from"], glass["angle"],
                          glass["angle_from"]))

    def test_a_blank_or_zero_size_is_not_kept(self) -> None:
        held = {"Player.ScreenWidth": {"value": "", "set_here": True},
                "Player.ScreenHeight": {"value": "0", "set_here": True},
                setup_vpx.ANGLE_KEY: {"value": "", "set_here": True}}

        glass = self._ask(held)

        self.assertEqual((setup_vpx.FROM_SCREEN, setup_vpx.TYPICAL),
                         (glass["size_from"], glass["angle_from"]))

    def test_half_a_size_is_not_a_size_kept(self) -> None:
        held = {"Player.ScreenWidth": {"value": "50", "set_here": True}}

        self.assertEqual(setup_vpx.FROM_SCREEN, self._ask(held)["size_from"])

    def test_the_units_are_the_managers(self) -> None:
        self.assertEqual("in", self._ask({}, units="in")["units"])


class UnreadablePagesTests(unittest.TestCase):
    """A page that cannot read the settings says so and lets Setup go on."""

    def _client(self) -> mock.Mock:
        client = _client()
        client.launcher_config.side_effect = RuntimeError("gone")
        return client

    def test_the_glass_says_so_when_the_settings_cannot_be_read(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, chosen=OLD, answered=True)
        with mock.patch.object(setup_vpx, "ApiClient", return_value=self._client()), \
             self.assertLogs("vpinfe.manager.setup.setup_vpx", level="WARNING"):
            step = _ran(setup_vpx.ask_glass(vpx, mock.Mock()))

        self.assertEqual(1, len(step["notes"]))
        self.assertNotIn("draw", step)

    def test_the_buttons_say_so_when_the_settings_cannot_be_read(self) -> None:
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, chosen=OLD, answered=True)
        with mock.patch.object(setup_vpx, "ApiClient", return_value=self._client()), \
             self.assertLogs("vpinfe.manager.setup.setup_vpx", level="WARNING"):
            step = _ran(setup_vpx.ask_keys(vpx))

        self.assertEqual(1, len(step["notes"]))
        self.assertNotIn("draw", step)

    def test_a_deeplink_with_no_launcher_yet_says_so_instead_of_failing(self) -> None:
        vpx = setup_vpx.Vpx()

        self.assertEqual(1, len(_ran(setup_vpx.ask_keys(vpx))["notes"]))
        self.assertEqual(1, len(_ran(setup_vpx.ask_glass(vpx, mock.Mock()))["notes"]))


class AnswerGlassTests(unittest.TestCase):
    def _answer(self, glass: dict[str, Any], held: dict[str, Any]) -> mock.Mock:
        client = _client(held)
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, chosen=OLD, answered=True)
        with mock.patch.object(setup_vpx, "ApiClient", return_value=client):
            _ran(setup_vpx.answer_glass(vpx, {setup_vpx.GLASS: glass}))
        return client

    def test_the_size_and_angle_are_written_where_the_ini_holds_something_else(self) -> None:
        client = self._answer({"size": [34.4, 21.6], "angle": 6.0}, {})

        client.write_launcher_config.assert_called_once_with(
            "vpx-1", {"Player.ScreenWidth": "34.4", "Player.ScreenHeight": "21.6",
                      setup_vpx.ANGLE_KEY: "6"}, create=True)

    def test_a_value_the_ini_already_holds_is_left_alone(self) -> None:
        held = {"Player.ScreenWidth": {"value": "34.4"}, "Player.ScreenHeight": {"value": "20"},
                setup_vpx.ANGLE_KEY: {"value": "6"}}

        client = self._answer({"size": [34.4, 21.6], "angle": 6.0}, held)

        client.write_launcher_config.assert_called_once_with(
            "vpx-1", {"Player.ScreenHeight": "21.6"}, create=True)

    def test_nothing_changed_writes_nothing(self) -> None:
        held = {"Player.ScreenWidth": {"value": "34.4"}, "Player.ScreenHeight": {"value": "21.6"},
                setup_vpx.ANGLE_KEY: {"value": "6"}}

        client = self._answer({"size": [34.4, 21.6], "angle": 6.0}, held)

        client.write_launcher_config.assert_not_called()

    def test_a_write_that_fails_tells_the_person_and_lets_setup_go_on(self) -> None:
        client = _client()
        client.write_launcher_config.side_effect = RuntimeError("read only")
        vpx = setup_vpx.Vpx(launcher=LAUNCHER, chosen=OLD, answered=True)

        with mock.patch.object(setup_vpx, "ApiClient", return_value=client), \
             mock.patch.object(setup_vpx.ui, "notify") as notify, \
             self.assertLogs("vpinfe.manager.setup.setup_vpx", level="WARNING"):
            _ran(setup_vpx.answer_glass(
                vpx, {setup_vpx.GLASS: {"size": [34.4, 21.6], "angle": 6.0}}))

        notify.assert_called_once()

    def test_a_walk_that_never_drew_the_glass_writes_nothing(self) -> None:
        client = _client()
        vpx = setup_vpx.Vpx(launcher=LAUNCHER)
        with mock.patch.object(setup_vpx, "ApiClient", return_value=client):
            _ran(setup_vpx.answer_glass(vpx, {}))

        client.write_launcher_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
