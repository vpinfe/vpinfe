"""How much of Visual Pinball core still knows about, pinned so it cannot grow.

Core is meant to ask an app what a file is, how to launch it and what it can do. Today
it still knows a great deal directly - the score parser reads NVRAM, the importer places
sidecars by name, the ini editor edits an ini. Moving that is the work after the
contract, and this test is what stops the boundary eroding while it is in flight.

The baseline is per file, so a reference moved from one module to another is visible
rather than netting out. It ratchets in one direction: a file may lose references and
never gain them. When a file is emptied, delete its line.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The dirs that are meant to be app-agnostic. `apps/` is where this knowledge belongs,
# so it is not scanned.
SCANNED = ("common", "manager", "httpapi", "frontend")

PATTERN = re.compile(r"\.vpx|vpinballx|directb2s|pinmame|nvram", re.IGNORECASE)

# Compatibility and migration, where naming the old world is the job. A 2.x theme reads
# `deletedNVRamOnClose`; a migration reads what the previous version wrote.
EXEMPT = frozenset({
    "common/deprecations.py",
    "common/games/info_migration.py",
    "common/games/launcher_migration.py",
    "frontend/theme_contract.py",
})

# Lines mentioning Visual Pinball, per file, as of the commit that introduced the
# contract. Lower these as the extraction lands; never raise one.
BASELINE: dict[str, int] = {
    "common/apps/__init__.py": 1,
    "common/config/config_schema.py": 2,
    # The Displays section compares the launcher's Visual Pinball ini with the screens
    # found, so the imports of its screen keys and its ini reader are the comparison, and
    # the compositor probe names a window by the part Visual Pinball's title says it is.
    # The contract has no hook for it yet.
    "common/support/collect/displays/section.py": 2,
    "common/support/collect/displays/compare.py": 1,
    "common/support/collect/displays/probes/wlr.py": 1,
    # Two imports of the settings-file reader, the PinMAME catalog import and its label.
    "common/support/collect/launchers.py": 4,
    "common/games/archive_service.py": 4,
    # Includes the one read of a table's ROM dependency by its key.
    "common/games/asset_lens.py": 7,
    "common/games/asset_registry.py": 4,
    "common/games/asset_resolver.py": 11,
    # Reading and drawing a backglass file is the subject of these two, so the name is
    # their import, their suffix check, the file's own tag and the calls into the parser,
    # one per picture it draws.
    "common/games/backglass_ops.py": 9,
    "common/games/collection_resolver.py": 3,
    # The two that read and write the file's DMD placement: the format's own name is their
    # module, their suffix check and their calls into it.
    "common/games/backglass_displays.py": 14,
    "common/games/formats/directb2s_displays.py": 3,
    "common/games/formats/directb2s_parser.py": 7,
    "common/games/export_bundle.py": 4,
    "common/games/game.py": 2,
    "common/games/game_metadata.py": 2,
    "common/games/game_parser.py": 2,
    "common/games/game_play_service.py": 11,
    "common/games/game_repository.py": 2,
    "common/games/game_service.py": 17,
    "common/games/info_file.py": 7,
    "common/games/info_maintenance.py": 1,
    "common/games/launchers.py": 1,
    "common/games/library_discovery.py": 1,
    "common/games/library_enrichment.py": 2,
    "common/games/library_ops.py": 1,
    "common/games/media_browse.py": 2,
    "common/games/media_lookup.py": 2,
    "common/games/media_ops.py": 2,
    "common/games/media_placement.py": 1,
    "common/games/metadata_service.py": 1,
    "common/games/score_parser.py": 10,
    # Names are read from the table script the way the parser strips its comments.
    "common/games/formats/script_names.py": 1,
    "common/games/standalone_scripts.py": 2,
    "common/games/table_lens.py": 15,
    "common/games/table_ops.py": 6,
    "common/games/tables.py": 8,
    "common/games/formats/vpx_parser.py": 13,
    # The import of the folder Visual Pinball's DOF plugin reads, which the frontend's DOF shares.
    "common/host/dof_config.py": 1,
    "common/host/launch.py": 6,
    "common/host/launch_state.py": 1,
    "common/host/pinmame_catalog.py": 14,
    "common/host/pinmame_worker.py": 14,
    "common/host/vpx_log.py": 1,
    "common/config/labels.py": 1,
    "common/launcher_path.py": 1,
    "common/online/pinmame_score_parser_updater.py": 7,
    "common/online/vps_kinds.py": 1,
    "common/online/vps_lens.py": 2,
    "common/uploads/asset_analyzer_service.py": 6,
    # The two folders a ROM's nvram and cfg are placed in.
    "common/uploads/asset_import_service.py": 20,
    "manager/library/assets.py": 2,
    "manager/kit/data.py": 1,
    # The DMD panel's kinds are the names the plugin's keys take.
    "manager/settings/dmd_panel.py": 1,
    "manager/kit/game_tables.py": 1,
    "manager/library/games.py": 9,
    "manager/library/media.py": 1,
    "manager/kit/media_ownership.py": 2,
    "manager/library/mediasource.py": 2,
    "manager/settings/sections.py": 2,
    # Both lines are the VPX Mobile page id and its note key. They go when the page does.
    "manager/settings/settings.py": 2,
    # Setup's Visual Pinball step: the walk's three pages and the session that holds them,
    # and the Screens answer handed to the launcher it makes.
    "manager/setup/setup.py": 14,
    # The three pages of that step: the program, the playfield glass, the buttons.
    "manager/setup/setup_vpx.py": 25,
    "manager/kit/table_features.py": 1,
    "manager/kit/appearance.py": 2,
    # The Displays page of a Visual Pinball launcher: the file's keys are its subject.
    "manager/settings/vpx_displays.py": 12,
    # Where the program is on this machine: the names its builds go by are the subject.
    "manager/settings/vpx_find.py": 11,
    # The Input page of a Visual Pinball launcher: the file's keys are its subject.
    "manager/settings/vpx_input.py": 9,
    "manager/library/file_acts.py": 1,
    "manager/workbench/core.py": 1,
    "manager/workbench/file.py": 1,
    "manager/workbench/game_assets.py": 5,
    "manager/workbench/game_match.py": 1,
    "manager/workbench/game_play.py": 2,
    "manager/workbench/game_table.py": 15,
    "manager/workbench/launcher.py": 7,
    "manager/workbench/selection_marks.py": 5,
    "common/games/marks_ops.py": 4,
    # The score file a table's own script names: the table's script is the subject.
    "common/games/em_scores.py": 2,
    "httpapi/core_capabilities.py": 5,
    "httpapi/filesystem.py": 1,
    "httpapi/games.py": 4,
    "httpapi/library.py": 2,
    "httpapi/models.py": 18,
    "httpapi/tables.py": 1,
}


def _counts() -> dict[str, int]:
    """Matching lines per file, keyed by the path relative to the repo root."""
    found: dict[str, int] = {}
    for directory in SCANNED:
        for source in sorted((ROOT / directory).rglob("*.py")):
            relative = source.relative_to(ROOT).as_posix()
            if relative in EXEMPT:
                continue
            hits = sum(1 for line in source.read_text(encoding="utf-8").splitlines()
                       if PATTERN.search(line))
            if hits:
                found[relative] = hits
    return found


class VPXSurfaceRatchetTests(unittest.TestCase):
    def test_no_file_gains_a_reference(self) -> None:
        counts = _counts()

        for path in sorted(counts):
            with self.subTest(path=path):
                self.assertLessEqual(
                    counts[path], BASELINE.get(path, 0),
                    f"{path} names Visual Pinball on {counts[path]} lines, up from "
                    f"{BASELINE.get(path, 0)}. Ask the app instead, or raise the "
                    f"baseline deliberately.")

    def test_the_exempt_files_are_real(self) -> None:
        """An exemption for a file that has been renamed away is a hole nobody sees."""
        for path in sorted(EXEMPT):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

    def test_the_baseline_names_no_file_that_is_gone(self) -> None:
        counts = _counts()

        self.assertEqual(
            sorted(path for path in BASELINE
                   if path not in counts and not (ROOT / path).is_file()),
            [],
            "The baseline names files that no longer exist. Delete their lines.")


if __name__ == "__main__":
    unittest.main()
