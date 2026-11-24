import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from apps.vpx.launch import VPXLaunch
from common import apps
from common.apps.contract import SESSION_CHILD_WITH_READINESS, Entry
from common.i18n import t


def _command(table: str, own: Path | None = None, **settings) -> list[str]:
    with mock.patch("apps.vpx.launch.own_file", return_value=own):
        return VPXLaunch().command(Entry(table=table), settings)


class CommandTests(unittest.TestCase):
    def test_another_settings_file_is_passed(self) -> None:
        cmd = _command("/games/example.vpx",
                       bin_path="/opt/vpinball/VPinballX",
                       ini_path="/cfg/plugin_profiles/no-dmd.ini")

        self.assertEqual(cmd, ["/opt/vpinball/VPinballX",
                               "-ini", "/cfg/plugin_profiles/no-dmd.ini",
                               "-play", "/games/example.vpx"])

    def test_an_empty_one_leaves_the_program_to_its_own(self) -> None:
        cmd = _command("/games/example.vpx", bin_path="/opt/vpinball/VPinballX")

        self.assertEqual(cmd, ["/opt/vpinball/VPinballX", "-play", "/games/example.vpx"])

    def test_naming_its_own_passes_nothing(self) -> None:
        """Passing `-ini` for the file Visual Pinball reads anyway changes nothing today,
        and pins the launcher to that version's folder after an upgrade."""
        with TemporaryDirectory() as tmp:
            own = Path(tmp) / "10.8" / "VPinballX.ini"
            own.parent.mkdir()
            own.write_text("[Player]\n", encoding="utf-8")

            cmd = _command("/games/example.vpx", own=own,
                           bin_path="/opt/vpinball/VPinballX", ini_path=str(own))

        self.assertNotIn("-ini", cmd)

    def test_play_is_last(self) -> None:
        cmd = _command("/games/example.vpx",
                       bin_path="/opt/vpinball/VPinballX", ini_path="/cfg/VPinballX.ini")

        self.assertEqual(cmd[-2:], ["-play", "/games/example.vpx"])
        self.assertEqual(cmd.count("-ini"), 1)

    def test_arguments_go_between_the_settings_file_and_the_table(self) -> None:
        with mock.patch("apps.vpx.launch.own_file", return_value=None):
            cmd = VPXLaunch().command(
                Entry(table="/games/example.vpx", names={"rom": "mm_109c", "player": ""}),
                {"bin_path": "/opt/vpinball/VPinballX", "ini_path": "/cfg/VPinballX.ini",
                 "args": '-Minimized -c1 {rom} -Audit "/my logs/a.txt" {player}'})

        self.assertEqual(cmd, ["/opt/vpinball/VPinballX", "-ini", "/cfg/VPinballX.ini",
                               "-Minimized", "-c1", "mm_109c", "-Audit", "/my logs/a.txt",
                               "-play", "/games/example.vpx"])


class ArgumentsRefusedTests(unittest.TestCase):
    """What a save and a launch refuse, so -play stays last and the Settings File is the
    one VPX reads."""

    def test_what_the_command_gives_itself_is_refused_in_any_case(self) -> None:
        for typed, reason in (("-INI /cfg/other.ini", "ini"), ("-Minimized -Play x", "play"),
                              ("/play x", "play")):
            with self.subTest(typed=typed):
                said = apps.arguments_problem("vpx", typed)
                self.assertEqual(said, t(f"app.vpx.field.args.taken.{reason}"))
                self.assertNotIn("field.args.taken", said)

    def test_an_unknown_name_and_an_open_quote_are_refused(self) -> None:
        self.assertEqual(apps.arguments_problem("vpx", "-c1 {exit_code}"),
                         t("capture.command.unknown", token="exit_code"))
        self.assertEqual(apps.arguments_problem("vpx", '-Audit "/my logs'),
                         t("capture.command.quote"))

    def test_options_and_offered_names_are_taken(self) -> None:
        self.assertEqual(apps.arguments_problem("vpx", "-Minimized -c1 {rom} -Primary"), "")
        self.assertEqual(apps.arguments_problem("generic", "-play {table}"), "")

    def test_a_windows_path_keeps_its_backslashes(self) -> None:
        typed = r'-Audit C:\logs\a.txt -c1 "C:\my roms\"'
        with mock.patch("sys.platform", "win32"):
            said = apps.arguments_problem("vpx", typed)
            cmd = _command(r"C:\Tables\example.vpx", bin_path=r"C:\VPX\VPinballX.exe",
                           args=typed)

        self.assertEqual(said, "")
        self.assertEqual(cmd, [r"C:\VPX\VPinballX.exe", "-Audit", r"C:\logs\a.txt",
                               "-c1", "C:\\my roms\\", "-play", r"C:\Tables\example.vpx"])


class SessionTests(unittest.TestCase):
    def test_the_session_waits_for_the_startup_marker(self) -> None:
        """The process exists well before the player is looking at anything."""
        session = VPXLaunch().session({})

        self.assertEqual(session.kind, SESSION_CHILD_WITH_READINESS)
        self.assertEqual(session.readiness_marker, "Startup done")

    def _logs(self, base: Path, settings: dict[str, str]) -> tuple[str, ...]:
        with mock.patch("apps.vpx.config._machine_folders", return_value=(base, None)):
            return VPXLaunch().session(settings).log_files

    def test_it_names_every_version_folder_and_the_one_beside_the_settings_file(self) -> None:
        with TemporaryDirectory() as root:
            base = Path(root) / "VPinballX"
            (base / "10.7").mkdir(parents=True)
            (base / "10.8").mkdir()
            named = self._logs(base, {"ini_path": str(Path(root) / "cfg" / "VPinballX.ini")})

        expected = [str(base / "10.8" / "vpinball.log"), str(base / "10.7" / "vpinball.log"),
                    str(base / "vpinball.log"), str(Path(root) / "cfg" / "vpinball.log")]
        self.assertEqual([one for one in named if one in expected], expected)

    def test_a_blank_settings_file_still_names_the_log_beside_the_programs_own(self) -> None:
        with TemporaryDirectory() as root:
            program = Path(root) / "vpx" / "VPinballX64.exe"
            program.parent.mkdir()
            (program.parent / "VPinballX.ini").write_text("", encoding="utf-8")
            logs = self._logs(Path(root) / "VPinballX", {"bin_path": str(program)})

        self.assertIn(str(program.parent / "vpinball.log"), logs)

    def test_a_log_named_twice_is_named_once(self) -> None:
        with TemporaryDirectory() as root:
            base = Path(root) / "VPinballX"
            (base / "10.8").mkdir(parents=True)
            logs = self._logs(base, {"ini_path": str(base / "10.8" / "VPinballX.ini")})

        self.assertEqual(len(logs), len(set(logs)))
        self.assertEqual(logs.count(str(base / "10.8" / "vpinball.log")), 1)


if __name__ == "__main__":
    unittest.main()
