from __future__ import annotations

import io
import unittest

from common.uploads.jdiff_patch import (
    BKT,
    DEL,
    EQL,
    ESC,
    INS,
    MOD,
    PatchError,
    _read_int,
    apply_patch,
)


def _apply(source: bytes, patch: bytes) -> bytes:
    out = io.BytesIO()
    apply_patch(io.BytesIO(source), io.BytesIO(patch), out)
    return out.getvalue()


class LengthEncodingTests(unittest.TestCase):
    """Vectors from jojodiff-rs, which is the format's only public specification -
    JojoDiff itself documents the tools, never the file layout."""

    def test_widths(self):
        for raw, expected in (
            (b"\x01", 2),                              # 0..251 -> n + 1
            (bytes([252, 1]), 254),                    # 253 + next
            (bytes([253, 1, 2]), 258),                 # 16-bit
            (bytes([254, 1, 0, 0, 0]), 1 << 24),       # 32-bit
            (bytes([255, 0, 0, 0, 0, 1, 0, 0, 0]), 1 << 24),   # 64-bit
        ):
            self.assertEqual(_read_int(io.BytesIO(raw)), expected, raw.hex())


class OperandTests(unittest.TestCase):
    def test_eql_copies_from_source(self):
        self.assertEqual(_apply(b"ABCDEF", bytes([ESC, EQL, 2])), b"ABC")

    def test_del_skips_source(self):
        patch = bytes([ESC, DEL, 2, ESC, EQL, 2])
        self.assertEqual(_apply(b"ABCDEF", patch), b"DEF")

    def test_bkt_rewinds_source(self):
        patch = bytes([ESC, EQL, 2, ESC, BKT, 2, ESC, EQL, 2])
        self.assertEqual(_apply(b"ABCDEF", patch), b"ABCABC")

    def test_ins_adds_without_consuming_source(self):
        patch = bytes([ESC, INS]) + b"XY" + bytes([ESC, EQL, 2])
        self.assertEqual(_apply(b"ABCDEF", patch), b"XYABC")

    def test_mod_replaces_and_consumes_source(self):
        patch = bytes([ESC, MOD]) + b"XY" + bytes([ESC, EQL, 0])
        self.assertEqual(_apply(b"ABCDEF", patch), b"XYC")

    def test_bare_bytes_are_an_implicit_mod(self):
        """Since JojoDiff 0.8.5 a run of literal data needs no opcode at all."""
        self.assertEqual(_apply(b"ABCDEF", b"XY" + bytes([ESC, EQL, 0])), b"XYC")

    def test_doubled_escape_is_one_literal_escape_byte(self):
        patch = bytes([ESC, INS, ESC, ESC, ESC, EQL, 0])
        self.assertEqual(_apply(b"ABC", patch), bytes([ESC]) + b"A")


class FailureTests(unittest.TestCase):
    def test_wrong_source_is_reported_not_silently_truncated(self):
        """A patch built against a different table must fail loudly - a short copy
        would otherwise yield a plausible-looking, corrupt file."""
        with self.assertRaises(PatchError):
            _apply(b"AB", bytes([ESC, EQL, 200]))


class TableVisibilityTests(unittest.TestCase):
    """A patched table leaves its base in the folder, so a folder can hold tables
    the user does not want offered. Hiding never deletes: the patched table cannot be
    rebuilt without the base."""

    @staticmethod
    def _offered(settings, names):
        from common.games.tables import entry_filename, offered_tables
        return sorted(entry_filename(entry) for _id, entry in offered_tables(settings,
                                                                             listing=names))

    def test_absent_settings_mean_everything_is_visible(self):
        names = ["a.vpx", "b.vpx"]
        self.assertEqual(self._offered(None, names), ["a.vpx", "b.vpx"])
        self.assertEqual(self._offered({}, names), ["a.vpx", "b.vpx"])

    def test_hidden_files_are_not_offered(self):
        from common.games.tables import hidden_tables
        settings = {"base.vpx": {"hidden": True}}
        names = ["base.vpx", "table.vpx", "table (VR).vpx"]
        self.assertEqual(hidden_tables(settings), {"base.vpx"})
        self.assertEqual(self._offered(settings, names), ["table (VR).vpx", "table.vpx"])

    def test_several_visible_tables_are_peers(self):
        """No primary-with-alternates: a VR table and a desktop one are equals."""
        self.assertEqual(len(self._offered({}, ["table.vpx", "table (VR).vpx"])), 2)

    def test_malformed_settings_do_not_hide_anything(self):
        from common.games.tables import hidden_tables
        for bad in (None, [], "nope", {"a.vpx": "yes"}, {"a.vpx": {"hidden": "true"}}):
            self.assertEqual(hidden_tables(bad), set(), repr(bad))


if __name__ == "__main__":
    unittest.main()
