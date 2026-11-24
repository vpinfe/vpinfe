"""The record of the draw order this install wrote into tables' own settings."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from common.games import order_record
from tests.support.library import TempTree

PAIR = {"ScoreView.Priority.B2S": "2", "ScoreView.Priority.Nixie": "1"}
OTHER = {"ScoreView.Priority.B2S": "5", "ScoreView.Priority.Nixie": "1"}


class Recording(TempTree):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(order_record, "PATH", self.root / "state" / "draw_order.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nothing_recorded_holds_nothing(self) -> None:
        self.assertEqual(order_record.held(), {})

    def test_a_pair_kept_is_held_by_its_table_and_survives_a_reread(self) -> None:
        order_record.keep({"t1": PAIR, "t2": OTHER})

        self.assertEqual(order_record.held(), {"t1": PAIR, "t2": OTHER})

    def test_a_pair_kept_again_replaces_the_one_before(self) -> None:
        order_record.keep({"t1": PAIR})

        order_record.keep({"t1": OTHER})

        self.assertEqual(order_record.held(), {"t1": OTHER})

    def test_an_empty_pair_takes_the_table_out_and_leaves_the_others(self) -> None:
        order_record.keep({"t1": PAIR, "t2": OTHER})

        order_record.keep({"t1": {}})

        self.assertEqual(order_record.held(), {"t2": OTHER})

    def test_no_change_writes_no_file(self) -> None:
        order_record.keep({})

        self.assertFalse(order_record.PATH.exists())

    def test_an_unchanged_keep_writes_no_file(self) -> None:
        order_record.keep({"t1": {}})

        self.assertFalse(order_record.PATH.exists())

        order_record.keep({"t1": PAIR})
        before = order_record.PATH.stat().st_mtime_ns
        with patch.object(order_record, "write_atomic") as write:
            order_record.keep({"t1": PAIR})

        write.assert_not_called()
        self.assertEqual(order_record.PATH.stat().st_mtime_ns, before)


class Unreadable(Recording):
    WRONG = (
        "{not json",
        json.dumps({"schema": 99, "tables": {"t1": PAIR}}),
        "[]",
        json.dumps({"schema": 1, "tables": ["x"]}),
        json.dumps({"schema": 1, "tables": {"t1": "abc"}}),
        json.dumps({"schema": 1, "tables": {"t1": 5}}),
        json.dumps({"schema": 1, "tables": {"t1": {"k": 3}}}),
        json.dumps({"schema": 1, "tables": {"t1": {"k": None}}}),
    )

    def test_a_record_that_cannot_be_taken_whole_is_refused_not_read_as_empty(self) -> None:
        order_record.PATH.parent.mkdir(parents=True)
        for text in self.WRONG:
            with self.subTest(text=text):
                order_record.PATH.write_text(text, encoding="utf-8")

                with self.assertRaises(order_record.Unreadable):
                    order_record.held()

    def test_keep_leaves_such_a_file_as_it_was(self) -> None:
        order_record.PATH.parent.mkdir(parents=True)
        for text in self.WRONG:
            with self.subTest(text=text):
                order_record.PATH.write_text(text, encoding="utf-8")

                with self.assertRaises(order_record.Unreadable):
                    order_record.keep({"t2": PAIR})

                self.assertEqual(order_record.PATH.read_text(encoding="utf-8"), text)

    def test_a_read_the_system_refuses_is_unreadable_and_nothing_is_written(self) -> None:
        order_record.keep({"t1": PAIR})
        with patch.object(type(order_record.PATH), "read_text",
                          side_effect=PermissionError("denied")), \
                patch.object(order_record, "write_atomic") as write:
            with self.assertRaises(order_record.Unreadable):
                order_record.keep({"t2": OTHER})

        write.assert_not_called()
        self.assertEqual(order_record.held(), {"t1": PAIR})

    def test_the_refusal_names_the_file(self) -> None:
        order_record.PATH.parent.mkdir(parents=True)
        order_record.PATH.write_text("{not json", encoding="utf-8")

        with self.assertRaises(order_record.Unreadable) as raised:
            order_record.held()

        self.assertIn(str(order_record.PATH), str(raised.exception))


class Failing(Recording):
    def test_a_failed_write_reaches_the_caller_and_leaves_the_record(self) -> None:
        order_record.keep({"t1": PAIR})

        with patch.object(order_record, "write_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                order_record.keep({"t2": OTHER})

        self.assertEqual(order_record.held(), {"t1": PAIR})

    def test_drop_takes_tables_out(self) -> None:
        order_record.keep({"t1": PAIR, "t2": OTHER})

        order_record.drop(["t1", "unknown"])

        self.assertEqual(order_record.held(), {"t2": OTHER})

    def test_drop_says_a_failure_in_the_log_and_does_not_raise(self) -> None:
        order_record.keep({"t1": PAIR})

        with patch.object(order_record, "write_atomic", side_effect=OSError("disk full")):
            with self.assertLogs("vpinfe.common.games.order_record", "ERROR"):
                order_record.drop(["t1"])

        order_record.PATH.write_text("{not json", encoding="utf-8")
        with self.assertLogs("vpinfe.common.games.order_record", "ERROR"):
            order_record.drop(["t1"])


if __name__ == "__main__":
    unittest.main()
