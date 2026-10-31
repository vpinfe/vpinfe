"""A media kind's name inside a sentence reads as the catalog gives it."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from manager.kit import confirm
from manager.library import mediamap, mediasource

LIBRARY = SimpleNamespace(placements=None, displaced_by=None, place_media=None,
                          folders=None, offers_online=lambda kind: False,
                          discovery=lambda: {"display_name": "Cabinet"})


async def _nothing() -> None:
    return None


class KindNamesInSentences(unittest.TestCase):
    def test_the_replace_question(self) -> None:
        with patch.object(confirm, "ask", AsyncMock(return_value=True)) as ask:
            asyncio.run(confirm.replace("Real DMD", ["old.png"]))

        self.assertEqual(ask.call_args.args[0], "Replace the Real DMD that is there?")

    def test_a_folder_with_nothing_to_use(self) -> None:
        folder = mediasource._Folder({"library": LIBRARY, "game_id": "game", "game": {}},
                                     "pup_pack", "PUP Pack", _nothing)
        with patch.object(mediasource, "ui"), \
                patch.object(mediasource.folder_picker, "read_start", AsyncMock()), \
                patch.object(mediasource.folder_picker, "draw_browser") as drawn, \
                patch.object(mediasource.offload, "io", AsyncMock(return_value=[])):
            asyncio.run(folder.host_tab(MagicMock()))

        self.assertEqual("Nothing here to use as PUP Pack", drawn.call_args.kwargs["empty"])

    def test_a_file_already_in_a_slot(self) -> None:
        slot = mediasource._Slot(
            {"library": LIBRARY, "game_id": "game", "game": {}, "lens": "",
             "media": {"scoreview": {"file": "afm.png"}}},
            "wheel", "Wheel", _nothing, mediasource._media(LIBRARY, "wheel"))

        self.assertEqual(slot._in_use("afm.png"), "Already the DMD")

    def test_an_empty_tile(self) -> None:
        self.assertEqual(mediamap._tooltip("playfield_fss", {"present": False}),
                         "No Playfield FSS")


if __name__ == "__main__":
    unittest.main()
