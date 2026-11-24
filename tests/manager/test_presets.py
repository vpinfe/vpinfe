"""Protects: (a) a preset changes only the settings it names and Undo puts back the exact
values that were there, so a launcher's View Mode is never lost."""

from __future__ import annotations

import unittest
from unittest import mock

from apps.vpx import screens as vpx
from manager.settings import presets
from manager.settings.presets import Now


class MatchingTests(unittest.TestCase):
    def test_a_preset_matches_when_both_settings_are_its_own(self) -> None:
        self.assertIs(presets.matching(Now(True, "1", "l")), presets.CABINET)
        self.assertIs(presets.matching(Now(False, "0", "l")), presets.DESKTOP)

    def test_a_desktop_view_mode_that_never_uses_fss_is_still_desktop(self) -> None:
        self.assertIs(presets.matching(Now(False, "2", "l")), presets.DESKTOP)

    def test_a_split_answer_matches_neither(self) -> None:
        self.assertIsNone(presets.matching(Now(True, "0", "l")))
        self.assertIsNone(presets.matching(Now(False, "1", "l")))

    def test_with_no_launcher_only_cabinet_mode_decides(self) -> None:
        self.assertIs(presets.matching(Now(True)), presets.CABINET)
        self.assertIs(presets.matching(Now(False)), presets.DESKTOP)


class ViewNameTests(unittest.TestCase):
    def test_each_view_mode_is_named_in_words_and_not_by_its_key(self) -> None:
        names = [presets.view_name(value) for value in ("0", "1", "2")]

        self.assertEqual(len(set(names)), 3)
        self.assertFalse([one for one in names if "field." in one or "choice" in one])


class ChangesTests(unittest.TestCase):
    def test_only_the_settings_that_differ_are_listed(self) -> None:
        self.assertEqual([one.key for one in presets.changes(Now(False, "0", "l"),
                                                             presets.CABINET)],
                         [presets.CAB_MODE, presets.VIEW_MODE])
        self.assertEqual([one.key for one in presets.changes(Now(True, "0", "l"),
                                                             presets.CABINET)],
                         [presets.VIEW_MODE])

    def test_nothing_is_listed_for_the_preset_already_held(self) -> None:
        self.assertEqual(presets.changes(Now(True, "1", "l"), presets.CABINET), [])

    def test_a_launcher_on_desktop_never_fss_is_not_asked_to_change(self) -> None:
        self.assertEqual([one.key for one in presets.changes(Now(True, "2", "l"),
                                                             presets.DESKTOP)],
                         [presets.CAB_MODE])

    def test_with_no_launcher_there_is_no_view_mode_row(self) -> None:
        self.assertEqual([one.key for one in presets.changes(Now(False), presets.CABINET)],
                         [presets.CAB_MODE])


class WritingTests(unittest.TestCase):
    def _client(self) -> mock.Mock:
        client = mock.Mock()
        patch = mock.patch.object(presets, "ApiClient", return_value=client)
        patch.start()
        self.addCleanup(patch.stop)
        return client

    def test_apply_writes_the_view_mode_only_where_it_differs(self) -> None:
        client = self._client()

        presets.apply(Now(True, "2", "vpx-1"), presets.DESKTOP)

        client.put_config.assert_called_once_with({"presentation": {"cab_mode": False}})
        client.write_launcher_config.assert_not_called()

    def test_apply_writes_both_when_both_differ(self) -> None:
        client = self._client()

        presets.apply(Now(False, "0", "vpx-1"), presets.CABINET)

        client.put_config.assert_called_once_with({"presentation": {"cab_mode": True}})
        client.write_launcher_config.assert_called_once_with(
            "vpx-1", {vpx.VIEW_MODE: "1"}, create=True)

    def test_a_view_mode_that_cannot_be_written_says_cabinet_mode_already_changed(self) -> None:
        client = self._client()
        client.write_launcher_config.side_effect = RuntimeError("read only")

        with self.assertRaises(presets.ViewModeError):
            presets.apply(Now(False, "0", "vpx-1"), presets.CABINET)

        client.put_config.assert_called_once_with({"presentation": {"cab_mode": True}})

    def test_a_cabinet_mode_that_cannot_be_written_raises_as_it_is(self) -> None:
        client = self._client()
        client.put_config.side_effect = RuntimeError("refused")

        with self.assertRaises(RuntimeError) as caught:
            presets.apply(Now(False, "0", "vpx-1"), presets.CABINET)

        self.assertNotIsInstance(caught.exception, presets.ViewModeError)
        client.write_launcher_config.assert_not_called()

    def test_a_launcher_with_no_view_mode_stored_reads_as_desktop(self) -> None:
        client = self._client()
        client.config_values.return_value = {}
        client.config_schema.return_value = {}
        client.launcher_config.return_value = {"values": {}}

        self.assertEqual(presets.read("vpx-1").view, "0")

    def test_a_launcher_that_cannot_be_read_raises(self) -> None:
        client = self._client()
        client.config_values.return_value = {}
        client.config_schema.return_value = {}
        client.launcher_config.side_effect = RuntimeError("unreadable")

        with self.assertRaises(RuntimeError):
            presets.read("vpx-1")

    def test_undo_puts_back_the_view_mode_that_was_there(self) -> None:
        client = self._client()

        presets.restore(Now(True, "2", "vpx-1"), presets.DESKTOP)
        client.put_config.assert_called_once_with({"presentation": {"cab_mode": True}})
        client.write_launcher_config.assert_not_called()

        client.reset_mock()
        presets.restore(Now(False, "2", "vpx-1"), presets.CABINET)
        client.put_config.assert_called_once_with({"presentation": {"cab_mode": False}})
        client.write_launcher_config.assert_called_once_with(
            "vpx-1", {vpx.VIEW_MODE: "2"}, create=True)


if __name__ == "__main__":
    unittest.main()
