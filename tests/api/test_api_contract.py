"""Pins the HTTP behavior every in-repo consumer depends on.

The drag-and-drop client, the theme frontend's remote-launch poll and the mobile
page's download link all speak to these routes, so a change here is a change a
user can see. Endpoints keep their entry once they move under /api/v1, which is
what makes a move provably behavior-preserving.

Runs the app in a subprocess with a throwaway config dir: common.config.paths resolves
CONFIG_DIR at import time, so isolation is only reliable in a fresh interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_probe() -> dict:
    with TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / "config"
        games_dir = Path(tmp) / "games"
        game = games_dir / "Example Table (Bally 1990)"
        game.mkdir(parents=True)
        (game / "Example Table (Bally 1990).vpx").write_bytes(b"not really a vpx")
        # Assets the parser should find: a backglass, an ini, a ROM and music.
        (game / "Example Table (Bally 1990).directb2s").write_bytes(b"b2s")
        (game / "Example Table (Bally 1990).ini").write_text("[Standalone]", encoding="utf-8")
        (game / "pinmame" / "roms").mkdir(parents=True)
        (game / "pinmame" / "roms" / "exmpl.zip").write_bytes(b"rom")
        (game / "music").mkdir()
        (game / "music" / "theme.mp3").write_bytes(b"music")
        # Media: one canonical, one root-fallback, most kinds absent.
        (game / "medias").mkdir()
        (game / "medias" / "wheel.png").write_bytes(b"\x89PNG wheel")
        (game / "bg.png").write_bytes(b"\x89PNG bg at root")
        (game / "Example Table (Bally 1990).info").write_text(json.dumps({
            "Info": {"Title": "Example Table", "Manufacturer": "Bally", "Year": "1990",
                     "Type": "SS", "VPSId": "vps-example"},
            "tables": {"Example Table (Bally 1990).vpx": {"rom": "exmpl",
                                                            "detect_pinmame": True}},
            "User": {"Rating": 3},
        }), encoding="utf-8")

        # A folder holding several .vpx, plus a .vbs that is not a table.
        multi = games_dir / "Multi File (Bally 1991)"
        multi.mkdir()
        for name in ("Multi File (Bally 1991).vpx", "Multi File (Bally 1991) - alt.vpx",
                     "Multi File (Bally 1991) - VPW.vpx"):
            (multi / name).write_bytes(b"vpx")
        (multi / "Multi File (Bally 1991).vbs").write_text("' sidecar", encoding="utf-8")
        (multi / "Multi File (Bally 1991).info").write_text(json.dumps({
            "Info": {"Title": "Multi File", "VPSId": "vps-multi"},
            "tables": {
                "Multi File (Bally 1991).vpx": {"rom": "multi"},
                # Recorded but never parsed: a patched build knows where it came from
                # before anything has opened it.
                "Multi File (Bally 1991) - VPW.vpx": {
                    "source": {"base": {"file": "Multi File (Bally 1991).vpx",
                                        "hash": "3a77427e"},
                               "patch": {"format": "jojodiff",
                                         "applied": "2026-07-30T15:00:18Z"}},
                },
            },
        }), encoding="utf-8")

        # .info names a .vpx that is not on disk.
        mismatch = games_dir / "Mismatch (Bally 1992)"
        mismatch.mkdir()
        (mismatch / "Mismatch (Bally 1992).vpx").write_bytes(b"vpx")
        (mismatch / "Mismatch (Bally 1992).info").write_text(json.dumps({
            "Info": {"Title": "Mismatch", "VPSId": "vps-mismatch"},
            "tables": {"does-not-exist.vpx": {"rom": "gone"}},
        }), encoding="utf-8")

        env = dict(os.environ)
        env["VPINFE_CONFIG_DIR"] = str(config_dir)
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

        config_dir.mkdir(parents=True)
        (config_dir / "vpinfe.ini").write_text(
            f"[Settings]\ngamerootdir = {games_dir}\n", encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-m", "tests.support.api_probe"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=180,
        )
    stdout = proc.stdout.strip().splitlines()
    payload = json.loads(stdout[-1]) if stdout else {"__error__": proc.stderr[-2000:]}
    if "__error__" in payload:
        raise AssertionError(f"probe failed: {payload['__error__']}\n{proc.stderr[-2000:]}")
    return payload


class ApiContractTests(unittest.TestCase):
    """Each assertion describes behavior a consumer relies on. A failure after a
    route move means the move changed something visible."""

    probe: dict

    @classmethod
    def setUpClass(cls) -> None:
        # Deliberately not skipped on failure: a safety net that quietly opts out
        # when it cannot run is not a safety net.
        cls.probe = _run_probe()

    def test_play_state_shape_and_cors(self) -> None:
        """A browser page that sends an Origin gets the CORS header, so a page on another
        install's address can read it with a token."""
        entry = self.probe["play_state"]

        self.assertEqual(entry["status"], 200)
        self.assertEqual(entry["json"],
                         {"launching": False, "game_name": None, "source": None,
                          "paused": False, "game_id": None, "started_at": None})
        self.assertEqual(entry["cors"], "*", "themes call this from another origin")
        # Same-origin callers get no CORS header, which is correct and not a regression:
        # the header only has meaning in a cross-origin response.
        self.assertEqual(self.probe["play_state_same_origin"]["json"], entry["json"])

    def test_play_state_reports_a_launch_in_progress(self) -> None:
        """The transition the frontend puts its overlay up on.

        `source` was added when the wheel, the Remote page and the API were moved
        onto one launch path: the state is now set for every launch rather than
        only the ones the frontend did not start, so a consumer has to be able to
        tell whose it is. Additive - the two fields consumers already read are
        unchanged.
        """
        launching = self.probe["play_state_launching"]["json"]
        cleared = self.probe["play_state_cleared"]["json"]

        self.assertIsInstance(launching.pop("started_at"), str)
        self.assertEqual(launching, {"launching": True,
                                     "game_name": "Medieval Madness (Williams 1997)",
                                     "source": "remote", "paused": False,
                                     "game_id": "mm-1"})
        self.assertEqual(cleared,
                         {"launching": False, "game_name": None, "source": None,
                          "paused": False, "game_id": None, "started_at": None})

    def test_launch_refuses_before_it_starts_anything(self) -> None:
        """Every refusal is answered synchronously. A launch that returns 202 and
        then fails on its thread tells the caller nothing.

        The reason names the launcher rather than a config key: the binary belongs to a
        launcher now, and pointing somebody at `vpxbinpath` would send them to a setting
        that no longer decides anything.
        """
        no_launcher = self.probe["launch_no_launcher"]
        self.assertEqual(no_launcher["status"], 501)
        self.assertEqual(no_launcher["json"]["error"]["code"], "feature_unavailable")
        self.assertIn("Visual Pinball X", no_launcher["json"]["error"]["message"])

    def test_launch_rejects_a_table_the_game_does_not_have(self) -> None:
        entry = self.probe["launch_unknown_file"]

        self.assertEqual(entry["status"], 400)
        self.assertEqual(entry["json"]["error"]["code"], "invalid_request")

    def test_launch_conflicts_while_something_is_already_playing(self) -> None:
        """Two VPX processes would fight over the same hardware."""
        entry = self.probe["launch_while_busy"]

        self.assertEqual(entry["status"], 409)
        self.assertEqual(entry["json"]["error"]["code"], "conflict")

    def test_launch_of_an_unknown_game_is_a_not_found(self) -> None:
        entry = self.probe["launch_unknown_table"]

        self.assertEqual(entry["status"], 404)
        self.assertEqual(entry["json"]["error"]["code"], "not_found")

    def test_a_game_reports_its_assets_not_its_media(self) -> None:
        """Assets are what the game needs to play; media is the artwork shown
        while browsing - see docs/conventions.md. The detail endpoint is the
        inventory lens: every kind, files attributed."""
        game = self.probe["table_get"]["json"]

        self.assertIn("assets", game)
        self.assertNotIn("media", game, "these were mislabelled as media")
        self.assertEqual(set(game["assets"]),
                         {"backglass", "b2spro", "ini", "script", "pov", "scv", "nix",
                          "pup_pack", "alt_color", "alt_sound", "music", "readme"})

    def test_assets_present_in_the_folder_are_reported(self) -> None:
        """The fixture game ships a backglass, a per-table ini and music."""
        assets = self.probe["table_get"]["json"]["assets"]

        self.assertTrue(assets["backglass"]["present"])
        self.assertTrue(assets["ini"]["present"])
        self.assertTrue(assets["music"]["present"])
        self.assertFalse(assets["pup_pack"]["present"], "the fixture has none")
        # The inventory lens attributes each file to what it serves.
        self.assertEqual(assets["backglass"]["files"][0]["binding"], "shared")

    def test_a_table_says_whether_it_will_run(self) -> None:
        """The rollup over every required-to-launch kind, on the wire rather than
        re-derived per client. It is computed in `_tables` and reaches nobody unless
        the response model declares it - which is how it shipped as null on a table
        whose file was present and whose rom was installed."""
        entry = self.probe["table_files"]["json"]["tables"][0]

        self.assertIn("launchable", entry)
        self.assertIs(entry["launchable"], True)

    def test_a_table_reports_what_it_would_use_on_launch(self) -> None:
        """The launch lens: resolved assets and the pinmame chain, per table."""
        entry = self.probe["table_files"]["json"]["tables"][0]

        self.assertEqual(entry["assets"]["backglass"]["resolution"], "shared")
        self.assertEqual(entry["assets"]["ini"]["resolution"], "shared")
        # The wire model makes the shape uniform: an unresolved kind still
        # carries file, explicitly null.
        self.assertEqual(entry["assets"]["pov"], {"resolution": "none", "file": None})

        chain = entry["dependencies"]["pinmame"]
        self.assertEqual(chain["declared"], "exmpl")
        self.assertEqual(chain["effective"], "exmpl")
        self.assertTrue(chain["installed"], "exmpl.zip is in the fixture's roms folder")
        self.assertTrue(chain["required"], "the fixture's metadata says the script drives pinmame")
        self.assertFalse(chain["nvram"]["present"], "nothing has been played")

    def test_an_unparsed_table_gets_an_honest_unknown_rom(self) -> None:
        """Every build answers for itself now. One that has not been parsed says so
        rather than inheriting the ROM of one that has.

        Including a build the .info already describes for other reasons: the fixture's
        VPW build records where it was patched from, and an entry existing is not the
        same as the file having been read. "No rom declared" is a claim about the
        build; "not parsed yet" is the truth about our knowledge of it."""
        entries = self.probe["multi_file_files"]["json"]["tables"]
        by_name = {e["filename"]: e for e in entries}

        described = by_name["Multi File (Bally 1991).vpx"]["dependencies"]["pinmame"]
        self.assertEqual(described["declared"], "multi")

        unparsed = [e for name, e in by_name.items()
                    if name != "Multi File (Bally 1991).vpx"]
        self.assertTrue(unparsed)
        for entry in unparsed:
            chain = entry["dependencies"]["pinmame"]
            self.assertIsNone(chain["declared"], "must not inherit another build's rom")
            self.assertIsNone(chain["required"])
            self.assertEqual(set(entry["features"].values()), {None},
                             "nothing has read it, which is not answering no")

    def test_rom_presence_is_not_reported_as_an_asset(self) -> None:
        """A declared ROM name may be a PinMAME dependency or just a DOF key. Until
        the two can be told apart, "no ROM file" would read as broken on every EM
        table, which is most of the ones that declare a name."""
        game = self.probe["table_get"]["json"]

        self.assertNotIn("rom", game["assets"])
        self.assertIn("rom", game, "the declared name is still metadata on the table")

    def test_media_lists_every_kind_present_or_not(self) -> None:
        """Media is the artwork about a game - exactly the media_specs kinds. A
        client enumerates what is possible instead of guessing from omissions."""
        media = self.probe["media_list"]["json"]["media"]

        self.assertTrue(media["wheel"]["present"])
        self.assertEqual(media["wheel"]["file"], "wheel.png")
        self.assertTrue(media["backglass"]["present"], "root-level fallback is found")
        self.assertFalse(media["flyer"]["present"])
        self.assertIsNone(media["flyer"]["links"]["self"])
        self.assertIn("playfield_video", media)
        self.assertIn("audio", media)

    def test_media_says_which_tier_served_each_kind(self) -> None:
        """`via` answers "why this file", which a client cannot work out from the name.
        A wheel borrowed from a fallback and a dedicated one look identical without it,
        and the value reaches a screen, so the vocabulary is part of the contract."""
        media = self.probe["media_list"]["json"]["media"]

        self.assertEqual(media["wheel"]["via"], "default", "the fixed-name slot")
        self.assertEqual(media["backglass"]["via"], "default")
        self.assertIsNone(media["flyer"]["via"], "nothing served it, so no tier")
        for kind, entry in media.items():
            if entry["present"]:
                self.assertIsNotNone(entry["via"], f"{kind} is present but says no tier")

    def test_a_media_file_is_streamed_with_its_content_type(self) -> None:
        entry = self.probe["media_wheel"]

        self.assertEqual(entry["status"], 200)
        self.assertEqual(entry["content_type"], "image/png")
        self.assertGreater(entry["bytes"], 0)

    def test_absent_media_is_not_found_and_unknown_kind_is_invalid(self) -> None:
        absent = self.probe["media_absent"]
        self.assertEqual(absent["status"], 404)
        self.assertEqual(absent["json"]["error"]["code"], "not_found")

        unknown = self.probe["media_unknown_kind"]
        self.assertEqual(unknown["status"], 400)
        self.assertEqual(unknown["json"]["error"]["code"], "invalid_request")
        self.assertIn("wheel", unknown["json"]["error"]["details"]["known"])

    def test_the_old_remote_launch_route_is_gone(self) -> None:
        self.assertGreaterEqual(self.probe["legacy_remote_launch_gone"]["status"], 400)

    # --- games, and the archive that used to be /api/download-table-vpxz -----

    def test_listing_games_returns_addressable_resources(self) -> None:
        entry = self.probe["tables_list"]

        self.assertEqual(entry["status"], 200)
        body = entry["json"]
        self.assertEqual(body["total"], 3)
        game = [t for t in body["games"] if t["name"] == "Example Table"][0]
        self.assertTrue(game["id"], "every listed table is addressable")
        self.assertEqual(game["vps_id"], "vps-example", "correlation, not identity")
        self.assertEqual(game["name"], "Example Table")

    def test_a_game_resource_links_to_its_sub_resources(self) -> None:
        game = self.probe["table_get"]["json"]

        self.assertEqual(self.probe["table_get"]["status"], 200)
        self.assertEqual(game["links"]["tables"],
                         f"/api/v1/games/{game['id']}/tables")
        self.assertEqual(game["links"]["archive"], f"/api/v1/games/{game['id']}/archive")

    def test_tables_are_a_list_even_though_there_is_one_today(self) -> None:
        """A game is not permanently one .vpx; the shape says so now."""
        entry = self.probe["table_files"]

        self.assertEqual(entry["status"], 200)
        files = entry["json"]["tables"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["format"], "vpx")
        self.assertTrue(files[0]["default"])
        self.assertTrue(files[0]["available"])

    def test_archive_downloads_with_the_progress_cookie_preserved(self) -> None:
        """The 2.x Mobile page watches for this cookie to know the download started."""
        entry = self.probe["table_archive"]

        self.assertEqual(entry["status"], 200)
        self.assertEqual(entry["content_type"], "application/octet-stream")
        self.assertIn(".vpxz", entry["disposition"])
        self.assertIn("vpinfe_vpxz_download_abc123=1", entry["set_cookie"] or "")
        self.assertGreater(entry["bytes"], 0)

    def test_a_folder_with_several_vpx_reports_all_of_them(self) -> None:
        """A game folder can hold more than one .vpx, and .vbs is not a table."""
        files = self.probe["multi_file_files"]["json"]["tables"]

        names = [f["filename"] for f in files]
        self.assertEqual(names, sorted(names, key=str.lower), "order must not depend on the disk")
        self.assertEqual(len(files), 3)
        self.assertNotIn("Multi File (Bally 1991).vbs", names)
        self.assertEqual(len([f for f in files if f["default"]]), 1)
        self.assertTrue(all(f["available"] for f in files))

    def test_a_recorded_file_that_is_missing_is_reported_but_not_the_default(self) -> None:
        """Reporting it matters; pointing a caller at it to launch does not."""
        files = self.probe["mismatch_files"]["json"]["tables"]

        by_name = {f["filename"]: f for f in files}
        self.assertFalse(by_name["does-not-exist.vpx"]["available"])
        self.assertFalse(by_name["does-not-exist.vpx"]["default"])
        self.assertTrue(by_name["Mismatch (Bally 1992).vpx"]["available"])
        self.assertTrue(by_name["Mismatch (Bally 1992).vpx"]["default"])

    def test_an_unknown_game_is_a_404_in_the_envelope(self) -> None:
        for key in ("table_unknown", "archive_unknown"):
            with self.subTest(endpoint=key):
                entry = self.probe[key]
                self.assertEqual(entry["status"], 404)
                self.assertEqual(entry["json"]["error"]["code"], "not_found")

    def test_the_old_archive_route_is_gone(self) -> None:
        """Addressing by id also retires the path-traversal case the old route had
        to guard: an id either maps to a known table or it does not exist."""
        self.assertGreaterEqual(self.probe["legacy_archive_gone"]["status"], 400)

    # --- uploads, now under /api/v1 ------------------------------------------

    def test_the_drag_and_drop_upload_sequence_works_end_to_end(self) -> None:
        """begin -> add file -> summary -> delete, exactly as dnd_upload.js drives it."""
        begin = self.probe["upload_begin"]
        self.assertEqual(begin["status"], 200)
        self.assertTrue(begin["json"].get("id"), "the client reads .id")

        added = self.probe["upload_add_file"]
        self.assertEqual(added["status"], 200)
        self.assertEqual(added["json"], {"bytes": 5})

        summary = self.probe["upload_summary"]
        self.assertEqual(summary["status"], 200)
        self.assertEqual(summary["json"], {"file_count": 1, "total_bytes": 5})

        deleted = self.probe["upload_delete"]
        self.assertEqual(deleted["status"], 200)
        self.assertEqual(deleted["json"], {"ok": True})

    def test_an_unknown_upload_session_is_a_404_in_the_envelope(self) -> None:
        for key in ("upload_unknown_session", "upload_analysis_unknown"):
            with self.subTest(endpoint=key):
                entry = self.probe[key]
                self.assertEqual(entry["status"], 404)
                self.assertEqual(entry["json"]["error"]["code"], "not_found")

    def test_vps_search_returns_a_results_list(self) -> None:
        entry = self.probe["vps_search"]

        self.assertEqual(entry["status"], 200)
        self.assertIsInstance(entry["json"].get("results"), list)

    def test_the_old_upload_routes_are_gone(self) -> None:
        """Their only consumer was our own drag-and-drop client, which moved with them.

        Not asserted as a clean 404: NiceGUI's own 404 handler renders a page and
        needs the app config that ui.run() installs, which this harness never calls.
        Under a real server it is a 404; here the point is only that it no longer serves.
        """
        self.assertGreaterEqual(self.probe["legacy_upload_gone"]["status"], 400)

    def test_every_live_endpoint_answers_as_json(self) -> None:
        for name, entry in self.probe.items():
            if name in ("legacy_upload_gone", "legacy_archive_gone",
                        "legacy_remote_launch_gone", "table_archive", "media_wheel"):
                continue  # NiceGUI's own 404s, and the file downloads
            with self.subTest(endpoint=name):
                self.assertEqual(entry["content_type"], "application/json")


