"""The command-line interface: the flags, and what each one runs."""

import argparse
import os
import sys

from screeninfo import get_monitors

from common.config.config_store import ConfigStore
from common.config.paths import VPINFE_INI_PATH, ensure_config_dir
from common.deprecations import announce
from common.devices import port_check, serving
from common.games import game_report_service, info_maintenance, metadata_service, revert_3x
from common.runtime.log_setup import get_logger

logger = get_logger("vpinfe.cli")

# Initialize config
ensure_config_dir()
logger.info("Using config file at: %s", VPINFE_INI_PATH)
config_store = ConfigStore(str(VPINFE_INI_PATH))


def build_metadata(download_media: bool = True, update_all: bool = True,
                   game_name: str | None = None, user_media: bool = False,
                   progress_cb=None, log_cb=None):
    return metadata_service.build_metadata(
        download_media=download_media,
        update_all=update_all,
        game_name=game_name,
        user_media=user_media,
        progress_cb=progress_cb,
        log_cb=log_cb,
        iniconfig=config_store,
    )


def upgrade_info_files(game_name: str | None = None, progress_cb=None, log_cb=None):
    from common.games import game_service

    return info_maintenance.upgrade_library(
        game_name=game_name, progress_cb=progress_cb, log_cb=log_cb,
        route_match=game_service.route_legacy_match)


def restore_info_files(game_name: str | None = None, progress_cb=None, log_cb=None):
    from common.config.paths import CONFIG_DIR

    return info_maintenance.restore_library(
        game_name=game_name, config_dir=CONFIG_DIR,
        progress_cb=progress_cb, log_cb=log_cb)


def reset_3x_state(config_only: bool = False, dry_run: bool = False,
                   progress_cb=None, log_cb=None):
    from common.config.config_access import NetworkConfig
    from common.config.paths import CONFIG_DIR, STATE_DIR

    return revert_3x.reset(
        None, CONFIG_DIR, state_dir=STATE_DIR,
        http_port=NetworkConfig.from_config(config_store).http_port,
        config_only=config_only, dry_run=dry_run,
        progress_cb=progress_cb, log_cb=log_cb)


def list_missing_games():
    return game_report_service.list_missing_games(iniconfig=config_store, log=logger.info)


def list_unknown_games():
    return game_report_service.list_unknown_games(iniconfig=config_store, log=logger.info)


