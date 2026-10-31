#!/usr/bin/env python3
"""Starting VPinFE.

Reads mostly top to bottom, and the order is the point: config before anything
under common/ is imported, servers before windows, and the shutdown at the end is
the one path every exit goes through.
"""

from __future__ import annotations

import multiprocessing
import os
import platform
import sys

from common.config.config_access import NetworkConfig

multiprocessing.freeze_support()

# On Windows, hide the terminal window when launched via icon (not from terminal).
# When double-clicked, the process is the sole owner of its terminal.
# When run from cmd/powershell, multiple processes share the terminal - don't hide it.
if platform.system() == "Windows" and getattr(sys, 'frozen', False):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

if "--dof-helper" in sys.argv[1:]:
    from common.host.dof_service_worker import main as _dof_helper_main
    raise SystemExit(_dof_helper_main())

if "--dof-pull" in sys.argv[1:]:
    # `--dof-pull <script> <its arguments>`: the config downloader run by the frozen build,
    # which has no other Python to hand it to.
    import os
    import runpy

    _pull = sys.argv[sys.argv.index("--dof-pull") + 1:]
    sys.argv = _pull
    sys.path.insert(0, os.path.dirname(os.path.abspath(_pull[0])))
    runpy.run_path(_pull[0], run_name="__main__")
    raise SystemExit(0)

if "--pinmame-helper" in sys.argv[1:]:
    from common.host.pinmame_worker import main as _pinmame_helper_main
    sys.argv.remove("--pinmame-helper")
    raise SystemExit(_pinmame_helper_main())


# common.config.paths resolves CONFIG_DIR at import time, so --configdir has to reach
# the environment before anything under common/ is imported. Do it first.
from common.config_bootstrap import apply_configdir_override

apply_configdir_override(sys.argv[1:])

import requests

# device_migration and the games modules below register the seeds the 2.x conversion runs,
# so they have to be in before the ConfigStore is made.
from common import events, extensions, i18n
from common.config.config_store import ConfigStore
from common.config.paths import (
    LOG_DIR,
    THEMES_DIR,
    VPINFE_INI_PATH,
    configure_nicegui_storage,
    ensure_config_dir,
)
from common.devices import (
    device_migration,  # noqa: F401
    install_setup,
    port_check,
    serving,
)
from common.games import launcher_migration, launchers, library_folders  # noqa: F401
from common.host import this_machine
from common.host.dof_service import start_dof_service_if_enabled, stop_dof_service
from common.host.libdmdutil_service import (
    stop_libdmdutil_service,
)
from common.online import theme_seed, theme_upgrade  # noqa: F401
from common.online.pinmame_score_parser_updater import ensure_latest_roms_json
from common.online.theme_registry_client import ThemeRegistryError
from common.online.themes import ThemeRegistry
from common.release import migration_ledger, whats_new
from common.runtime import lifecycle, shutdown
from common.runtime.log_setup import configure_logging, get_logger
from common.security import sign_in
from common.security.http_client import unreachable
from common.settings import theme_options
from common.vpinfe_version import get_version

# Get the base path
base_path = os.path.dirname(os.path.abspath(__file__))

# Load config BEFORE importing cli/manager (they create ConfigStore at import time)
config_dir = ensure_config_dir()
nicegui_storage_path = configure_nicegui_storage()
log_path = configure_logging(LOG_DIR, enable_file=False)
config_store = ConfigStore(str(VPINFE_INI_PATH))
log_path = configure_logging(LOG_DIR, config_store)
logger = get_logger("vpinfe.main")

# Before anything builds a label. Every registry resolves through the catalog at
# attribute access, so this only has to happen ahead of the first page, not ahead of the
# imports - but there is no reason to leave it later than the config that answers it.
spoken = i18n.set_language(config_store.value("general", "language"))
logger.info("Logging to %s", log_path)
logger.info("Language: %s (%s)", spoken, " -> ".join(i18n.chain()))
logger.info("Using NiceGUI storage path: %s", nicegui_storage_path)
logger.info("Version: %s", get_version())
whats_new.note_start(config_store, get_version())

# Startup downloads themes, walks the library and rewrites .info files, and it can take
# a while on a big one. Start listening for a kill now; the checks below act on it at
# the step boundaries, so nothing gets killed halfway through writing.
shutdown.watch_during_startup()

try:
    roms_update_result = ensure_latest_roms_json()
    logger.info(
        "pinmame-score-parser roms.json status=%s path=%s",
        roms_update_result.get("status"),
        roms_update_result.get("path"),
    )
except requests.RequestException as exc:
    unreachable(logger, "Could not update pinmame-score-parser roms.json at startup", exc)
