"""The warning that names the tables whose DMD order could not be written."""

from __future__ import annotations

import unittest
from collections.abc import Iterable, Mapping
from typing import Any
from unittest.mock import patch

from common.i18n import t
from manager.kit import order_notice

GONE = [{"table": "t1", "name": "one.vpx", "reason": "read-only"},
        {"table": "t2", "name": "", "reason": "full"}]


class Naming(unittest.TestCase):
    def test_each_table_is_a_line_with_its_file_and_why(self) -> None:
        self.assertEqual(order_notice.lines(GONE), "one.vpx: read-only\nt2: full")

    def test_a_warning_counts_them_and_lists_them(self) -> None:
        with patch.object(order_notice.ui, "notify") as notify:
            order_notice.tell(GONE)

        notify.assert_called_once_with(t("manager.order.failed", count=2),
                                       caption=order_notice.lines(GONE), type="warning",
                                       multi_line=True)

    def test_none_unwritten_says_nothing(self) -> None:
        nothing: list[Iterable[Mapping[str, Any]] | None] = [None, [], ()]
        for failed in nothing:
            with self.subTest(failed=failed), patch.object(order_notice.ui, "notify") as notify:
                order_notice.tell(failed)

                notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
