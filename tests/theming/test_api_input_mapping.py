import configparser
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from common import events, i18n
from frontend import play_events
from frontend.api import API


class TestApiInputMapping(unittest.TestCase):
    def _build_ini(self):
        parser = configparser.ConfigParser()
        parser.add_section("Input")
        parser.set("Input", "joyleft", "1")
        parser.set("Input", "keyleft", "ArrowLeft,ShiftLeft")
        parser.set("Input", "joyright", "2")
        parser.set("Input", "keyright", "ArrowRight,ShiftRight")
        parser.set("Input", "joyup", "3")
        parser.set("Input", "keyup", "ArrowUp")
        parser.set("Input", "joydown", "4")
        parser.set("Input", "keydown", "ArrowDown")
        parser.set("Input", "joyselect", "5")
        parser.set("Input", "keyselect", "Enter")
        parser.set("Input", "joymenu", "6")
        parser.set("Input", "keymenu", "m")
        parser.set("Input", "joyback", "7")
        parser.set("Input", "keyback", "b")
        parser.set("Input", "joytutorial", "8")
        parser.set("Input", "keytutorial", "t")
        parser.set("Input", "joyexit", "9")
        parser.set("Input", "keyexit", "Escape,q")
        parser.set("Input", "joycollectionmenu", "10")
        parser.set("Input", "keycollectionmenu", "c")
        parser.add_section("general")
        parser.set("general", "startup_collection", "")

        class DummyIni:
            def __init__(self, config):
                self.config = config
                self.saved = False

            def save(self):
                self.saved = True

        return DummyIni(parser)

    @patch("frontend.api.all_games", return_value=[])
    def test_get_joymaping_includes_joytutorial(self, _mock_games) -> None:
        ini = self._build_ini()
        api = API(ini)

        mapping = api.get_joymaping()

        self.assertEqual(mapping["joytutorial"], "8")

    @patch("frontend.api.all_games", return_value=[])
    def test_set_button_mapping_accepts_joytutorial(self, _mock_games) -> None:
        ini = self._build_ini()
        api = API(ini)

        result = api.set_button_mapping("joytutorial", 15)

        self.assertTrue(result["success"])
        # Written as a binding on the action, not back into the key it came from: the
        # old [Input] key is what an unmigrated file holds, never what we write.
        self.assertEqual(ini.config.get("input", "tutorial"), "key:t,pad:0/button:15")
        self.assertTrue(ini.saved)
        self.assertTrue(ini.saved)

    @patch("frontend.api.all_games", return_value=[])
    def test_its_answer_is_in_the_catalogs_words(self, _mock_games) -> None:
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")
        api = API(self._build_ini())

        self.assertEqual(api.set_button_mapping("joytutorial", 15)["message"],
                         i18n.t("frontend.input_api.mapped",
                                action=i18n.t("input.tutorial.label"), button=15))
        self.assertEqual(api.set_button_mapping("joynothing", 15)["message"],
                         i18n.t("frontend.input_api.no_such_action", action="joynothing"))

    @patch("frontend.api.all_games", return_value=[])
    def test_get_keymapping_includes_keytutorial(self, _mock_games) -> None:
        ini = self._build_ini()
        api = API(ini)

        mapping = api.get_keymapping()

        self.assertEqual(mapping["keytutorial"], "t")

    @patch("common.host.launch.subprocess.Popen")
    @patch("common.host.launch._log_files_of", return_value=())
    @patch("common.host.launch._plan",
           return_value=(["/tmp/fake-launcher", "-play", "/tmp/table.vpx"],
                         "Startup done"))
    @patch("common.host.launch._binary_of", return_value="/tmp/fake-launcher")
    @patch("common.host.launch._launcher_for")
    @patch("frontend.api.all_games")
    def test_launch_game_emits_launching_and_complete_events(
        self,
        mock_games,
        mock_launcher_for,
        _mock_binary,
        _mock_plan,
        _mock_log_files,
        mock_popen,
    ) -> None:
        """The wheel launches through the shared service; the windows hear about it
        as subscribers rather than from the launch itself."""
        with TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "VPinballX"
            launcher.write_text("", encoding="utf-8")
            vpx_path = Path(tmp) / "Example.vpx"
            vpx_path.write_text("", encoding="utf-8")

            game = types.SimpleNamespace(
                full_path_vpx_file=str(vpx_path),
                meta_config={},
                game_dir_name="Example",
                full_path_game=str(Path(tmp)),
                location_id="",
            )
            mock_games.return_value = [game]
            from common.games.launchers import Launcher
            mock_launcher_for.return_value = (
                Launcher(launcher_id="l1", app="vpx", display_name="Visual Pinball X",
                         settings={"bin_path": str(launcher)}), "")

            process = types.SimpleNamespace(stdout=[], wait=lambda: 0, returncode=0, pid=4242)
            mock_popen.return_value = process

            window_messages = []
            call_order = []
            ws_bridge = types.SimpleNamespace(
                send_event_all_with_iframe=lambda message: window_messages.append(message)
            )

            ini = self._build_ini()
            api = API(ini, ws_bridge=ws_bridge)  # type: ignore[arg-type]

            def popen_side_effect(*args, **kwargs):
                call_order.append("popen")
                return process

            mock_popen.side_effect = popen_side_effect

            play_events.reset_for_tests()
            self.addCleanup(play_events.reset_for_tests)
            self.addCleanup(events.clear)
            service: dict[str, Any] = {"parse_score_from_nvram.return_value": (None, None)}
            with (
                patch("common.host.launch.delete_vpinball_log_on_start_if_configured",
                      side_effect=lambda *_a: call_order.append("delete_log")),
                patch("common.host.launch.game_play_service", **service),
                patch("frontend.play_events.save_last_launched"),
            ):
                play_events.register(ws_bridge)  # type: ignore[arg-type]
                api.launch_table(0)

            self.assertEqual(call_order[:2], ["delete_log", "popen"])
            self.assertEqual(window_messages[0]["type"], "TableLaunching")
            # The refresh that carries the finished session's play data comes after.
            self.assertEqual([message["type"] for message in window_messages[-2:]],
                             ["TableLaunchComplete", "TableDataChange"])
