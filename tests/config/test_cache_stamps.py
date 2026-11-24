from __future__ import annotations

import hashlib
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.online import cache_stamps
from tests.support.stamps import OwnStamps

_ROMS_BYTES = b"{}"
_ROMS_RELEASE = {"assets": [{
    "name": "roms.json",
    "browser_download_url": "https://example.invalid/roms.json",
    "digest": f"sha256:{hashlib.sha256(_ROMS_BYTES).hexdigest()}"}]}


class CacheStampsTests(OwnStamps):
    def test_a_stamp_reads_back_what_was_put(self):
        cache_stamps.put("vpsdb", version="2026-10-01", checked="then")
        self.assertEqual(cache_stamps.get("vpsdb", "version"), "2026-10-01")
        self.assertEqual(cache_stamps.get("vpsdb", "checked"), "then")

    def test_nothing_written_reads_as_empty(self):
        self.assertEqual(cache_stamps.get("vpsdb", "version"), "")

    def test_a_file_that_is_not_json_reads_as_empty(self):
        self.stamps_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(cache_stamps.get("vpsdb", "version"), "")
        cache_stamps.put("roms", release="abc")
        self.assertEqual(cache_stamps.get("roms", "release"), "abc")

    def test_a_put_keeps_the_other_groups_and_keys(self):
        cache_stamps.put("vpsdb", version="v1")
        cache_stamps.put("roms", release="r1")
        cache_stamps.put("vpsdb", checked="now")
        self.assertEqual(cache_stamps.get("vpsdb", "version"), "v1")
        self.assertEqual(cache_stamps.get("vpsdb", "checked"), "now")
        self.assertEqual(cache_stamps.get("roms", "release"), "r1")

    def test_emptying_the_folder_makes_every_cache_due_again(self):
        from common import timestamps
        from common.online import pinmame_score_parser_updater as roms
        from common.online import theme_sync, vpsdb_sync

        held = Path(self.enterContext(TemporaryDirectory()))
        (held / "themes.json").write_text("[]", encoding="utf-8")
        (held / "roms.json").write_text("{}", encoding="utf-8")
        self.enterContext(mock.patch.object(theme_sync, "THEME_CACHE_PATH", held / "themes.json"))
        self.enterContext(mock.patch.object(roms, "ROMS_JSON_PATH", held / "roms.json"))
        config = ConfigParser()
        now = timestamps.utc_now_iso()
        cache_stamps.put("vpsdb", version="v1", checked=now)
        cache_stamps.put("themes", read=now)
        cache_stamps.put("roms", release="abc", checked=now)
        self.assertFalse(vpsdb_sync.due(config))
        self.assertFalse(theme_sync.due(config))
        with mock.patch.object(roms, "_request_json", return_value=_ROMS_RELEASE) as asked:
            roms.ensure_latest_roms_json()
            asked.assert_not_called()

        self.stamps_path.unlink()

        self.assertTrue(vpsdb_sync.due(config))
        self.assertTrue(theme_sync.due(config))
        with mock.patch.object(roms, "_request_json", return_value=_ROMS_RELEASE) as asked, \
                mock.patch.object(roms.http_client, "download_file",
                                  side_effect=lambda url, dest, **_: dest.write_bytes(_ROMS_BYTES)):
            roms.ensure_latest_roms_json()
            asked.assert_called_once()