except Exception:
    logger.exception("Failed to update pinmame-score-parser roms.json at startup")

shutdown.exit_if_requested(logger)


def reconfigure_app_logging() -> None:
    configure_logging(LOG_DIR, config_store)

# Now safe to import modules that create their own ConfigStore at import time
from nicegui import app as nicegui_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import httpapi
import manager
from cli import parse_args
from frontend import library_art, library_resolver, lifecycle_host, runtime, theme_routes
from manager import app_control
from manager.host import _shutdown_event, start_server, stop_server
from manager.security import sign_in_notice
from manager.security.origin_guard import SocketOriginGuard
from manager.security.sign_in_gate import SignInGate

nicegui_app.add_static_files('/static', os.path.join(base_path, 'manager/static'))


class _SuppressNoResponseReturnedMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except RuntimeError as exc:
            if str(exc) == "No response returned.":
                # Harmless client disconnect race in Starlette/NiceGUI middleware chain.
                return Response(status_code=204)
            raise


nicegui_app.add_middleware(_SuppressNoResponseReturnedMiddleware)
nicegui_app.add_middleware(SignInGate)
nicegui_app.add_middleware(SocketOriginGuard)

# Load what this install has installed, before the API is built: the API mounts the
# routers extensions registered, so one loading afterwards would answer nothing.
extensions.load_installed()

# Mount the HTTP API. Has to happen before any ui.run(), including the early
# first-run start below.
httpapi.register(nicegui_app)

# And say so on the network, once the routes an announcement points at exist. Here rather
# than inside the app builder: a test builds that app many times over, and each build
# would be another machine as far as anyone listening is concerned.
httpapi.instance.announce_on_the_network()

# The Manager.
manager.register()

# On Windows, the Proactor event loop logs a noisy ConnectionResetError (WinError 10054)
# whenever a browser tab is closed mid-connection. Install a startup handler that
# silently drops those and forwards everything else to the default handler.
if sys.platform == "win32":
    import asyncio as _asyncio
    _windows_logger = get_logger("vpinfe.windows")

    @nicegui_app.on_startup
    async def _suppress_proactor_connection_reset() -> None:
        loop = _asyncio.get_running_loop()
        _default = loop.get_exception_handler()

        def _handler(loop: _asyncio.AbstractEventLoop, ctx: dict) -> None:
            exc = ctx.get("exception")
            if isinstance(exc, ConnectionResetError):
                _windows_logger.debug(str(exc))
                return  # swallow WinError 10054 noise from browser disconnects
            if _default is not None:
                _default(loop, ctx)
            else:
                loop.default_exception_handler(ctx)

        loop.set_exception_handler(_handler)

# Shared instances accessible from other modules (e.g. remote.py)
ws_bridge = None
frontend_browser = None


def create_api_instances():
    """Create API instances for each configured display window."""
    global ws_bridge, frontend_browser
    ws_bridge, frontend_browser = runtime.create_api_instances(config_store, logger)

    def open_windows() -> None:
        runtime.reopen_windows(config_store, frontend_browser, ws_bridge,
                               _shutdown_event, logger, keep_running=bool(headless))

    def close_windows() -> None:
        runtime.close_windows(frontend_browser, logger)

    # Now that there is a browser to stop, this process can say what it can do. Headless is
    # how this run started: its windows can still be opened, and closing them, by hand or
    # with the frontend's Exit, does not end it.
    lifecycle_host.install(
        config_store=config_store,
        frontend_browser=frontend_browser,
        shutdown_event=_shutdown_event,
        ws_bridge=ws_bridge,
        open_windows=open_windows,
        close_windows=close_windows,
        windows_state=lambda: runtime.windows_state(frontend_browser),
        exit_closes_windows=bool(headless),
    )
    app_control.install_notices()


cli_args = parse_args() if len(sys.argv) > 0 else None
headless = cli_args and cli_args.headless


def _a_screen_can_show_a_code() -> bool:
    """False on any failure: a code printed with nobody to press Show is better than a
    request stranded waiting."""
    try:
        opened = frontend_browser is not None \
            and runtime.windows_state(frontend_browser) == lifecycle.WINDOWS_OPEN
        return opened or sign_in_notice.watching()
    except Exception:  # noqa: BLE001
        logger.exception("Could not tell whether a screen can show a sign-in code")
        return False


sign_in.instance().screen = _a_screen_can_show_a_code
this_machine.warm()

# Register frontend theme assets before NiceGUI can start on the first-run path.
mount_points = runtime.build_mount_points(base_path, config_dir, config_store)
theme_routes.register(nicegui_app, mount_points)
library_art.register(nicegui_app, lambda: library_resolver.library_url(config_store))

