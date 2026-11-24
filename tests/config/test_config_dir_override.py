import importlib
import os
import unittest
from pathlib import Path

import common.config.paths
from common.config_bootstrap import apply_configdir_override

ENV_VAR = "VPINFE_CONFIG_DIR"
# A root-relative path gains the current drive on Windows, so every expectation is
# built from the same Path the override resolves to.
OVERRIDE = Path("/tmp/vpinfe-override").absolute()


class TestConfigDirEnvOverride(unittest.TestCase):
    """common.config.paths.CONFIG_DIR honors VPINFE_CONFIG_DIR at import time."""

    def setUp(self) -> None:
        self._saved = os.environ.get(ENV_VAR)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = self._saved
        # Leave the module resolved against the real environment for other tests.
        importlib.reload(common.config.paths)

    def _reload(self):
        return importlib.reload(common.config.paths)

    def test_override_relocates_config_dir_and_derived_paths(self) -> None:
        os.environ[ENV_VAR] = str(OVERRIDE)
        paths = self._reload()

        self.assertEqual(paths.CONFIG_DIR, OVERRIDE)
        self.assertEqual(paths.VPINFE_INI_PATH, OVERRIDE / "vpinfe.ini")
        self.assertEqual(paths.THEMES_DIR, OVERRIDE / "frontend_themes")
        self.assertEqual(paths.COLLECTIONS_PATH, OVERRIDE / "collections.json")

    def test_an_override_keeps_the_caches_beside_it(self) -> None:
        os.environ[ENV_VAR] = str(OVERRIDE)
        paths = self._reload()

        self.assertEqual(paths.CACHE_DIR, OVERRIDE / "cache")
        for cached in (paths.VPSDB_PATH, paths.USER_ROMS_PATH, paths.THEME_CACHE_PATH,
                       paths.UPDATE_CHECK_PATH, paths.FRONTEND_BROWSER_PATH,
                       paths.COMMUNITY_KEPT_DIR):
            self.assertTrue(cached.is_relative_to(paths.CACHE_DIR), cached)

    def test_without_an_override_caches_leave_the_config_folder(self) -> None:
        os.environ.pop(ENV_VAR, None)
        paths = self._reload()

        self.assertNotEqual(paths.CACHE_DIR, paths.CONFIG_DIR)
        # Compared as text: Windows paths ignore case, and its own cache folder is
        # `Cache` inside the config folder.
        self.assertNotEqual(str(paths.CACHE_DIR), str(paths.CONFIG_DIR / "cache"))
        self.assertEqual(paths.VPSDB_PATH, paths.CACHE_DIR / "vpsdb.json")
        self.assertEqual(paths.USER_ROMS_PATH, paths.CACHE_DIR / "roms.json")

    def test_override_expands_user_home(self) -> None:
        os.environ[ENV_VAR] = "~/vpinfe-home"
        paths = self._reload()

        self.assertEqual(paths.CONFIG_DIR, Path.home() / "vpinfe-home")

    def test_a_relative_override_is_anchored_where_it_was_read(self) -> None:
        os.environ[ENV_VAR] = "relative-config"
        paths = self._reload()

        anchored = Path.cwd() / "relative-config"

        self.assertEqual(paths.CONFIG_DIR, anchored)
        self.assertTrue(paths.COLLECTIONS_PATH.is_absolute())
        self.assertEqual(os.environ[ENV_VAR], str(anchored),
                         "a helper process reads the same variable from another directory")

    def test_blank_override_falls_back_to_platformdirs(self) -> None:
        os.environ.pop(ENV_VAR, None)
        default = self._reload().CONFIG_DIR

        os.environ[ENV_VAR] = "   "
        self.assertEqual(self._reload().CONFIG_DIR, default)


class TestApplyConfigdirOverride(unittest.TestCase):
    """The CLI flag maps onto the env var before common.config.paths is imported."""

    def test_sets_env_from_space_separated_flag(self) -> None:
        env: dict[str, str] = {}
        applied = apply_configdir_override(["--configdir", "/tmp/cfg"], env)

        self.assertEqual(applied, "/tmp/cfg")
        self.assertEqual(env[ENV_VAR], "/tmp/cfg")

    def test_sets_env_from_equals_form(self) -> None:
        env: dict[str, str] = {}
        apply_configdir_override(["--configdir=/tmp/cfg"], env)

        self.assertEqual(env[ENV_VAR], "/tmp/cfg")

    def test_existing_env_wins_over_flag(self) -> None:
        env = {ENV_VAR: "/already/set"}
        applied = apply_configdir_override(["--configdir", "/tmp/cfg"], env)

        self.assertIsNone(applied)
        self.assertEqual(env[ENV_VAR], "/already/set")

    def test_last_flag_wins(self) -> None:
        env: dict[str, str] = {}
        apply_configdir_override(["--configdir", "/first", "--configdir=/second"], env)

        self.assertEqual(env[ENV_VAR], "/second")

    def test_no_flag_leaves_env_untouched(self) -> None:
        env: dict[str, str] = {}
        applied = apply_configdir_override(["--headless", "--buildmeta"], env)

        self.assertIsNone(applied)
        self.assertNotIn(ENV_VAR, env)

    def test_blank_flag_value_is_ignored(self) -> None:
        env: dict[str, str] = {}
        applied = apply_configdir_override(["--configdir", "   "], env)

        self.assertIsNone(applied)
        self.assertNotIn(ENV_VAR, env)

    def test_trailing_flag_without_value_is_ignored(self) -> None:
        env: dict[str, str] = {}
        applied = apply_configdir_override(["--headless", "--configdir"], env)

        self.assertIsNone(applied)
        self.assertNotIn(ENV_VAR, env)


if __name__ == "__main__":
    unittest.main()
