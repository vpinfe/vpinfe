"""The window on each screen that says its number, for the Manager's Show numbers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock
from urllib.parse import unquote

from common.capture import adapters, geometry
from frontend import screen_numbers

ONE = SimpleNamespace(number=1, name="Dell P2419H", width=2560, height=1440, x=0, y=0)
TWO = SimpleNamespace(number=2, name="", width=1280, height=390, x=0, y=1440)


class PageTests(unittest.TestCase):
    def test_it_says_the_number_the_name_and_the_size(self) -> None:
        page = screen_numbers.page(1, "Dell P2419H", "2560 x 1440")

        for word in ('class="number">1<', "Dell P2419H", "2560 x 1440"):
            self.assertIn(word, page)

    def test_a_screen_with_no_name_has_no_name_line(self) -> None:
        self.assertNotIn('class="name"', screen_numbers.page(2, "", "1280 x 390"))

    def test_a_name_is_text_and_not_markup(self) -> None:
        page = screen_numbers.page(1, "<script>x()</script>", "1 x 1")

        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_it_draws_no_arrow(self) -> None:
        self.assertNotIn("<svg", screen_numbers.page(1, "", "1 x 1"))


class AddressTests(unittest.TestCase):
    def test_it_is_the_page_itself_as_a_data_address(self) -> None:
        address = screen_numbers.address(3, "A & B", "1 x 1")

        self.assertTrue(address.startswith("data:text/html;charset=utf-8,"))
        self.assertEqual(unquote(address.split(",", 1)[1]),
                         screen_numbers.page(3, "A & B", "1 x 1"))

    def test_it_holds_nothing_that_ends_an_address_early(self) -> None:
        address = screen_numbers.address(1, "", "1 x 1")

        self.assertNotIn("#", address)
        self.assertNotIn(" ", address)


def _window(exit_code: int | None = None, pid: int = 4242) -> mock.Mock:
    """A Chromium process: still running, or gone with `exit_code`."""
    return mock.Mock(pid=pid, returncode=exit_code, **{"poll.return_value": exit_code})


def _output(name: str, x: int, y: int, width: int = 1920, height: int = 1080) -> Any:
    return adapters.Output(name, x, y, width, height, (width, height), 60.0,
                           geometry.sway_transform("normal"))


class ShowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = self.enterContext(mock.patch.object(screen_numbers, "ChromiumManager"))
        self.enterContext(mock.patch.object(screen_numbers.paths, "get_ini_config"))
        self.enterContext(mock.patch.object(
            screen_numbers.SettingsConfig, "from_config",
            return_value=SimpleNamespace(chrome_options="--x",
                                         default_chrome_options=True)))
        self.enterContext(mock.patch.object(screen_numbers, "cfg_get", return_value=""))
        self.sleep = self.enterContext(mock.patch.object(screen_numbers.time, "sleep"))
        self.enterContext(mock.patch.object(screen_numbers, "window_commands", return_value=None))
        self.launch = self.manager.return_value.launch_window
        self.launch.return_value = _window()

    def test_each_screen_gets_a_window_of_its_own_on_it(self) -> None:
        screen_numbers.show([ONE, TWO])

        [first, second] = self.launch.call_args_list
        self.assertEqual((first.args[0], first.args[2], first.args[3]), ("screen1", ONE, 1))
        self.assertEqual((second.args[0], second.args[2], second.args[3]), ("screen2", TWO, 2))
        self.assertIn("Dell P2419H", unquote(first.args[1]))
        self.assertIn("1280 x 390", unquote(second.args[1]))

    def test_the_windows_are_opened_with_the_chromium_options_the_frontend_uses(self) -> None:
        screen_numbers.show([ONE])

        kwargs = self.launch.call_args.kwargs
        self.assertEqual(kwargs["additional_options"], "--x")
        self.assertTrue(kwargs["include_default_options"])
        self.assertTrue(kwargs["mute_audio"])

    def test_they_stay_for_the_time_given_and_are_then_all_closed(self) -> None:
        screen_numbers.show([ONE, TWO], seconds=3)

        self.sleep.assert_called_once_with(3)
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_one_window_that_will_not_open_leaves_the_others(self) -> None:
        self.launch.side_effect = [RuntimeError("no"), _window()]

        with self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE, TWO])

        self.sleep.assert_called_once()
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_no_window_opening_raises_the_reason_and_closes_what_it_started(self) -> None:
        self.launch.side_effect = FileNotFoundError("Chromium binary not found")

        with self.assertRaises(FileNotFoundError), \
                self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE, TWO])

        self.sleep.assert_not_called()
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_it_can_be_shown_again_once_it_is_over(self) -> None:
        self.launch.side_effect = FileNotFoundError("gone")
        with self.assertRaises(FileNotFoundError), \
                self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE])
        self.launch.side_effect = None

        screen_numbers.show([ONE])

        self.assertEqual(self.sleep.call_count, 1)

    def test_a_manager_that_cannot_be_made_does_not_leave_it_showing_forever(self) -> None:
        self.manager.side_effect = RuntimeError("no browser")
        with self.assertRaises(RuntimeError):
            screen_numbers.show([ONE])
        self.manager.side_effect = None

        screen_numbers.show([ONE])

        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(self.sleep.call_count, 1)

    def test_the_size_is_said_in_the_catalogs_words(self) -> None:
        with mock.patch.object(screen_numbers, "t", return_value="W by H") as said:
            screen_numbers.show([ONE])

        said.assert_called_once_with("manager.screens.size", width=2560, height=1440)
        self.assertIn("W by H", unquote(self.launch.call_args.args[1]))

    def test_a_second_call_while_one_is_showing_opens_nothing(self) -> None:
        asked: list[int] = []

        def ask_again(_seconds: float) -> None:
            before = self.launch.call_count
            screen_numbers.show([ONE])
            asked.append(self.launch.call_count - before)

        self.sleep.side_effect = ask_again

        screen_numbers.show([ONE])

        self.assertEqual(asked, [0])
        self.assertEqual(self.manager.return_value.terminate_all.call_count, 1)


class PlacementTests(unittest.TestCase):
    """On Sway and Hyprland the compositor, not Chromium, puts a window on its output."""

    def setUp(self) -> None:
        self.manager = self.enterContext(mock.patch.object(screen_numbers, "ChromiumManager"))
        self.enterContext(mock.patch.object(screen_numbers.paths, "get_ini_config"))
        self.enterContext(mock.patch.object(
            screen_numbers.SettingsConfig, "from_config",
            return_value=SimpleNamespace(chrome_options="", default_chrome_options=True)))
        self.enterContext(mock.patch.object(screen_numbers, "cfg_get", return_value=""))
        self.sleep = self.enterContext(mock.patch.object(screen_numbers.time, "sleep"))
        self.compositor = mock.Mock()
        self.compositor.outputs.return_value = [_output("DP-1", 0, 1440, 1280, 390),
                                                _output("HDMI-A-1", 0, 0, 2560, 1440)]
        self.compositor.view_of.side_effect = lambda pid: f"view{pid}"
        self.compositor.focused_view.return_value = "table"
        self.enterContext(mock.patch.object(screen_numbers, "window_commands",
                                            return_value=self.compositor))
        self.launch = self.manager.return_value.launch_window
        self.launch.side_effect = [_window(pid=1), _window(pid=2)]

    def test_each_window_is_sent_to_the_output_its_screen_is_full_screen(self) -> None:
        screen_numbers.show([ONE, TWO])

        [first, second] = self.compositor.fill_output.call_args_list
        self.assertEqual((first.args[0], first.args[1].name), ("view1", "HDMI-A-1"))
        self.assertEqual((second.args[0], second.args[1].name), ("view2", "DP-1"))

    def test_the_commands_wait_for_chromium_to_finish_starting(self) -> None:
        order: list[str] = []
        self.sleep.side_effect = lambda seconds: order.append(f"sleep {seconds}")
        self.compositor.fill_output.side_effect = lambda *_: order.append("place")

        screen_numbers.show([ONE], seconds=8)

        self.assertEqual(order[:2], [f"sleep {screen_numbers.PLACE_SETTLE}", "place"])
        self.assertEqual(order[-1], "sleep 8")

    def test_focus_goes_back_once_the_windows_are_closed_and_not_before(self) -> None:
        calls = mock.Mock()
        calls.attach_mock(self.manager.return_value.terminate_all, "closed")
        calls.attach_mock(self.compositor.focus_view, "focused")

        screen_numbers.show([ONE])

        self.assertEqual([call[0] for call in calls.mock_calls], ["closed", "focused"])
        self.compositor.focus_view.assert_called_once_with("table")

    def test_no_focus_is_given_where_nothing_had_it(self) -> None:
        self.compositor.focused_view.return_value = None

        screen_numbers.show([ONE])

        self.compositor.focus_view.assert_not_called()

    def test_a_window_that_closes_while_placed_is_opened_again_and_placed(self) -> None:
        self.launch.side_effect = [_window(-6, pid=1), _window(pid=2), _window(pid=3)]

        with self.assertNoLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE, TWO])

        self.assertEqual([call.args[2] for call in self.launch.call_args_list],
                         [ONE, TWO, ONE])
        placed = [call.args[0] for call in self.compositor.fill_output.call_args_list]
        self.assertEqual(placed, ["view2", "view3"])

    def test_a_window_that_will_not_stay_open_is_given_up_on_and_said(self) -> None:
        self.launch.side_effect = lambda *args, **kwargs: _window(-6)

        with self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING") as logged:
            screen_numbers.show([ONE])

        self.assertEqual(self.launch.call_count, screen_numbers.PLACE_ATTEMPTS)
        self.assertEqual(len(logged.records), 1)
        self.assertIn("closed early (exit -6)", logged.output[0])
        self.manager.return_value.terminate_all.assert_called_once_with()

    def test_what_chromium_said_goes_to_the_debug_log(self) -> None:
        def launch(*args: Any, stderr: Any, **kwargs: Any) -> mock.Mock:
            stderr.write(b"FATAL: Check failed")
            return _window(-6)

        self.launch.side_effect = launch

        with self.assertLogs("vpinfe.frontend.screen_numbers", "DEBUG") as logged:
            screen_numbers.show([ONE])

        self.assertTrue(any("FATAL: Check failed" in line for line in logged.output))

    def test_a_screen_no_output_is_at_gets_no_command_and_is_said(self) -> None:
        elsewhere = SimpleNamespace(number=3, name="", width=800, height=600, x=9000, y=0)
        self.launch.side_effect = [_window(pid=1)]

        with self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING") as logged:
            screen_numbers.show([elsewhere])

        self.assertIn("screen 3", logged.output[0])
        self.compositor.fill_output.assert_not_called()

    def test_one_window_the_compositor_refuses_leaves_the_others_placed(self) -> None:
        self.compositor.fill_output.side_effect = [OSError("no"), None]

        with self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE, TWO])

        self.assertEqual(self.compositor.fill_output.call_count, 2)
        self.sleep.assert_called()

    def test_a_compositor_that_will_not_say_its_outputs_still_shows_the_windows(self) -> None:
        self.compositor.outputs.side_effect = OSError("gone")

        with self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE, TWO], seconds=5)

        self.compositor.fill_output.assert_not_called()
        self.sleep.assert_called_with(5)

    def test_a_window_that_never_appears_is_said_and_not_waited_on_forever(self) -> None:
        self.compositor.view_of.side_effect = None
        self.compositor.view_of.return_value = None
        self.launch.side_effect = [_window(pid=1)]

        with mock.patch.object(screen_numbers, "PLACE_WAIT", 0), \
                self.assertLogs("vpinfe.frontend.screen_numbers", "WARNING") as logged:
            screen_numbers.show([ONE])

        self.assertIn("no window", logged.output[0])
        self.compositor.fill_output.assert_not_called()

    def test_a_window_that_goes_after_the_command_reaches_it_is_opened_again(self) -> None:
        first = _window(pid=1)
        first.poll.return_value = None

        def fill(view: str, output: Any) -> None:
            if view == "view1":
                first.poll.return_value = first.returncode = -6

        self.compositor.fill_output.side_effect = fill
        self.launch.side_effect = [first, _window(pid=2)]

        with self.assertNoLogs("vpinfe.frontend.screen_numbers", "WARNING"):
            screen_numbers.show([ONE])

        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(self.compositor.fill_output.call_args_list[-1].args[0], "view2")

    def test_every_error_file_is_closed_however_it_ends(self) -> None:
        files: list[Any] = []

        def launch(*args: Any, stderr: Any, **kwargs: Any) -> mock.Mock:
            files.append(stderr)
            return _window(-6 if len(files) == 1 else None, pid=len(files))

        self.launch.side_effect = launch

        screen_numbers.show([ONE])

        self.assertGreater(len(files), 1)
        self.assertTrue(all(one.closed for one in files))

    def test_an_error_while_placing_is_the_one_raised_and_closes_every_file(self) -> None:
        files: list[Any] = []

        def launch(*args: Any, stderr: Any, **kwargs: Any) -> mock.Mock:
            files.append(stderr)
            return _window(pid=len(files))

        self.launch.side_effect = launch
        self.compositor.fill_output.side_effect = KeyError("id")

        with self.assertRaises(KeyError):
            screen_numbers.show([ONE, TWO])

        self.assertEqual(len(files), 2)
        self.assertTrue(all(one.closed for one in files))


if __name__ == "__main__":
    unittest.main()