network = NetworkConfig.from_config(config_store)
port_check.require(network)

# While Setup is pending, start the web server early so chromium can load it.
if install_setup.pending(config_store):
    start_server(port=network.http_port, bind=network.http_bind)
    serving.publish()
    reconfigure_app_logging()
    # Wait for the NiceGUI server to be ready before chromium tries to load it
    runtime.wait_for_server_ready(network.http_port)
    logger.info("Setup pending: web server ready on port %s", network.http_port)

# Before anything installs or updates a theme. An update deletes the theme's folder, and
# until now that folder is where the user's option values were kept - so this has to run
# first or the release that fixes that data loss is the release that causes it one last
# time. Cheap and idempotent: it skips any theme that already has an entry in `theme_options`.
try:
    theme_options.migrate_from_packages(THEMES_DIR)
except Exception:
    logger.exception("Could not move theme options out of the installed packages")

# Initialize theme registry and auto-install default themes
try:
    theme_registry = ThemeRegistry()
    theme_registry.load_registry()
    theme_registry.load_theme_manifests(default_only=True, dates=False,
                                        include=theme_upgrade.wanted(theme_registry))
    if theme_upgrade.run(theme_registry):
        config_store.reread()
    theme_registry.auto_install_defaults()
except requests.RequestException as exc:
    unreachable(logger, "Theme registry initialization failed", exc)
except ThemeRegistryError as exc:
    logger.warning("Theme registry initialization failed: %s", exc)
except Exception:
    logger.exception("Theme registry initialization failed")

shutdown.exit_if_requested(logger)

# Give every game and every table a stable id. One-time cost per library; a no-op
# afterwards, and neither pass writes a .info it did not change.
unseen: list = []
try:
    from common.games.game_identity import ensure_unique_ids, game_id
    from common.games.game_repository import all_games
    from common.games.library_discovery import discover
    from common.games.table_identity import ensure_unique_table_ids
    games = all_games()
    unseen = [game for game in games if not game_id(game)]
    awaiting_upgrade = [game for game in games if game.info_pending_upgrade]
    ensure_unique_ids(games)
    # Before the minting pass, not after: discovery adds an entry for every .vpx the
    # folder holds, and minting is what turns those into addressable tables.
    discover(games)
    ensure_unique_table_ids(games)
    # After the ids, because a launcher assignment is recorded against a table and a
    # table has no id until the line above has run. It only writes the games that carried
    # an override, and takes the keys out as it reads them.
    launcher_migration.migrate_assignments(launchers.get_launcher_store(), games)
    launcher_migration.retire_table_pattern(config_store, games)
    if upgraded := sum(1 for game in awaiting_upgrade if not game.info_pending_upgrade):
        migration_ledger.record("info_upgrade", get_version(), upgraded)
except Exception:
    logger.exception("Id backfill failed; games or tables without an id are not addressable")

# Reading those tables is the expensive half - a parse hashes the whole .vpx - so it
# goes on a job and startup does not wait for it. Writes land on a background thread
# while requests are served, which is safe because persist_game_meta rebinds
# meta_config rather than mutating the dict a reader may be holding.
try:
    from common.games.library_refresh import read_at_startup
    from common.i18n import t
    from common.runtime import jobs
    jobs.submit(jobs.KIND_LIBRARY_SCAN,
                lambda job: read_at_startup(games, unseen, job.reporter()),
                title=t("said.title_startup_read"))
except Exception:
    logger.exception("Could not start library enrichment; unread tables stay unread")

try:
    from common.games import media_lens
    media_lens.read_ahead()
except Exception:
    logger.exception("Could not start reading the library's media ahead")

try:
    from common.games import table_lens
    table_lens.read_ahead()
except Exception:
    logger.exception("Could not start reading the library's tables ahead")

# And keep looking, if the user asked us to. Off by default: a check re-reads every
# game folder, which is nothing locally and real traffic on a share.
try:
    from common.config.config_access import cfg_int
    from common.games.library_refresh import start_periodic
    start_periodic(cfg_int(config_store, "updates", "refresh_minutes", 0))
except Exception:
    logger.exception("Could not start the periodic library refresh")

shutdown.exit_if_requested(logger)

# Collection membership moves onto game ids once the ids exist. Resolvable entries
# are rewritten; anything that does not resolve is left alone rather than dropped.
try:
    from common.config.paths import COLLECTIONS_PATH
    from common.games.collection_migration import (
        ensure_last_played,
        ensure_order_direction,
    )
    from common.games.collection_store import CollectionStore
    _collections = CollectionStore(str(COLLECTIONS_PATH))
    _collections.migrate_membership_to_game_ids(all_games())
    # After the rekey, so a file arriving as a 2.x ini is in its current shape first.
    if ensure_last_played(_collections):
        migration_ledger.record("last_played", get_version())
    if moved := ensure_order_direction(_collections):
        migration_ledger.record("collection_order", get_version(), moved)
