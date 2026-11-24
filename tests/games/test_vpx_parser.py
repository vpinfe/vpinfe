import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games.formats.vpx_parser import VPXParser


class TestVPXParser(unittest.TestCase):
    def test_extract_rom_name_ignores_single_quote_commented_cgamename_lines(self) -> None:
        parser = VPXParser()
        values = {
            "game_data": (
                "'Const cGameName = \"mm_10\" 'Williams official rom\n"
                "'Const cGameName = \"mm_109\" 'free play only\n"
                "'Const cGameName=\"mm_109b\" 'unofficial\n"
                "Const cGameName=\"mm_109c\" 'unofficial profanity rom"
            )
        }

        parser.extract_rom_name(values)

        self.assertEqual(values["rom"], "mm_109c")

    def test_extract_rom_name_uses_first_uncommented_cgamename(self) -> None:
        parser = VPXParser()
        values = {
            "game_data": (
                "'Const cGameName = \"commented\"\n"
                "Const cGameName = \"active_first\"\n"
                "Const cGameName = \"active_second\""
            )
        }

        parser.extract_rom_name(values)

        self.assertEqual(values["rom"], "active_first")

    def test_extract_rom_name_preserves_single_quote_inside_string_literals(self) -> None:
        parser = VPXParser()
        values = {
            "game_data": (
                "Dim tableName\n"
                "tableName = \"It' s fine\"\n"
                "Const cGameName = \"quoted_ok\""
            )
        }

        parser.extract_rom_name(values)

        self.assertEqual(values["rom"], "quoted_ok")

    def test_extract_rom_name_falls_back_to_opt_rom_after_commented_cgamename(self) -> None:
        parser = VPXParser()
        values = {
            "game_data": (
                "'Const cGameName = \"commented\"\n"
                "Const cOptRom = \"fallback_rom\""
            )
        }

        parser.extract_rom_name(values)

        self.assertEqual(values["rom"], "fallback_rom")

    def _detect(self, script: str) -> str:
        parser = VPXParser()
        values = {"game_data": script}
        parser.run_detectors(values)
        return values["detect_pinmame"]

    def test_a_script_that_loads_vpm_drives_pinmame(self) -> None:
        self.assertEqual(self._detect(
            'Const cGameName = "afm_113b"\nLoadVPM "01560000", "S11.vbs", 3.10\n'), "true")

    def test_a_hand_rolled_controller_drives_pinmame(self) -> None:
        """Pre-framework tables skip LoadVPM and create the controller directly."""
        self.assertEqual(self._detect(
            'Set Controller = CreateObject ( "VPinMAME.Controller" )\n'), "true")

    def test_a_commented_out_loadvpm_does_not_count(self) -> None:
        """EM tables commonly carry dead VPM code; a comment is not a dependency."""
        self.assertEqual(self._detect(
            "Const cGameName = \"GTB2001_1971\" 'for DOF\n"
            "'LoadVPM \"01560000\", \"sys80.vbs\", 3.10\n"), "false")

    def test_a_dof_only_em_script_does_not_drive_pinmame(self) -> None:
        self.assertEqual(self._detect(
            'Const cGameName = "Aces_and_Kings"\nSub Table1_Init()\nEnd Sub\n'), "false")

    def test_sidecar_vbs_overrides_embedded_game_data(self) -> None:
        parser = VPXParser()
        with TemporaryDirectory() as tmp:
            vpx_path = Path(tmp) / "Example.vpx"
            vbs_path = Path(tmp) / "Example.vbs"
            vpx_path.write_bytes(b"")
            vbs_path.write_text(
                "'Const cGameName=\"embedded_active\"\n"
                "Const cGameName=\"sidecar_active\"\n",
                encoding="utf-8",
            )
            values = {"game_data": "Const cGameName=\"embedded_active\""}

            parser.load_sidecar_vb_code(str(vpx_path), values)
            parser.extract_rom_name(values)

            self.assertEqual(values["rom"], "sidecar_active")


if __name__ == "__main__":
    unittest.main()
