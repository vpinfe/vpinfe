"""What a launcher's row says about whether it can run a table.

One column rather than two, because a launcher that is switched off and a launcher whose
program is gone are answers to the same question and a reader should not have to combine
them.
"""

from __future__ import annotations

import unittest

from common.config import path_state
from manager.frontend import launchers


def _launcher(*, enabled: bool = True, **checks) -> dict:
    return {
        "launcher_id": "l1",
        "display_name": "Visual Pinball X",
        "app": "vpx",
        "app_name": "Visual Pinball X",
        "enabled": enabled,
        "settings": {"bin_path": "/opt/VPinballX"},
        "fields": [{"key": "bin_path", "label": "Program", "path": "exe"},
                   {"key": "ini_path", "label": "Configuration File", "path": "file"}],
        "checks": {key: {"state": state, "reason": reason}
                   for key, (state, reason) in checks.items()},
    }


class StateTests(unittest.TestCase):
    def test_a_launcher_that_works_is_ready(self) -> None:
        self.assertEqual(launchers.state_of(_launcher(bin_path=(path_state.OK, ""))),
                         launchers.STATE_READY)

    def test_an_unset_optional_path_is_not_a_fault(self) -> None:
        """Blank means "use the one the program finds itself"."""
        self.assertEqual(
            launchers.state_of(_launcher(bin_path=(path_state.OK, ""),
                                         ini_path=(path_state.UNSET, ""))),
            launchers.STATE_READY)

    def test_a_launcher_naming_no_program_says_so(self) -> None:
        self.assertEqual(
            launchers.state_of(_launcher(bin_path=(path_state.UNSET, ""),
                                         ini_path=(path_state.UNSET, ""))),
            launchers.STATE_NO_PROGRAM)

    def test_even_switched_off(self) -> None:
        self.assertEqual(
            launchers.state_of(_launcher(enabled=False, bin_path=(path_state.UNSET, ""))),
            launchers.STATE_NO_PROGRAM)

    def test_a_program_not_at_its_path_is_missing(self) -> None:
        self.assertEqual(
            launchers.state_of(_launcher(bin_path=(path_state.MISSING, "Not there"))),
            launchers.STATE_MISSING)

    def test_a_settings_file_not_there_is_not_the_rows_state(self) -> None:
        """Said at its field. The row's state is whether the program can start."""
        self.assertEqual(
            launchers.state_of(_launcher(bin_path=(path_state.OK, ""),
                                         ini_path=(path_state.MISSING, "Not there"))),
            launchers.STATE_READY)

    def test_a_switched_off_launcher_says_so(self) -> None:
        self.assertEqual(
            launchers.state_of(_launcher(enabled=False, bin_path=(path_state.OK, ""))),
            launchers.STATE_OFF)

    def test_a_missing_program_outranks_being_switched_off(self) -> None:
        """Switched off is a choice somebody made."""
        self.assertEqual(
            launchers.state_of(_launcher(enabled=False,
                                         bin_path=(path_state.MISSING, "Not there"))),
            launchers.STATE_MISSING)


class RowTests(unittest.TestCase):
    def test_a_ready_launcher_holds_the_ready_token(self) -> None:
        rows = launchers.rows([_launcher(bin_path=(path_state.OK, ""))], {})

        self.assertEqual(rows[0]["state"], launchers.STATE_READY)

    def test_an_exception_holds_its_token_and_the_column_says_the_word(self) -> None:
        rows = launchers.rows([_launcher(bin_path=(path_state.UNSET, ""))], {})

        self.assertEqual(rows[0]["state"], launchers.STATE_NO_PROGRAM)
        self.assertEqual(launchers.STATES.word(rows[0]["state"]), "No Program")

    def test_a_row_counts_the_tables_it_plays(self) -> None:
        rows = launchers.rows([_launcher(bin_path=(path_state.OK, ""))], {}, {"l1": 12})

        self.assertEqual(rows[0]["tables"], 12)

    def test_and_none_where_nothing_is_counted(self) -> None:
        rows = launchers.rows([_launcher(bin_path=(path_state.OK, ""))], {})

        self.assertEqual(rows[0]["tables"], 0)

    def test_the_default_is_marked_on_the_one_it_applies_to(self) -> None:
        """A column that says the same thing on every row but one is a column about the
        exception."""
        rows = launchers.rows([_launcher(bin_path=(path_state.OK, ""))],
                              {"vpx": "l1"})

        self.assertEqual(rows[0]["default"], "Default")

    def test_and_is_blank_everywhere_else(self) -> None:
        rows = launchers.rows([_launcher(bin_path=(path_state.OK, ""))], {})

        self.assertEqual(rows[0]["default"], "")

    def test_a_row_names_the_program_it_runs(self) -> None:
        rows = launchers.rows([_launcher(bin_path=(path_state.OK, ""))], {})

        self.assertEqual(rows[0]["program"], "/opt/VPinballX")


if __name__ == "__main__":
    unittest.main()