except Exception:
    logger.exception("Collection membership migration failed; memberships left as they were")

shutdown.exit_if_requested(logger)

# The owner is the one player every install has, and the one up until somebody else is.
try:
    from common.config.paths import get_ini_config
    from common.players.players import get_roster
    get_roster().ensure_owner(get_ini_config())
except Exception:
    logger.exception("Could not make the owner; the roster has none until the next start")

# On macOS the Remote's Table Controls press keys, which macOS allows only after a
# dialog at the machine; ask now rather than on the first press from a phone.
try:
    from common.host import key_simulator

    key_simulator.ask_for_post_access()
except Exception:
    logger.exception("Could not ask macOS for the key permission")

shutdown.exit_if_requested(logger)

# The catalog everything VPS-shaped reads from - matching, release lists, what a kind is
# offered from.
def _start_sync_watchers() -> None:
    from common.online.dof_sync import start_watch as _watch_dof_config
    from common.online.sources_watch import start_watch as _watch_sources
    from common.online.theme_sync import start_watch as _watch_themes

    _watch_sources(config_store, _shutdown_event)
    _watch_themes(config_store, _shutdown_event)
    _watch_dof_config(config_store, _shutdown_event)


# While Setup is pending the watchers start when it finishes instead.
if install_setup.pending(config_store):
    install_setup.after_finish(config_store, _start_sync_watchers)
else:
    _start_sync_watchers()

# Whatever a person asked to run when VPinFE starts, and anything a previous run left
# half done. Ahead of the services below because that is what these are usually for -
# a share mounted, a service stopped, an audio route moved - and everything after this
# line is entitled to assume it has happened.
try:
    from common.host import vpinfe_commands

    vpinfe_commands.on_start(config_store)
except Exception:
    logger.exception("Commands set to run when VPinFE starts did not all work")

# The Manager saves through a store of its own, so this one holds what the file said at
# startup until it is told to look again. First, so a subscriber that runs after it reads
# what was just saved.
events.subscribe(events.SETTINGS_CHANGED, lambda **_: config_store.reread())

# Feedback hardware follows game lifecycle events from here on, so both launch
# paths get the same behavior without either of them knowing about DOF.
from common.host import peripherals

peripherals.register()

start_dof_service_if_enabled(config_store)

# Create API instances and register with WebSocket bridge
create_api_instances()

# Start the NiceGUI HTTP server
start_server(port=network.http_port, bind=network.http_bind)
serving.publish()
reconfigure_app_logging()

# A request left by a run that was killed first must not turn this run's exit into going back.
try:
    from common.backups import going_back
    going_back.clear(config_dir)
except Exception:
    logger.exception("A leftover request to go back to 2.x was not cleared")

try:
    from common.games import community_lists
    from manager.kit.api import ApiClient
    community_lists.start_periodic(lambda path: ApiClient().ext_get(path))
except Exception:
    logger.exception("Could not start reading the Community lists that tag or rank")

# Start the WebSocket bridge
ws_bridge.start()

runtime.run_frontend_loop(
    headless,
    config_store,
    frontend_browser,
    _shutdown_event,
    logger,
    is_window_connected=ws_bridge.is_window_connected,
    serve=lambda ini: runtime.register_window_apis(ini, ws_bridge, frontend_browser, logger),
)

# Shutdown items - wrap each in try/except so restart check always runs
runtime.shutdown_services(
    logger,
    iniconfig=config_store,
    ws_bridge=ws_bridge,
    stop_dof=stop_dof_service,
    stop_dmd=stop_libdmdutil_service,
    nicegui_app=nicegui_app,
    stop_server=stop_server,
)

# Restores of the config folder's files wait for here, where nothing is left to write them.
try:
    from common.backups import pending as pending_restores
    pending_restores.apply(config_dir)
except Exception:
    logger.exception("Restores waiting on a close were not all carried out")

# Before the exit commands, which may unmount the library these write to.
try:
    from common.backups import going_back
    going_back.carry_out(config_dir)
except Exception:
    logger.exception("Going back to 2.x was not carried out")

# Last, so a command that puts the machine back runs after everything it might be
# putting back has actually stopped. Before the restart check, because a restart is
# another run and the pair has to close before the next one opens.
try:
    vpinfe_commands.on_exit(config_store)
except Exception:
    logger.exception("Commands set to run when VPinFE exits did not all work")

# Check for a restart request
runtime.restart_if_requested(logger)
