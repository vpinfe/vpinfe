"""Protects: (c) a ROM's match was saved under its bare name, where nothing reads it.

Readers look a ROM zip up under its path in the ROMs folder; a media file under its path
below the game; any other asset under its name.
"""

from __future__ import annotations

import unittest

from manager.library import discovery_kinds, discovery_list


class WhereAMatchIsRecorded(unittest.TestCase):
    def test_a_rom_zip_is_recorded_under_the_roms_folder(self) -> None:
        kind = discovery_kinds.Kind(key="rom", word="ROM", listed_as="romFiles")

        key = discovery_list._source_key(kind, discovery_list._Held("tom_13.zip", ""))

        self.assertEqual("pinmame/roms/tom_13.zip", key)

    def test_a_media_file_is_recorded_under_its_path(self) -> None:
        kind = discovery_kinds.Kind(key="wheel", word="Wheel", media="wheel")

        key = discovery_list._source_key(
            kind, discovery_list._Held("w.png", "", {"path": "medias/w.png"}))

        self.assertEqual("medias/w.png", key)

    def test_another_asset_is_recorded_under_its_name(self) -> None:
        kind = discovery_kinds.Kind(key="ini", word="INI", listed_as="iniFiles")

        key = discovery_list._source_key(kind, discovery_list._Held("t.ini", ""))

        self.assertEqual("t.ini", key)


if __name__ == "__main__":
    unittest.main()
