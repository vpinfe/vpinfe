"""Reading a game's text file and writing it back as it was found.

A script is cp1252 with CRLF line ends when Windows wrote it, and a save that rewrote either
would show a one-line edit as the whole file changed. What is pinned: the bytes a save leaves,
what refuses it, and that a copy of the file as it was is kept first.
"""

from __future__ import annotations

import hashlib
import pathlib
from unittest.mock import patch
from urllib.parse import quote

from common import service_errors
from common.backups import copies
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.games import library_folders, text_file
from tests.api.test_asset_copies import GAME, NAME, SCRIPT, AssetCase

ROUTE = f"/games/{GAME}/assets/text"


def version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TextCase(AssetCase):
    def write(self, data: bytes, name: str = SCRIPT) -> pathlib.Path:
        target = self.folder / name
        target.write_bytes(data)
        return target

    def get(self, name: str = SCRIPT):
        return self.client.get(f"{ROUTE}?path={quote(name)}")

    def put(self, text: str, base: bytes, name: str = SCRIPT, **extra):
        return self.client.put(f"{ROUTE}?path={quote(name)}",
                               json={"text": text, "version": version(base), **extra})


class ReadTests(TextCase):
    def test_the_text_comes_with_the_facts_a_save_writes_back(self) -> None:
        self.write(b"Option Explicit\r\nDim a\r\n")

        found = self.get().json()

        self.assertEqual(found["text"], "Option Explicit\nDim a\n")
        self.assertEqual((found["encoding"], found["newline"], found["final_newline"]),
                         ("utf-8", "crlf", True))
        self.assertEqual((found["editable"], found["why"]), (True, ""))
        self.assertEqual(found["version"], version(b"Option Explicit\r\nDim a\r\n"))

    def test_a_bom_is_a_utf8_file_with_a_bom(self) -> None:
        self.write(b"\xef\xbb\xbfx = 1\n")

        found = self.get().json()

        self.assertEqual((found["text"], found["encoding"], found["newline"]),
                         ("x = 1\n", "utf-8-sig", "lf"))

    def test_bytes_that_are_not_utf8_are_cp1252(self) -> None:
        self.write("caf\xe9 €\r\n".encode("cp1252"))

        found = self.get().json()

        self.assertEqual((found["text"], found["encoding"]), ("caf\xe9 €\n", "cp1252"))

    def test_an_nfo_that_is_not_utf8_is_read_as_dos_text(self) -> None:
        self.write(b"\xc9\xcd\xbb\r\n\xba\xba\r\n\xc8\xcd\xbc\r\n", "readme.nfo")

        found = self.get("readme.nfo").json()

        self.assertEqual((found["text"], found["encoding"], found["editable"]),
                         ("\u2554\u2550\u2557\n\u2551\u2551\n\u255a\u2550\u255d\n",
                          "cp437", True))

    def test_the_majority_line_ending_names_a_mixed_file(self) -> None:
        self.write(b"a\r\nb\r\nc\n")

        self.assertEqual(self.get().json()["newline"], "crlf")

    def test_a_file_with_no_final_newline_says_so(self) -> None:
        self.write(b"a\nb")

        self.assertFalse(self.get().json()["final_newline"])

    def test_a_nul_byte_is_not_text(self) -> None:
        self.write(b"MZ\x00\x01")

        self.assertEqual(self.get().status_code, 400)

    def test_a_byte_cp1252_does_not_define_is_read_only(self) -> None:
        self.write(b"+--+\x81\x8d\xff\n")

        found = self.get().json()

        self.assertEqual((found["editable"], found["why"]),
                         (False, "Read-only, uses characters this editor can't keep"))

    def test_a_file_over_four_megabytes_is_read_only_and_cut(self) -> None:
        self.write(b"a" * (text_file.MAX_BYTES + 10))

        found = self.get().json()

        self.assertEqual((found["editable"], found["why"], len(found["text"])),
                         (False, "Read-only, too large to edit here", text_file.MAX_BYTES))

    def test_a_dropped_share_is_read_only_in_the_words_it_refuses_in(self) -> None:
        self.write(b"a\n")

        with patch.object(library_folders, "writable_game_folder",
                          side_effect=service_errors.BlockedError("its share is not mounted")):
            found = self.get().json()

        self.assertEqual((found["editable"], found["why"]), (False, "its share is not mounted"))

    def test_a_path_out_of_the_folder_or_not_there_is_refused(self) -> None:
        self.assertEqual(self.get("../outside.vbs").status_code, 400)
        self.assertEqual(self.get("Nothing.vbs").status_code, 404)

    def test_a_file_that_is_not_one_of_the_text_kinds_is_refused(self) -> None:
        self.write(b"x", "Taxi.info.json")

        self.assertEqual(self.get("Taxi.info.json").status_code, 400)
        self.assertEqual(self.get("Taxi.vpx").status_code, 400)


    def test_a_path_with_a_nul_is_refused(self) -> None:
        self.assertEqual(self.get("a%00.txt".replace("%00", "\x00")).status_code, 400)

    def test_a_cut_inside_a_character_is_dropped_not_misread(self) -> None:
        self.write(b"a" * (text_file.MAX_BYTES - 1) + "é".encode())

        found = self.get().json()

        self.assertEqual(found["text"], "a" * (text_file.MAX_BYTES - 1))


