"""Starting the frontend and stopping it again.

The order matters more than it looks: servers before windows, and every exit path
through `shutdown_services`, which is what writes the session's play data.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.capture import freeze
from common.config.config_access import DisplayConfig, NetworkConfig, SettingsConfig
from common.config.config_store import ConfigStore
from common.config.paths import THEMES_FOLDER
from common.devices import discovery, install_setup, serving
from common.games import remote_library
from common.host import key_reader, metrics, system_actions
from common.host.display_service import get_display_monitors
from common.players import player_pictures
from common.runtime import lifecycle, shutdown
from common.security import http_client
from common.settings import settings_waiting
from frontend import (
    ext_data,
    input_capture,
    input_events,
    library_resolver,
    play_events,
    preview,
    showing,
    sign_in_prompt,
)
from frontend.api import API
from frontend.chromium_manager import ChromiumManager
from frontend.device_channel import DeviceChannel


def window_configs(iniconfig: ConfigStore | None = None) -> list[tuple[str, str]]:
    """(window name, monitor key) for each window to open: the active theme's, and the topper
    when the cabinet has a screen for it.

    Derived rather than fixed: a theme names its own windows, and the default depends on
    which contract it was written against - which is what keeps index_table.html working
    without a fallback lookup.
    """
    from frontend import theme_windows

    return [(name, theme_windows.screen_key(name))
            for name in theme_windows.active(iniconfig)]


def _own_windows(iniconfig: ConfigStore) -> tuple[str, ...]:
    from frontend import theme_windows

    return theme_windows.own_windows(theme_windows.active(iniconfig))


def _served_windows(iniconfig: ConfigStore) -> frozenset[str]:
    """The windows that get an API under these settings: those with a screen set."""
    displays = DisplayConfig.from_config(iniconfig)
    return frozenset(name for name, key in window_configs(iniconfig)
                     if displays.window_screen_id(key).strip())


def register_window_apis(iniconfig: ConfigStore, ws_bridge: DeviceChannel,
                         frontend_browser: ChromiumManager, logger: logging.Logger,
                         library: library_resolver.LibraryResolver | None = None) -> None:
    """Give every window that has a screen an API, once. A window with none is refused at
    the socket, so this runs again whenever the windows open: Setup and the Manager both
    assign screens after the process booted."""
    displays = DisplayConfig.from_config(iniconfig)
    registered = ws_bridge.registered_windows()
    if library is None:
        library = next((api.library for api in ws_bridge.apis()), None)
    if library is None:
        library = library_resolver.LibraryResolver(iniconfig)
    for window_name, config_key in window_configs(iniconfig):
        if window_name in registered:
            continue
        if not displays.window_screen_id(config_key).strip():
            continue

        api = API(
            ini_config=iniconfig,
            window_name=window_name,
            ws_bridge=ws_bridge,
            frontend_browser=frontend_browser,
            library=library,
        )
        api._finish_setup()
        ws_bridge.register_api(window_name, api)
        logger.debug("Registered API for window '%s'", window_name)


def create_api_instances(iniconfig: ConfigStore,
                         logger: logging.Logger) -> tuple[DeviceChannel, ChromiumManager]:
    _apply_theme_media_sets(iniconfig, logger)
    settings_waiting.install(_own_windows, _served_windows)
    settings_waiting.record_start(iniconfig)
    network = NetworkConfig.from_config(iniconfig)
    ws_bridge = DeviceChannel(port=network.ws_port)
    serving.bound("ws_port", network.ws_port)
    frontend_browser = ChromiumManager()

    # One view, however many windows. They show the same library and the same selection,
    # and only the controller window takes input - so three copies were three derivations
    # of one answer, kept in step by everyone doing the same work.
    shared_library = library_resolver.LibraryResolver(iniconfig)

    # Nothing is announced to anybody here. An install says what it is on the network and
    # every other install decides what to do with that, so a machine that only launches
    # games carries no address for a machine that manages it.
    if network.library_url and network.verify_shared_library:
        threading.Thread(
            target=_report_shared_library, args=(shared_library, logger),
            daemon=True, name="verify-shared-library").start()

    register_window_apis(iniconfig, ws_bridge, frontend_browser, logger,
                         library=shared_library)

    # Once, against the shared bridge - not once per window, which would send
    # every launch message three times.
    play_events.register(ws_bridge, frontend_browser, iniconfig)
    input_events.register(ws_bridge)
    input_capture.register(ws_bridge)
    key_reader.register()
    metrics.register()
    freeze.register()
    showing.register(ws_bridge, shared_library)
    preview.register(ws_bridge)
    sign_in_prompt.register(ws_bridge)
    # What extensions add to an entry, fetched by core when the wheel stops. Given the
    # bridge's own send rather than a bridge of its own, so one answer reaches every
    # window as one message.
    ext_data.register(ws_bridge.send_event_all_with_iframe)

    return ws_bridge, frontend_browser


def _report_shared_library(shared_library: library_resolver.LibraryResolver,
                           logger: logging.Logger) -> None:
    """Say whether the library this install reads is the one on its own disk, once, at
    startup.

    Reports and does nothing else. A mismatch is a real problem - it is the difference
    between a wheel that launches and one that fails per game with a file-not-found - but
    what to do about it is a policy decision nobody has made, and guessing it here would
    make an unmounted share fatal on a machine that was working a moment ago.
    """
    from common.games.game_repository import all_games

    try:
        report = remote_library.verify_shared_library(shared_library.entries, all_games())
    except Exception:
        logger.debug("Could not verify the shared library", exc_info=True)
        return

    if report["shared"]:
        logger.info("Shared library verified: %s tables match", report["matched"])
        return
    logger.warning(
        "Shared library does not match: %s matched, %s missing here, %s differ, "
        "%s could not be vouched for. Launching may fail per game.",
        report["matched"], len(report["missing"]), len(report["differs"]),
        report["unverifiable"])


def _apply_theme_media_sets(iniconfig: ConfigStore, logger: logging.Logger) -> None:
    """Push the active theme's set choices down to media resolution.

    common/ cannot read theme.json (layering), so the frontend does it at boot,
    before the first scan, and again whenever the windows reopen. The ini's [Media]
    wheelset stays the default for anything the theme does not override.
    """
    try:
        from common.media.media_specs import set_media_set_override
        from frontend.theme_api import get_theme_config

        theme_config = get_theme_config(iniconfig) or {}
        wheelset = str(theme_config.get("wheelSet") or "").strip()
        # Cleared when the theme names none: a reopen can follow a change of theme, and
        # the last theme's choice would otherwise outlive it.
        set_media_set_override("wheel", wheelset or None)
        if wheelset:
            logger.info("Theme wheel set: %s", wheelset)
    except Exception:
        logger.exception("Could not apply the theme's media set choices")


def build_mount_points(base_path: str, config_dir: Path,
                       iniconfig: ConfigStore) -> dict[str, str]:
    themes_dir = str(config_dir / THEMES_FOLDER)
    collection_icons_dir = str(config_dir / "collection_icons")
    os.makedirs(themes_dir, exist_ok=True)
    os.makedirs(collection_icons_dir, exist_ok=True)
    player_pictures_dir = str(config_dir / player_pictures.FOLDER)
    os.makedirs(player_pictures_dir, exist_ok=True)
    core_dir = os.path.join(base_path, "frontend", "static")
    mount_points = {
        "/core/": core_dir,
        # What core provided was served at /web/ until 3.0, and installed themes ask for
        # vpinfe-core.js and vpinfe-style.css by that URL. Kept as an alias on the same
        # directory rather than a redirect: a theme that has not been updated keeps
        # working, and there is one copy of the files.
        "/web/": core_dir,
        "/themes/": themes_dir,
        "/collection_icons/": collection_icons_dir,
        player_pictures.ROUTE: player_pictures_dir,
    }
    settings = SettingsConfig.from_config(iniconfig)

    from common.media.manufacturer_logos import configure_manufacturer_logos, resolve_logos_dir

    logos_dir = resolve_logos_dir(settings.manufacturer_logos_dir, config_dir)
    os.makedirs(logos_dir, exist_ok=True)
    configure_manufacturer_logos(logos_dir)

    # Refresh the manufacturer reference from the cached VPSdb, off the boot
    # path - it exists for people reading it between runs, so boot is the one
    # guaranteed refresh even when no sync happens this session.
    def _refresh_reference() -> None:
        from common.config.paths import VPSDB_PATH
        from common.media.manufacturer_logos import (
            vps_manufacturer_names,
            write_manufacturer_reference,
        )

        names = vps_manufacturer_names(VPSDB_PATH)
        if names:
            write_manufacturer_reference(names)

    threading.Thread(target=_refresh_reference, daemon=True,
                     name="manufacturer-reference").start()
    return mount_points


def wait_for_server_ready(port: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            http_client.get_text(
                f"http://localhost:{serving.loopback_or(port)}/api/v1/health", timeout=1)
            return
        except Exception:
            time.sleep(0.5)


SETUP_POLL_S = 0.5


def _wait_out_setup(iniconfig: ConfigStore, frontend_browser: ChromiumManager,
                    logger: logging.Logger) -> bool:
    """One window on the playfield screen, showing Setup, until it closes.

    True when Setup finished under it - the caller opens the real windows next, no
    restart. False when a person closed the window themselves - the caller quits, same
    as any other first start.
    """
    from common.config.paths import get_ini_config

    displays = DisplayConfig.from_config(iniconfig)
    screen_id = displays.playfield_screen_id
    monitors = get_display_monitors()
    monitor = monitors[screen_id] if screen_id < len(monitors) else monitors[0]
    logger.info("Setup pending: opening Setup in a chromium window.")
    frontend_browser.launch_window(
        window_name="table",
        url=f"http://localhost:{serving.own_port(iniconfig)}/?setup=window",
        monitor=monitor,
        index=0,
    )

    stop_polling = threading.Event()

    def _watch() -> None:
        while not stop_polling.wait(SETUP_POLL_S):
            if not install_setup.pending(get_ini_config()):
                frontend_browser.request_exit()
                return

    threading.Thread(target=_watch, daemon=True).start()
    frontend_browser.wait_for_exit()
    stop_polling.set()

    return not install_setup.pending(get_ini_config())


def open_windows(iniconfig: ConfigStore, frontend_browser: ChromiumManager,
                 shutdown_event: threading.Event,
                 is_window_connected: Callable[[str], bool] | None = None,
                 serve: Callable[[ConfigStore], None] | None = None,
                 keep_running: bool = False) -> bool:
    """Open the windows the settings describe and watch them. A window closed by hand
    ends the run, which is `shutdown_event`, unless `keep_running`: then the rest are
    closed and the run goes on. True when one is up. `serve` is told the settings first,
    to give any window they newly describe an API to answer from."""
    # Prime the monitor cache before any windows spawn. Each
    # window's JS calls api.get_monitors() on its own pywebview worker thread;
    # opening X11 display connections concurrently races in libXau and aborts the
    # process. Doing the one real X query here keeps those calls served from cache.
    get_display_monitors()

    if serve:
        serve(iniconfig)
    if keep_running:
        # A window closed by hand earlier set it, and its reset can come after this open.
        frontend_browser.reset_exit()
    settings_waiting.windows_opened(iniconfig)
    frontend_browser.launch_all_windows(iniconfig)
    frontend_browser.watch(
        is_window_connected=is_window_connected,
        on_lost=(lambda: _windows_lost(frontend_browser)) if keep_running
        else shutdown_event.set)
    return frontend_browser.has_windows


# Serializes everything that changes which windows are up, shutdown included: a reopen
# that is still launching when the run ends would otherwise leave its windows behind.
_reopening = threading.Lock()


def _windows_lost(frontend_browser: ChromiumManager) -> None:
    """A window was closed by hand and the watcher took the others down: that is Close.
    The watcher ends the wait for exit, which a later open has to find cleared."""
    with _reopening:
        frontend_browser.reset_exit()
        if not frontend_browser.has_windows:
            settings_waiting.windows_closed()


def close_windows(frontend_browser: ChromiumManager, logger: logging.Logger) -> None:
    """Close the windows and leave the run going. While Setup is pending the one window
    is Setup itself, and closing it is the person quitting, so nothing is closed."""
    from common.config.paths import get_ini_config

    with _reopening:
        if install_setup.pending(get_ini_config()):
            logger.info("Setup is still open; its window is not closed from here.")
            return
        frontend_browser.close_windows()
        settings_waiting.windows_closed()


def windows_state(frontend_browser: ChromiumManager) -> str:
    """One of `lifecycle.WINDOWS_*`."""
    from common.config.paths import get_ini_config
    from common.devices import install_identity

    if not install_identity.has_feature(get_ini_config(), install_identity.FRONTEND):
        return lifecycle.WINDOWS_OFF
    if install_setup.pending(get_ini_config()):
        return lifecycle.WINDOWS_SETUP
    # A reopen closes the windows before it opens them, and is not a time to offer Open.
    if _reopening.locked() or frontend_browser.has_windows:
        return lifecycle.WINDOWS_OPEN
    return lifecycle.WINDOWS_CLOSED


def reopen_windows(iniconfig: ConfigStore, frontend_browser: ChromiumManager,
                   ws_bridge: DeviceChannel, shutdown_event: threading.Event,
                   logger: logging.Logger, keep_running: bool = False) -> None:
    """Close the windows, if any are up, and open them again on the settings as they are
    now. The same call opens windows that were closed.

    The Manager writes through a store of its own, so what this process booted with is
    read again first - the screens, rotation, theme and Chromium settings all come from
    it, and the windows' own API answers from it too. The theme's wheel set is applied
    again, and a library resolved under another set or variant is read once more.
    """
    from common.config.paths import get_ini_config
    from common.games import game_repository, play_numbers

    with _reopening:
        if shutdown_event.is_set() or shutdown.requested():
            return
        if install_setup.pending(get_ini_config()):
            logger.info("Setup is still open; the windows are not reopened under it.")
            return
        frontend_browser.close_windows()
        iniconfig.reread()
        _apply_theme_media_sets(iniconfig, logger)
        if game_repository.reread_media_settings() | play_numbers.changed_since_built():
            # The shared view still holds the games it resolved before. The first window
            # to ask rebuilds it, as it does after any change to the library.
            for api in ws_bridge.apis():
                api.library.mark_stale()
        # The reread above can be slow, and a quit can arrive during it.
        if shutdown_event.is_set() or shutdown.requested():
            return
        if not open_windows(
                iniconfig, frontend_browser, shutdown_event, ws_bridge.is_window_connected,
                serve=lambda ini: register_window_apis(ini, ws_bridge, frontend_browser, logger),
                keep_running=keep_running):
            logger.warning("No window opened; VPinFE keeps running without them.")


def _wait_for(shutdown_event: threading.Event) -> None:
    while not shutdown_event.is_set():
        shutdown_event.wait(0.2)


def run_frontend_loop(headless: bool, iniconfig: ConfigStore,
                      frontend_browser: ChromiumManager,
                      shutdown_event: threading.Event, logger: logging.Logger,
                      is_window_connected: Callable[[str], bool] | None = None,
                      serve: Callable[[ConfigStore], None] | None = None) -> None:
    # Only headless used to handle a signal, so killing the windowed frontend died where
    # it stood and skipped shutdown_services - the play data never reached VPinPlay, and
    # Chromium runs in its own session, so its windows outlived us on screen.
    def _request_exit() -> None:
        shutdown_event.set()
        # Setup's wait is on the browser rather than on the event.
        frontend_browser.request_exit()

    shutdown.handle_termination(shutdown_event.set if headless else _request_exit)

    # Asked to quit while starting up. The windows were never opened, so this is the
    # whole frontend lifetime - return and let the caller stop the services.
    if shutdown.requested():
        logger.info("Shutdown was requested during startup; not opening the frontend.")
        return

    if headless:
        logger.info("Headless mode: servers running without Chromium frontend")
        logger.info("Press Ctrl+C to stop...")

        try:
            _wait_for(shutdown_event)
        except KeyboardInterrupt:
            shutdown_event.set()
        logger.info("Shutting down...")
        # Windows opened since the start would otherwise outlive the run.
        with _reopening:
            frontend_browser.terminate_all()
        return

    if install_setup.pending(iniconfig):
        if not _wait_out_setup(iniconfig, frontend_browser, logger):
            frontend_browser.terminate_all()
            return
        frontend_browser.terminate_all()
        frontend_browser.reset_exit()
        # A signal in the gap above already set the flag the reset just cleared.
        if shutdown.requested():
            return
        # Setup wrote through the Manager's own store, a different instance of this
        # same file - re-read rather than trust what booted this process.
        from common.config.paths import get_ini_config
        iniconfig = get_ini_config()

    # No window up at all ends the run. Once there is one, the run belongs to the event,
    # as a headless one does, and the windows come and go under it.
    if open_windows(iniconfig, frontend_browser, shutdown_event, is_window_connected,
                    serve=serve):
        _wait_for(shutdown_event)
    # A no-op when a closed window already took the others down with it; the work
    # is for the signal, which leaves the windows up.
    with _reopening:
        frontend_browser.terminate_all()


def shutdown_services(logger: logging.Logger, *, iniconfig: ConfigStore,
                      ws_bridge: DeviceChannel, stop_dof: Callable[[], Any],
                      stop_dmd: Callable[..., Any],
                      nicegui_app: Any, stop_server: Callable[[], Any]) -> None:
    logger.info("Shutting down services...")
    for label, action in (
        ("ws_bridge.stop", ws_bridge.stop),
        ("stop_dof_service", stop_dof),
        ("stop_libdmdutil_service", lambda: stop_dmd(clear=False)),
        # Before the sockets go: this is what sends the goodbye packet, and a peer that
        # never gets one has to wait out a timeout to notice the machine is gone.
        ("stop_discovery", discovery.stop),
        ("nicegui_app.shutdown", nicegui_app.shutdown),
        ("stop_server", stop_server),
    ):
        try:
            action()
        except Exception:
            logger.exception("%s() error", label)
    logger.info("All services stopped.")


def restart_if_requested(logger: logging.Logger) -> None:
    system_actions.restart_if_requested(logger)
