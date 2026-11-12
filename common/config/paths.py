"""Where VPinFE keeps its own files.

Resolved once at import, because a frozen build and a `--configdir` run have to agree
on the answer before anything else reads it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_log_dir, user_state_dir

from common.config.config_store import ConfigStore


def _resolve_config_dir() -> Path:
    # VPINFE_CONFIG_DIR lets you point the whole config directory (vpinfe.ini,
    # themes/, and the cache/, state/ and logs/ beside them) somewhere other than the
    # OS default. Read at import time so it also applies to frozen PyInstaller
    # builds. Falls back to the platformdirs location when unset.
    # Anchored here, once: a relative path would follow the working directory. Written
    # back so a helper started from another directory resolves the same one.
    override = os.environ.get("VPINFE_CONFIG_DIR", "").strip()
    if override:
        anchored = Path(override).expanduser().absolute()
        os.environ["VPINFE_CONFIG_DIR"] = str(anchored)
        return anchored
    if _unbootstrapped_test_run():
        raise RuntimeError(
            "This is a unittest run that skipped tests/__init__.py, so it would use the "
            "real config folder. Run: python -m unittest discover -t . -s tests"
            " (or: python -m tests)")
    return Path(user_config_dir("vpinfe", "vpinfe"))


def _resolve_cache_dir() -> Path:
    # Read after _resolve_config_dir, which writes an anchored override back to the
    # environment: an overridden config folder keeps its caches beside it, so a test, a
    # `git instance` and a single-folder install stay whole.
    if os.environ.get("VPINFE_CONFIG_DIR", "").strip():
        return CONFIG_DIR / "cache"
    return Path(user_cache_dir("vpinfe", "vpinfe"))


def _resolve_state_dir() -> Path:
    # Where the OS keeps state apart from config (Linux), that folder. Where it answers
    # with the config folder itself (macOS, Windows), a subfolder of it named as its
    # neighbours are: `State` beside Windows' `Cache` and `Logs`, `state` elsewhere.
    # An overridden config folder keeps its state beside it, like its cache.
    if os.environ.get("VPINFE_CONFIG_DIR", "").strip():
        return CONFIG_DIR / "state"
    native = Path(user_state_dir("vpinfe", "vpinfe"))
    if native != CONFIG_DIR:
        return native
    return CONFIG_DIR / ("State" if sys.platform == "win32" else "state")


def _resolve_log_dir() -> Path:
    # Where the OS keeps logs, which on every platform is a folder of its own; an
    # overridden config folder keeps them beside it, like its cache and state.
    if os.environ.get("VPINFE_CONFIG_DIR", "").strip():
        return CONFIG_DIR / "logs"
    return Path(user_log_dir("vpinfe", "vpinfe"))


def _unbootstrapped_test_run() -> bool:
    """A `python -m unittest` run that never imported tests/, which sets the override."""
    main = sys.modules.get("__main__")
    entry = getattr(getattr(main, "__spec__", None), "name", "")
    return entry == "unittest.__main__" and "tests" not in sys.modules


# Where the app itself lives, as opposed to the user's config. Bundled assets sit
# under it: frontend/static/, third_party/, main.py. Resolved here so no module has to count
# its own directory levels - doing that is what broke when common/ gained
# subpackages, and it broke silently everywhere a test did not happen to look.
APP_ROOT = Path(__file__).resolve().parent.parent.parent


def bundled(*parts: str) -> Path:
    """A file the build ships, wherever this copy of VPinFE is running from.

    Measured rather than assumed, on a real frozen build of each kind: `APP_ROOT` and
    `sys._MEIPASS` are the same directory - `_internal/` for a onedir build,
    `Contents/Frameworks/` inside a macOS .app - and in a source checkout it is the
    repo. So one expression covers every case, and the callers that each grew their own
    chain of guesses do not need one.
    """
    return APP_ROOT.joinpath(*parts)

CONFIG_DIR = _resolve_config_dir()
VPINFE_INI_PATH = CONFIG_DIR / "vpinfe.ini"
COLLECTIONS_PATH = CONFIG_DIR / "collections.json"
TAGS_PATH = CONFIG_DIR / "tags.json"
# Read once and converted; kept as a name so a restore can still find its backups.
COLLECTIONS_INI_PATH = CONFIG_DIR / "collections.ini"
# 2.x reads `themes/` and nothing else, so 3.0 keeps its own beside it.
THEMES_FOLDER = "frontend_themes"
THEMES_DIR = CONFIG_DIR / THEMES_FOLDER
# A replaced theme is moved to `<theme>.previous` in that folder rather than deleted; the
# folder listing skips these, so every reader of the folder needs the one spelling.
ASIDE_SUFFIX = ".previous"
CACHE_DIR = _resolve_cache_dir()
STATE_DIR = _resolve_state_dir()
LOG_DIR = _resolve_log_dir()
CAPTURE_DIR = STATE_DIR / "capture"
# What a job that asks to be kept answered, one file per run, for the Jobs view to open.
JOBS_DIR = STATE_DIR / "jobs"
# Where backups go until a folder is chosen.
DEFAULT_BACKUPS_DIR = CONFIG_DIR / "backups"
DERIVED_TAGS_PATH = CACHE_DIR / "derived_tags.json"
THEME_CACHE_PATH = CACHE_DIR / "themes.json"
UPDATE_CHECK_PATH = CACHE_DIR / "update.json"
FRONTEND_BROWSER_PATH = CACHE_DIR / "frontend_browser.json"
FRONTEND_WINDOWS_PATH = CACHE_DIR / "frontend_windows.json"
COMMUNITY_KEPT_DIR = CACHE_DIR / "community"
VPSDB_PATH = CACHE_DIR / "vpsdb.json"
STAMPS_PATH = CACHE_DIR / "stamps.json"
PLUGIN_PROFILES_DIR = CONFIG_DIR / "plugin_profiles"
LAUNCHER_SETTINGS_DIR = CONFIG_DIR / "launcher_settings"
SECRETS_PATH = CONFIG_DIR / "secrets.json"
ACCEPTED_PATH = CONFIG_DIR / "accepted.json"
SESSIONS_PATH = CONFIG_DIR / "sessions.json"
TOKENS_PATH = CONFIG_DIR / "tokens.json"
CONNECTIONS_PATH = CONFIG_DIR / "connections.json"
UPDATES_DIR = STATE_DIR / "updates"
USER_CONFIG_PATH = VPINFE_INI_PATH
USER_ROMS_PATH = CACHE_DIR / "roms.json"


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        # The folder holds the secrets file and the accepted list; other accounts on the
        # machine have no business listing it. Not fatal where the folder is not ours.
        try:
            os.chmod(CONFIG_DIR, 0o700)
        except OSError:
            pass
    return CONFIG_DIR


def configure_nicegui_storage() -> str:
    nicegui_storage_dir = STATE_DIR / ".nicegui"
    nicegui_storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = str(nicegui_storage_dir)
    os.environ["NICEGUI_STORAGE_PATH"] = storage_path
    return storage_path


def get_ini_config() -> ConfigStore:
    ensure_config_dir()
    return ConfigStore(str(VPINFE_INI_PATH))


def get_games_path(default: str = "~/tables") -> str:
    try:
        from common.config.config_access import cfg_get
        game_root = cfg_get(get_ini_config(), "Settings", "game_root_dir").strip()
        if game_root:
            return os.path.expanduser(game_root)
    except Exception:
        pass
    return os.path.expanduser(default)


def get_themes_dir() -> Path:
    return ensure_config_dir() / THEMES_FOLDER
