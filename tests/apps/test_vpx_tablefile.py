"""The Full Single Screen flag read out of a `.vpx` table's GameData stream."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from apps.vpx.tablefile import fss_enabled


def _record(tag: bytes, data: bytes) -> bytes:
    return (len(tag) + len(data)).to_bytes(4, "little") + tag + data


def _stream(*records: bytes) -> bytes:
    return b"".join(records) + _record(b"ENDB", b"")


class _Ole:
    """Stands in for the compound file, which the library can read but not write."""

    def __init__(self, data: bytes | Exception) -> None:
        self.data = data

    def __call__(self, path: str) -> MagicMock:
        ole = MagicMock()
        ole.__enter__.return_value = ole
        if isinstance(self.data, Exception):
            ole.openstream.side_effect = self.data
        else:
            ole.openstream.return_value.read.return_value = self.data
        return ole


class FssEnabledTests(unittest.TestCase):
    def _read(self, data: bytes | Exception) -> bool | None:
        with patch("apps.vpx.tablefile.olefile.OleFileIO", _Ole(data)):
            return fss_enabled("Example.vpx")

    def test_a_table_that_enables_it_says_so(self) -> None:
        self.assertIs(self._read(_stream(_record(b"NAME", b"x"),
                                         _record(b"EFSS", (1).to_bytes(4, "little")))), True)

    def test_a_table_that_disables_it_says_so(self) -> None:
        self.assertIs(self._read(_stream(_record(b"EFSS", (0).to_bytes(4, "little")))), False)

    def test_a_table_that_never_wrote_it_has_it_off(self) -> None:
        self.assertIs(self._read(_stream(_record(b"NAME", b"x"))), False)

    def test_a_file_that_cannot_be_read_says_nothing(self) -> None:
        self.assertIsNone(self._read(OSError("not an OLE file")))
        self.assertIsNone(self._read(b""))

    def test_a_flag_of_an_unexpected_size_says_nothing(self) -> None:
        self.assertIsNone(self._read(_stream(_record(b"EFSS", b"\x01"))))

    def test_a_record_too_short_to_hold_its_tag_says_nothing(self) -> None:
        self.assertIsNone(self._read((2).to_bytes(4, "little") + b"NAME" + b"\x00" * 4))


if __name__ == "__main__":
    unittest.main()
