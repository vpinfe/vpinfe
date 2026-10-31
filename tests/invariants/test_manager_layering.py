"""Which Manager package may import which, asserted rather than remembered.

Every module belongs to one area, the areas are ranked, and an import may go to the same
rank or below. Anything that goes up is listed here by name, so the next one fails here.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

# The shared layer is rank 0. The areas draw their screens with it and are rank 1. The
# workbench is the panel the grid opens beside any of them, and the remote is the phone
# surface over the same areas, so both sit above. The shell is the page chrome around all.
RANK = {
    "kit": 0,
    "library": 1, "settings": 1, "frontend": 1, "extensions": 1, "devices": 1,
    "setup": 1, "system": 1, "backups": 1, "security": 1, "overview": 1,
    "workbench": 2, "remote": 2,
    "shell": 3,
}

AREAS: dict[str, tuple[str, ...]] = {
    "kit": ("acting", "api", "data", "offload", "on_page", "panel", "dialog", "confirm",
            "confirm_code", "grid", "dropdown", "busy", "stars", "thumbnails", "undo",
            "views", "verbs", "units", "when", "walks", "failed_together", "renderers",
            "rename", "remembered", "qr", "more_below", "tag_chips", "table_features",
            "game_tables", "media_ownership", "folder_picker", "candidates", "kind_words", "art",
            "arrange", "appearance", "appearance_picker", "report_groups", "deeplink", "wizard",
            "need_keys",
            "play_facts", "clipboard", "wizard_lists", "place", "leftover_ids",
            "order_notice", "link_tab", "ghost_timers", "together"),
    "library": ("games", "collections", "collection_adds", "collection_rules", "discovery",
                "discovery_get", "discovery_ext", "discovery_ask", "discovery_home",
                "discovery_marks", "discovery_marks_grid", "discovery_releases",
                "discovery_pictures", "discovery_search", "discovery_shelves",
                "discovery_kinds", "discovery_list", "discovery_pane",
                "assets", "media", "media_fill", "media_rotate", "mediamap", "mediapages",
                "mediasource", "mediaview", "pictures", "backglass_view", "record",
                "release_match", "vps_match", "match_group", "vps_records", "picker_online",
                "picker_memory",
                "import_dialog", "import_swap",
                "getting_file", "import_report", "uploads",
                "file_acts", "file_facts", "file_move", "move_picker", "move_plan", "game_acts",
                "row_drag", "tageditor", "text_editor", "wizard_settle"),
    "settings": ("settings", "sections", "attract", "binding_editor", "binding_table",
                 "chrome_options", "commands", "displays", "dmd_panel", "dof", "folders",
                 "input_page", "library_folders", "input_relay", "placement_panel", "recording",
                 "screens",
                 "screens_picture", "shelves", "vpx_displays", "vpx_find", "vpx_input", "waiting"),
    "frontend": ("themes", "theme_card", "theme_sources", "launchers", "players"),
    "extensions": ("ext_action", "ext_cells", "ext_groups", "ext_links", "ext_mark",
                   "ext_page", "ext_pages", "ext_row", "ext_verbs", "ext_lists"),
    "devices": ("devices", "browser_state", "mobile_add", "pairing", "plays_on",
                "send_to_device"),
    "setup": ("setup", "setup_appearance", "setup_controls", "setup_screens", "setup_tables",
              "setup_vpx"),
    "system": ("about", "whats_new", "jobs", "jobs_list", "job_report", "logs", "metrics"),
    "backups": ("backups", "backup_cleanup", "backup_go_back", "backup_rows", "backups_move"),
    "security": ("security", "accept", "authenticator_panel", "encrypted_panel",
                 "sign_in_gate", "sign_in_notice", "signed_in_rows", "token_panel",
                 "origin_guard"),
    "overview": ("front", "needs", "set_aside"),
    "workbench": ("core", "builds", "rail", "subject_verbs", "tag", "device", "launcher",
                  "all_settings", "collection", "file", "game_table", "game_media",
                  "game_play", "game_guides", "game_assets", "game_match", "app_settings",
                  "selection_marks", "discovery_rail", "leftovers"),
    "remote": ("remote", "remote_record"),
    "shell": ("page", "edit_page", "report_page", "power", "rail_marks", "update_count", "host",
              "loopback", "app_control"),
}
AREA_OF = {module: area for area, modules in AREAS.items() for module in modules}

# An import that goes up a rank, by importing module and imported module. Each is a screen
# calling the host it is drawn in, or the grid page drawing the panel beside it, so the
# names stay and a fifth fails.
UPWARD = {
    ("games", "all_settings"),
    ("file_acts", "game_match"),
    ("launchers", "all_settings"),
    ("ext_row", "core"),
}


def _modules() -> dict[str, pathlib.Path]:
    found = {path.stem: path for path in sorted(MANAGER.rglob("*.py"))
             if "__pycache__" not in path.parts and path.stem != "__init__"}
    return found


def _imported(path: pathlib.Path, modules: set[str]) -> set[str]:
    """The Manager modules this file imports, however it spells it, lazily or not."""
    found: set[str] = set()
    for node in ast.walk(trees.tree_for(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found |= _named(alias.name.split("."), (), modules)
        elif isinstance(node, ast.ImportFrom):
            names = tuple(alias.name for alias in node.names)
            if node.level:
                parts = list(path.relative_to(REPO).with_suffix("").parts[:-node.level])
                parts += (node.module or "").split(".") if node.module else []
            else:
                parts = (node.module or "").split(".")
            found |= _named(parts, names, modules)
    return found


def _named(parts: list[str], names: tuple[str, ...], modules: set[str]) -> set[str]:
    if not parts or parts[0] != "manager":
        return set()
    # `manager.devices` is the package when `devices` is also a module, unless it is
    # spelled `manager.devices.devices`.
    is_package = parts[-1] in AREAS and parts[-2:-1] != [parts[-1]]
    if parts[-1] in modules and not is_package:
        return {parts[-1]}
    imported = {name for name in names if name in modules}
    if imported:
        return imported
    return {parts[-1]} if parts[-1] in modules else set()


def _edges() -> dict[tuple[str, str], str]:
    modules = _modules()
    names = set(modules)
    return {(stem, target): path.relative_to(REPO).as_posix()
            for stem, path in modules.items()
            for target in _imported(path, names) if target != stem}


class ManagerLayeringTests(unittest.TestCase):
    def test_every_module_belongs_to_one_area(self) -> None:
        """A module with no area is a rank nobody chose. A name in two areas, or in an
        area that does not exist, is a typo that would hide an upward import."""
        listed = [module for modules in AREAS.values() for module in modules]
        self.assertEqual(sorted({m for m in listed if listed.count(m) > 1}), [])
        self.assertEqual(sorted(set(AREAS) ^ set(RANK)), [])
        self.assertEqual(sorted(set(_modules()) - set(AREA_OF)), [])
        self.assertEqual(sorted(set(AREA_OF) - set(_modules())), [])

    def test_a_module_in_a_package_lives_with_its_area(self) -> None:
        """Once an area is a folder, the folder and the list above cannot disagree."""
        wrong = []
        for stem, path in _modules().items():
            folder = path.relative_to(MANAGER).parts[:-1]
            if folder and AREA_OF.get(stem) != folder[0]:
                wrong.append(f"{path.relative_to(REPO).as_posix()} is listed under "
                             f"{AREA_OF.get(stem)}")
        self.assertEqual(wrong, [])

    def test_module_names_are_unique(self) -> None:
        """The edges below are keyed by module name, so two files with one name would
        be read as a single module."""
        stems = [path.stem for path in MANAGER.rglob("*.py")
                 if "__pycache__" not in path.parts and path.stem != "__init__"]
        self.assertEqual(sorted({s for s in stems if stems.count(s) > 1}), [])

    def test_no_import_goes_up_a_rank_unless_it_is_named(self) -> None:
        up = sorted(f"{source} -> {target} ({RANK[AREA_OF[source]]} -> "
                    f"{RANK[AREA_OF[target]]}), in {where}"
                    for (source, target), where in _edges().items()
                    if (source, target) not in UPWARD
                    and RANK[AREA_OF[target]] > RANK[AREA_OF[source]])
        self.assertEqual(up, [])

    def test_the_named_exceptions_are_still_upward_imports(self) -> None:
        """An exception that is no longer needed would permit the next import back."""
        edges = _edges()
        stale = sorted(f"{source} -> {target}" for source, target in UPWARD
                       if (source, target) not in edges
                       or RANK[AREA_OF[target]] <= RANK[AREA_OF[source]])
        self.assertEqual(stale, [])

    def test_the_checker_sees_an_import_however_it_is_spelled(self) -> None:
        source = ("from manager import one\n"
                  "from manager.two import something\n"
                  "import manager.three\n"
                  "def late():\n"
                  "    from manager import two\n"
                  "from common import one\n")
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "spelled.py"
            path.write_text(source, encoding="utf-8")
            found = _imported(path, {"one", "two", "three"})
        self.assertEqual(found, {"one", "two", "three"})


if __name__ == "__main__":
    unittest.main()
