"""One asset file, or one folder of them, in detail over HTTP."""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Assets0001"
SCRIPT = "\n".join(f"' line {n}" for n in range(1, 101))


class AssetDetail(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID}}
        folder = write_game(self.root, "Attack from Mars (Bally 1995)", info=info,
                            files={"Attack from Mars (Bally 1995).vbs": SCRIPT.encode(),
                                   "Old Build.ini": "[Player]\r\nName=Café\r\n".encode("cp1252"),
                                   "pupvideos/Backglass/1.mp4": b"x" * 10,
                                   "pupvideos/Topper/2.mp4": b"y" * 5})
        game = fake_game(folder, "Attack from Mars (Bally 1995)", meta=info)
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _get(self, path: str, **query: object):
        return self.client.get(f"/games/{GAME_ID}/assets/detail",
                               params={"path": path, **query})

    def test_a_script_says_its_size_format_and_first_lines(self) -> None:
        said = self._get("Attack from Mars (Bally 1995).vbs").json()

        self.assertEqual(("VBS", len(SCRIPT), 40, "' line 1"),
                         (said["format"], said["size_bytes"], len(said["head"].splitlines()),
                          said["head"].splitlines()[0]))

    def test_all_of_a_script_is_there_for_the_asking(self) -> None:
        said = self._get("Attack from Mars (Bally 1995).vbs", lines=0).json()

        self.assertEqual(100, len(said["head"].splitlines()))

    def test_a_folder_counts_what_it_holds(self) -> None:
        said = self._get("pupvideos").json()

        self.assertEqual((True, 2, 15, None),
                         (said["folder"], said["files"], said["size_bytes"], said["head"]))

    def test_a_path_out_of_the_folder_is_refused(self) -> None:
        self.assertEqual(400, self._get("../../etc/hosts").status_code)

    def test_a_file_that_is_not_there_is_not_found(self) -> None:
        self.assertEqual(404, self._get("Nothing.vbs").status_code)

    def test_a_windows_script_reads_as_text(self) -> None:
        said = self._get("Old Build.ini").json()

        self.assertEqual(["[Player]", "Name=Café"], said["head"].splitlines())