def gamepadtest():
    """Run the gamepad test window using Chromium and a server of its own: the page needs
    core's files, not the library, and the real one may be running."""
    import socket
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI

    from common.config.config_access import NetworkConfig
    from frontend import lifecycle_host
    from frontend.api import API
    from frontend.chromium_manager import ChromiumManager
    from frontend.device_channel import DeviceChannel
    from frontend.theme_routes import register

    mount_points = {
        '/core/': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'static'),
    }
    network = NetworkConfig.from_config(config_store)
    port_check.require(network, ('ws_port',))
    ws_port = network.ws_port
    page_app = FastAPI()
    register(page_app, mount_points)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        http_port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(page_app, host='127.0.0.1', port=http_port,
                                           log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    serving.bound("http_port", http_port)

    monitors = get_monitors()
    ws_bridge = DeviceChannel(port=ws_port)
    chromium = ChromiumManager()
    api = API(
        ini_config=config_store,
        window_name="gamepad",
        ws_bridge=ws_bridge,
        frontend_browser=chromium,
    )
    api._finish_setup()
    ws_bridge.register_api("gamepad", api)
    lifecycle_host.install(config_store=config_store, frontend_browser=chromium,
                           shutdown_event=threading.Event(), ws_bridge=ws_bridge)

    ws_bridge.start()
    try:
        url = f"http://127.0.0.1:{http_port}/core/diag/gamepad.html?window=gamepad"
        chromium.launch_window("gamepad", url, monitors[0], 0)
        chromium.wait_for_exit()
    finally:
        ws_bridge.stop()
        server.should_exit = True


def check_extension(folder: str) -> int:
    """Print what the extension in `folder` declares and reads. 1 where it would not pass."""
    from common.extensions import check
    from httpapi import extension_ops

    extension_ops.offer_all()
    lines, passed = check.check(folder)
    print("\n".join(lines))
    return 0 if passed else 1


def support_package(folder: str) -> int:
    """Build the support package, print what is in it, and write the zip with every file
    unzipped beside it. 1 where it could not be built or written."""
    from pathlib import Path

    from common import i18n
    from common.failures import why
    from common.support import package

    try:
        made = package.build_package()
    except package.PackageTooLargeError:
        print(i18n.t("support.cli.too_large"))
        return 1
    target = Path(folder)
    unzipped = target / made.filename.removesuffix(".zip")
    try:
        unzipped.mkdir(parents=True, exist_ok=True)
        (target / made.filename).write_bytes(made.data)
        for part in made.parts:
            (unzipped / part.name).write_bytes(part.text.encode("utf-8"))
    except OSError as failure:
        print(i18n.t("support.cli.not_written", folder=str(target)))
        print(why(failure, target))
        return 1
    print(made.summary)
    readme = made.part(package.README)
    print(readme.text if readme else "")
    print(i18n.t("support.cli.zip", path=str(target / made.filename),
                 size=i18n.size(len(made.data))))
    print(i18n.t("support.cli.folder", path=str(unzipped)))
    return 0


def parse_args():
    """Parse and dispatch command-line arguments."""
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--version", action="store_true", help="Show the app version")
    parser.add_argument("--listres", action="store_true",
                        help="List your screens, with the number, name, position and size "
                             "of each")
    parser.add_argument("--check-extension", metavar="DIR",
                        help="Check the extension in the folder DIR the way VPinFE would "
                             "load it, and report what it declares, what it reads and any "
                             "on-screen word it has no entry for. Nothing is sent, saved "
                             "or run")
    parser.add_argument("--support-package", nargs="?", const=".", metavar="DIR",
                        help="Gather your system report and logs into a zip in the folder "
                             "DIR, or here, with private details replaced and every file "
                             "unzipped beside it so you can read it before you send it. "
                             "Nothing is sent anywhere")
    parser.add_argument("--listmissing", action="store_true",
                        help="List the games the Virtual Pinball Spreadsheet has that "
                             "your library does not")
    parser.add_argument("--listunknown", action="store_true",
                        help="List the games in your library that VPinFE cannot match "
                             "to the Virtual Pinball Spreadsheet")
    parser.add_argument("--configdir", metavar="DIR",
                        help="Keep your settings in the folder DIR instead of the usual "
                             "place for your system, with the cache in DIR/cache, the "
                             "state in DIR/state and the logs in DIR/logs. Setting "
                             "VPINFE_CONFIG_DIR to DIR does the same")
    parser.add_argument("--buildmeta", action="store_true",
                        help="Build the .info file, which holds a game's details, in each "
                             "game folder")
    parser.add_argument("--gamepadtest", action="store_true",
                        help="Open the gamepad button tester")
    parser.add_argument("--headless", action="store_true",
                        help="Run VPinFE's servers and services only, with no frontend "
                             "windows")
    parser.add_argument("--claim-user-media", action="store_true",
                        help="Deprecated and does nothing; your own media is "
                             "protected automatically")

    # Secondary args
    parser.add_argument("--no-media", action="store_true",
                        help="Do not download images when building the .info files")
    parser.add_argument("--update-all", action="store_true",
                        help="Read every game again when building the .info files")
    parser.add_argument("--user-media", action="store_true",
                        help="With --buildmeta: download nothing from VPinMediaDB and "
                             "use only the media you supply")
    parser.add_argument("--upgrade-info", action="store_true",
                        help="Upgrade every game's .info file to the current format, "
                             "backing up each one first. Normally done automatically "
                             "at startup; use this to finish an upgrade that was "
                             "interrupted")
    parser.add_argument("--restore-info", action="store_true",
                        help="Put back the .info files saved before they were "
                             "upgraded, for every game that has one. Your current "
                             ".info is kept first")
    parser.add_argument("--reset-3x-state", action="store_true",
                        help="Remove the state 3.x wrote so the next start migrates "
                             "from scratch: its settings and collections files, the "
                             ".info files it made, and every backup copy it kept. "
                             "Settings you changed under 3.x are lost, and so is any "
                             "rating or play count on a game 3.x added. Refuses while "
                             "VPinFE is running")
    parser.add_argument("--config-only", action="store_true",
                        help="With --reset-3x-state: reset the config directory and "
                             "leave the game library alone")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --reset-3x-state: list what would be removed and "
                             "change nothing")
    # --table is the pre-3.0 spelling, kept working and hidden from --help so the
    # documented flag is the current one. A user's script does not break.
    parser.add_argument("--game", "--table", dest="game",
                        help="Do just the one game folder with this name, for "
                             "--buildmeta, --upgrade-info or --restore-info")

    args, unknown = parser.parse_known_args()  # macOS-friendly parsing

    # argparse does not report which spelling reached it, so ask argv directly.
    if any(a == "--table" or a.startswith("--table=") for a in sys.argv[1:]):
        announce("cli-game-flag", "--table")

    if unknown:
        parser.error(f"Unknown arguments: {' '.join(unknown)}")

    if args.version:
        sys.exit(0)

    if args.listres:
        monitors = get_monitors()
        logger.info("screeninfo monitors:")
        for i, m in enumerate(monitors):
            logger.info({
                'ID': f'Monitor {i}',
                'output': m.name,
                'x': m.x,
                'y': m.y,
                'width': m.width,
                'height': m.height,
            })
        if sys.platform == "darwin":
            from frontend.chromium_manager import get_mac_screens
            logger.info("NSScreen monitors (used for window positioning on macOS):")
            for i, s in enumerate(get_mac_screens()):
                logger.info({
                    'ID': f'Screen {i}',
                    'x': s.x,
                    'y': s.y,
                    'width': s.width,
                    'height': s.height,
                })
        sys.exit()

    if args.check_extension:
        sys.exit(check_extension(args.check_extension))

    if args.support_package is not None:
        sys.exit(support_package(args.support_package))

    if args.listmissing:
        list_missing_games()
        sys.exit()

    if args.listunknown:
        list_unknown_games()
        sys.exit()

    # Kept as a stub because it shipped in 2.x and somebody has it in a script. It
    # marked media as yours so vpinmediadb would not overwrite it; the downloader now
    # proves ownership by hash before touching anything, so there is nothing to mark.
    if args.claim_user_media:
        logger.info("--claim-user-media is deprecated and does nothing. Media that is "
                    "not vpinmediadb's is left alone automatically; no need to claim it.")
        sys.exit()

    if args.upgrade_info:
        upgrade_info_files(game_name=args.game)
        sys.exit()

    if args.restore_info:
        restore_info_files(game_name=args.game)
        sys.exit()

    if args.reset_3x_state:
        try:
            reset_3x_state(config_only=args.config_only, dry_run=args.dry_run)
        except revert_3x.InstanceRunningError as exc:
            logger.error("%s", exc)
            sys.exit(1)
        sys.exit()

    if args.buildmeta:
        build_metadata(download_media=not args.no_media, update_all=args.update_all,
                       game_name=args.game, user_media=args.user_media)
        sys.exit()

    if args.gamepadtest:
        gamepadtest()
        sys.exit()

    return args