class HandlerKeysMatchTheModelsTests(unittest.TestCase):
    """A handler that builds its response as a dict literal must use the field names
    its response model declares.

    Four endpoints shipped broken because the vocabulary rename moved model fields and
    dataclass attributes - both identifiers - and left the dict keys, which are strings
    the codemod never sees. `{"has_table": analysis.has_game}` reads fine and returns a
    500, because FastAPI validates the return against the annotation. Three did exactly
    that; the fourth silently dropped a filter.

    So this checks the shape of the mistake rather than the four instances: a key no
    model declares, whose table/game counterpart one does.
    """

    def test_a_handler_returning_a_dict_uses_its_models_field_names(self) -> None:
        import ast

        from httpapi import models
        from tests.support import trees

        def dict_keys(node):
            return {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}

        offenders = []
        repo_root = Path(__file__).resolve().parent.parent.parent
        for path in sorted((repo_root / "httpapi").glob("*.py")):
            tree = trees.tree_for(path)
            functions = [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

            # Helpers like _plan_to_dict build the payload the endpoint returns, so the
            # keys are one call away from the annotation that validates them.
            builders = {fn.name: {key for ret in ast.walk(fn)
                                  if isinstance(ret, ast.Return)
                                  and isinstance(ret.value, ast.Dict)
                                  for key in dict_keys(ret.value)}
                        for fn in functions}

            for fn in functions:
                ann = fn.returns
                if not (isinstance(ann, ast.Attribute) and isinstance(ann.value, ast.Name)
                        and ann.value.id == "models"):
                    continue
                model = getattr(models, ann.attr, None)
                if model is None or not hasattr(model, "model_fields"):
                    continue
                declared = set(model.model_fields)

                emitted = set()
                for ret in ast.walk(fn):
                    if not isinstance(ret, ast.Return):
                        continue
                    if isinstance(ret.value, ast.Dict):
                        emitted |= dict_keys(ret.value)
                    elif isinstance(ret.value, ast.Call) and isinstance(ret.value.func, ast.Name):
                        emitted |= builders.get(ret.value.func.id, set())

                for key in sorted(emitted - declared):
                    offenders.append(f"{path.name}:{fn.name} returns {key!r}, "
                                     f"but {ann.attr} declares {sorted(declared)}")

        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