class SaveTests(TextCase):
    def test_a_save_with_no_edit_leaves_a_mixed_file_alone(self) -> None:
        for base in (b"a\r\r\nb\r\n", b"a\r\nb\nc\r\n", b"a\nb\r\nc\n", b"a\r\nb\r\nc\n\n"):
            with self.subTest(base=base):
                target = self.write(base)
                text = self.get().json()["text"]

                answered = self.put(text, base)

                self.assertEqual(target.read_bytes(), base)
                self.assertEqual((answered.json()["version"], answered.json()["kept"]),
                                 (version(base), None))

    def test_a_failed_write_leaves_no_copy_behind(self) -> None:
        base = b"one\n"
        target = self.write(base)

        with patch("os.fsync", side_effect=OSError("disk full")):
            answered = self.put("two\n", base)

        self.assertGreaterEqual(answered.status_code, 400)
        self.assertEqual(target.read_bytes(), base)
        self.assertEqual(self.assets(), [])

    def test_a_one_line_edit_changes_one_line_and_keeps_crlf(self) -> None:
        base = b"Option Explicit\r\nDim a\r\nDim b\r\n"
        target = self.write(base)

        answered = self.put("Option Explicit\nDim a\nDim c\n", base)

        self.assertEqual(answered.status_code, 200)
        self.assertEqual(target.read_bytes(), b"Option Explicit\r\nDim a\r\nDim c\r\n")
        self.assertEqual(answered.json()["version"], version(target.read_bytes()))

    def test_a_cp1252_file_stays_cp1252(self) -> None:
        base = "caf\xe9\r\n".encode("cp1252")
        target = self.write(base)

        self.put("caf\xe9 €\n", base)

        self.assertEqual(target.read_bytes(), "caf\xe9 €\r\n".encode("cp1252"))

    def test_an_nfo_saved_unchanged_writes_nothing_and_one_edit_changes_one_line(self) -> None:
        base = b"\xc9\xcd\xbb\r\n\xba a\xba\r\n\xc8\xcd\xbc\r\n"
        target = self.write(base, "readme.nfo")
        text = self.get("readme.nfo").json()["text"]

        self.put(text, base, "readme.nfo")
        self.assertEqual(target.read_bytes(), base)

        self.put(text.replace("a", "b"), base, "readme.nfo")
        self.assertEqual(target.read_bytes(), b"\xc9\xcd\xbb\r\n\xba b\xba\r\n\xc8\xcd\xbc\r\n")
        self.assertEqual(self.get("readme.nfo").json()["encoding"], "cp437")

    def test_a_bom_is_kept_and_no_final_newline_stays_gone(self) -> None:
        base = b"\xef\xbb\xbfa\nb"
        target = self.write(base)

        self.put("a\nb2", base)

        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbfa\nb2")

    def test_a_character_the_file_cannot_hold_is_refused_naming_the_line(self) -> None:
        base = "caf\xe9\r\nb\r\n".encode("cp1252")
        target = self.write(base)

        answered = self.put("caf\xe9\nb\nc → d\n", base)

        self.assertEqual(answered.status_code, 400)
        self.assertEqual(answered.json()["error"]["message"],
                         "Line 3 has a character this file can't hold")
        self.assertEqual(target.read_bytes(), base)
        self.assertEqual(self.assets(), [])

    def test_every_save_keeps_the_file_as_it_was(self) -> None:
        base = b"one\r\n"
        target = self.write(base)

        first = self.put("two\n", base)
        self.put("three\n", target.read_bytes())

        kept = sorted(pathlib.Path(self.root / "assets" / GAME).iterdir())
        self.assertEqual([one.read_bytes() for one in kept], [b"one\r\n", b"two\r\n"])
        self.assertEqual(first.json()["kept"], str(kept[0]))
        self.assertEqual([one["reason"] for one in self.assets()], ["edit", "edit"])

    def test_a_media_file_keeps_its_copy_under_media_and_a_web_page_is_text(self) -> None:
        base = b"<p>one</p>\n"
        self.write(base, "rules.html")

        self.assertEqual(self.get("rules.html").json()["text"], "<p>one</p>\n")
        answered = self.put("<p>two</p>\n", base, "rules.html", media=True)

        self.assertEqual(answered.status_code, 200)
        (kept,) = pathlib.Path(self.root / "media" / GAME).iterdir()
        self.assertEqual(kept.read_bytes(), base)
        self.assertFalse((self.root / "assets" / GAME).exists())

    def test_a_file_that_changed_is_a_409_and_stays_as_it_is(self) -> None:
        base = b"one\n"
        target = self.write(base)
        target.write_bytes(b"theirs\n")

        answered = self.put("mine\n", base)

        self.assertEqual(answered.status_code, 409)
        self.assertEqual(answered.json()["error"]["details"]["refused"], text_file.CHANGED)
        self.assertEqual(target.read_bytes(), b"theirs\n")
        self.assertEqual(self.assets(), [])

    def test_replace_changed_writes_over_it_after_keeping_the_newer_one(self) -> None:
        base = b"one\n"
        target = self.write(base)
        target.write_bytes(b"theirs\n")

        answered = self.put("mine\n", base, replace_changed=True)

        self.assertEqual(answered.status_code, 200)
        self.assertEqual(target.read_bytes(), b"mine\n")
        (kept,) = pathlib.Path(self.root / "assets" / GAME).iterdir()
        self.assertEqual(kept.read_bytes(), b"theirs\n")

    def test_a_backups_folder_that_cannot_be_written_asks_before_saving_without(self) -> None:
        base = b"one\n"
        target = self.write(base)

        with patch.object(backup_folder, "for_writing",
                          side_effect=service_errors.BlockedError("the backups folder is full")):
            asked = self.put("two\n", base)
            without = self.put("two\n", base, keep_copy=False)

        self.assertEqual(asked.status_code, 409)
        self.assertEqual(asked.json()["error"]["details"]["refused"], text_file.NO_COPY)
        self.assertEqual(asked.json()["error"]["message"], "the backups folder is full")
        self.assertEqual((without.status_code, without.json()["kept"]), (200, None))
        self.assertEqual(target.read_bytes(), b"two\n")

    def test_a_share_that_refuses_is_a_409_and_writes_nothing(self) -> None:
        base = b"one\n"
        target = self.write(base)

        with patch.object(library_folders, "writable_game_folder",
                          side_effect=service_errors.BlockedError("its share is read-only")):
            answered = self.put("two\n", base)

        self.assertEqual(answered.status_code, 409)
        self.assertEqual(target.read_bytes(), base)

    def test_a_file_that_cannot_be_edited_is_refused_whatever_is_sent(self) -> None:
        for data in (b"+--+\x81\x8d\n", b"a\x00b"):
            with self.subTest(data=data):
                base = data
                target = self.write(base)

                answered = self.put("x\n", base, replace_changed=True)

                self.assertEqual(answered.status_code, 400)
                self.assertEqual(target.read_bytes(), base)

    def test_a_file_over_four_megabytes_is_not_written(self) -> None:
        base = b"a" * (text_file.MAX_BYTES + 1)
        target = self.write(base)

        answered = self.put("x\n", base, replace_changed=True)

        self.assertEqual(answered.status_code, 400)
        self.assertEqual(target.read_bytes(), base)

    def test_it_cannot_write_outside_the_folder_or_create_a_file(self) -> None:
        outside = self.folder.parent / "outside.vbs"
        outside.write_bytes(b"x")

        self.assertEqual(self.put("y", b"x", "../outside.vbs").status_code, 400)
        self.assertEqual(self.put("y", b"x", "New.vbs").status_code, 404)
        self.assertFalse((self.folder / "New.vbs").exists())
        self.assertEqual(outside.read_bytes(), b"x")

    def test_the_copy_is_listed_for_the_game_as_an_edit(self) -> None:
        base = b"one\n"
        self.write(base)

        self.put("two\n", base)

        (found,) = self.assets()
        self.assertEqual((found["subject_name"], found["file"], found["reason"]),
                         (NAME, SCRIPT, "edit"))
        self.assertEqual(found["kind"], copies.KIND_ASSET)
        self.assertEqual(backup_media.ASSETS, "assets")
