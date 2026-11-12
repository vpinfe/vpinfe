"""
Chromium process manager for launching and managing embedded Chromium instances.

Based on the working vpinfe-chrome-test/embchrometest.py prototype.
Each configured display gets its own Chromium process in --app mode,
positioned on the correct monitor with fullscreen.
"""

from __future__ import annotations

import logging
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import namedtuple
from collections.abc import Callable, Iterable
from shutil import which
from typing import IO, Any, NamedTuple
from urllib.parse import quote, urlparse

from common import i18n
from common.config.config_access import DisplayConfig, NetworkConfig, SettingsConfig, cfg_get
from common.config.config_store import ConfigStore
from common.config.paths import bundled
from common.devices import serving
from common.host import bundled_libraries
from common.runtime.log_setup import include_thirdparty_logs
from frontend import theme_api, theme_windows

logger = logging.getLogger("vpinfe.frontend.chromium_manager")


CHROMIUM_BASE_FLAGS = [
    "--kiosk",
    "--start-maximized",
    "--no-first-run",
    "--noerrdialogs",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--disable-restore-session-state",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--log-level=3",
    # Prevent throttling/freezing when window is not focused. This is
    # important for Linux when an external game takes focus and occludes
    # the frontend window.
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-media-suspend",
    "--disable-features=CalculateNativeWindowOcclusion,PreloadMediaEngagementData,MediaEngagementBypassAutoplayPolicies",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-gpu-process-crash-limit",
    "--ignore-gpu-blocklist",
    # Fix for Gamepad API losing connection after focus switch on Linux.
    # Allows re-accessing /dev/input devices and bypasses gesture requirements.
    "--no-sandbox",
    "--disable-gpu-sandbox",
    "--autoplay-policy=no-user-gesture-required",
    # Suppress "unsupported command-line flag" info bar warnings.
    "--test-type",
]

# Without this Chromium asks GNOME's keyring for a password, and a cabinet that logs in
# automatically has no one to answer the dialog.
if platform.system() == "Linux":
    CHROMIUM_BASE_FLAGS.append("--password-store=basic")

# On a Mac the same wait is the login keychain: Chromium looks for "Chromium Safe Storage"
# and stops on a dialog when it cannot be found or unlocked.
elif platform.system() == "Darwin":
    CHROMIUM_BASE_FLAGS.extend(["--use-mock-keychain", "--password-store=basic"])

# The windows with a high-DPI override setting, by canonical name.
OVERRIDE_SECTIONS = {"backglass": "windows.backglass", "scoreview": "windows.scoreview"}


def parse_additional_chromium_options(raw_options: str) -> list[str]:
    """Parse user-provided Chromium flags using shell-style quoting."""
    raw_options = (raw_options or "").strip()
    if not raw_options:
        return []
    return shlex.split(raw_options, comments=False, posix=True)


def parsed_or_none(raw_options: str, setting: str) -> list[str]:
    """The flags a setting asks for, or none of them.

    An unbalanced quote is a typo in a text field, and the cost of raising on one is
    the whole frontend: nothing above catches it, so every window goes and the user
    sees a black screen rather than a bad setting.
    """
    try:
        return parse_additional_chromium_options(raw_options)
    except ValueError as exc:
        logger.warning("Ignoring invalid %s value: %s", setting, exc)
        return []


def get_builtin_chromium_options(
    window_name: str = "<window>",
    url: str = "<url>",
    monitor: Any = None,
    user_data_dir: str = "<temp profile dir>",
    mute_audio: bool = False,
    include_default_options: bool = True,
    exclude_options: Iterable[str] | None = None,
) -> list[str]:
    """Return VPinFE-managed Chromium flags without the executable path."""
    x = getattr(monitor, "x", "<x>")
    y = getattr(monitor, "y", "<y>")
    width = getattr(monitor, "width", "<width>")
    height = getattr(monitor, "height", "<height>")
    options = [
        f"--app={url}",
        f"--window-name=vpinfe-{window_name}",
        f"--class=vpinfe-{window_name}",
        f"--window-position={x},{y}",
        f"--window-size={width},{height}",
        f"--user-data-dir={user_data_dir}",
    ]
    if include_default_options:
        excluded = {str(opt).split("=")[0] for opt in (exclude_options or [])}
        options.extend(
            flag for flag in CHROMIUM_BASE_FLAGS
            if flag.split("=")[0] not in excluded
        )
    if mute_audio:
        options.append("--mute-audio")
    # Without this the cabinet's browser reports whatever locale the OS was installed
    # with, which is not a thing VPinFE chose or a user can see. It reaches a theme as
    # `navigator.language` and decides what `toLocaleDateString` prints.
    options.append(f"--lang={i18n.language()}")
    return options


class OptionLine(NamedTuple):
    """One option a window is started with, and where it came from.

    `source` is "window" for what places each window, "default" for VPinFE's own and
    "yours" for an added one. `replaced` marks a window or default line that an added
    option of the same name follows - Chrome is still given it, and the later one wins.
    `merged` is the added feature names carried in VPinFE's `--disable-features` or
    `--enable-features` line. `places` is a window option that decides where and which
    window it is, which is what breaks when an added option replaces it.
    """

    text: str
    source: str
    replaced: bool = False
    merged: tuple[str, ...] = ()
    places: bool = False


WINDOW_PLACEMENT = ("--app", "--window-name", "--class", "--window-position",
                    "--window-size", "--user-data-dir")


# Chrome keeps only the last of these, so an added one would switch VPinFE's own off.
FEATURE_OPTIONS = ("--disable-features", "--enable-features")


def option_name(option: str) -> str:
    return option.split("=")[0]


def option_lines(builtin: Iterable[str], added: Iterable[str]) -> list[OptionLine]:
    """The command line a window gets, in order, with where each option came from.

    An added `--disable-features` or `--enable-features` is folded into VPinFE's line of
    that name; any other added option follows the built-in ones, and wins over a built-in
    option of its name by being last.
    """
    builtin, added = list(builtin), list(added)
    base = {option_name(flag) for flag in CHROMIUM_BASE_FLAGS}
    theirs = {option_name(option) for option in added}
    folded: dict[str, list[str]] = {}
    for name in FEATURE_OPTIONS:
        if any(option_name(flag) == name for flag in builtin):
            folded[name] = [feature for option in added if option_name(option) == name
                            for feature in option.partition("=")[2].split(",") if feature]

    lines: list[OptionLine] = []
    for flag in builtin:
        name = option_name(flag)
        source = "default" if name in base else "window"
        if name in folded:
            own = set(flag.partition("=")[2].split(","))
            new = tuple(dict.fromkeys(f for f in folded[name] if f not in own))
            text = f"{flag},{','.join(new)}" if new else flag
            lines.append(OptionLine(text, source, merged=new))
        else:
            lines.append(OptionLine(flag, source, replaced=name in theirs,
                                    places=name in WINDOW_PLACEMENT))
    lines.extend(OptionLine(option, "yours") for option in added
                 if option_name(option) not in folded)
    return lines


class LibraryEndpoint(NamedTuple):
    """Where a page dials for the library, and where it dials for this device."""

    host: str
    port: int
    device_port: int


def _library_endpoint(network: NetworkConfig) -> LibraryEndpoint:
    """Resolve `network.library_url` into the addresses a window is launched with.

    With no library set the host is "" and both ports are this install's, which is every
    single-device setup. With one, the library's port is the one in its url and this
    device's own stays `network.http_port`: reading a library on another port would
    otherwise dial our own number at the other device. The library serves its art on that
    same port.
    """
    trimmed = str(getattr(network, "library_url", "") or "").strip()
    own_api = network.http_port
    if not trimmed:
        here = serving.loopback_or(own_api)
        return LibraryEndpoint("", here, here)

    parsed = urlparse(trimmed)
    return LibraryEndpoint(parsed.hostname or "", parsed.port or own_api, own_api)


def _build_window_url(
    base_url: str,
    theme_assets_port: int,
    theme_name: str,
    window_name: str,
    splash_enabled: bool,
    ws_port: int = NetworkConfig.ws_port,
    http_port: int = NetworkConfig.http_port,
    library_host: str = "",
    device_port: int = NetworkConfig.http_port,
    page: str = "",
) -> str:
    """Where a window opens, and how it finds the services.

    `page` is the path under the server root of a page that is not the theme's own
    `index_<window>.html`: core's, for a window the theme ships no page for.

    The ports travel in the url because they are what the page cannot ask for:
    everything else comes over the bridge, and asking needs the bridge port. Without
    them they are settings the browser ignores, and moving a service leaves the frontend
    dialling the old port forever.

    Built once and appended to all three url forms - stated per form, the frontend
    asserts one machine in six places.
    """
    endpoints = (f"wsPort={ws_port}&themeAssetsPort={theme_assets_port}"
                 f"&libraryPort={http_port}")
    # Only when the library is elsewhere. Absent, the page keeps assuming loopback and
    # one port for both, which is right for every install that serves its own library.
    if library_host:
        endpoints += (f"&libraryHost={quote(library_host, safe='')}"
                      f"&devicePort={device_port}")

    if platform.system() == "Linux":
        return f"{base_url}:{theme_assets_port}/app/{window_name}?{endpoints}"

    if splash_enabled:
        return (f"{base_url}:{theme_assets_port}/core/splash.html"
                f"?window={window_name}&{endpoints}")

    page = page or f"themes/{quote(theme_name, safe='')}/index_{window_name}.html"
    return f"{base_url}:{theme_assets_port}/{page}?window={window_name}&{endpoints}"


def resource_path(relative_path: str) -> str:
    """Kept as the name three call sites below use; `common.config.paths.bundled` is the answer."""
    return str(bundled(*relative_path.split("/")))


ChromiumPath = namedtuple("ChromiumPath", ["path", "using_local_install"])


class BrowserNotFoundError(FileNotFoundError):
    """No browser at the path a window would open with."""


def configured_browser_path() -> str:
    """The Browser Program setting, or "" when the browser is to be found."""
    try:
        from common.config.paths import get_ini_config

        return cfg_get(get_ini_config(), "chromium", "path", "").strip()
    except Exception:  # noqa: BLE001 - no config readable means nothing was chosen
        logger.debug("Could not read the Browser Program setting", exc_info=True)
        return ""


def google_chrome_path() -> str | None:
    """Where Google Chrome is installed on this machine, or None."""
    system = platform.system()
    if system == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        candidates = [found for found in (which("google-chrome"), which("google-chrome-stable"))
                      if found]
    return next((path for path in candidates if os.path.isfile(path)), None)


_BUNDLED = {"Windows": "chromium/windows/chrome-win/chrome.exe",
            "Darwin": "chromium/Chromium.app/Contents/MacOS/Chromium"}


def has_bundled_browser() -> bool:
    """Whether this build ships a browser of its own: a full build, not a slim one."""
    return os.path.isfile(resource_path(
        _BUNDLED.get(platform.system(), "chromium/linux/chrome/chrome")))


def get_chromium_path(configured: str | None = None) -> ChromiumPath:
    """Get the platform-specific path to the Chromium binary.

    Returns a ChromiumPath(path, using_local_install); using_local_install is
    True when the binary came from the system rather than the bundled copy.

    `configured` is the Browser Program setting, read from the config when not given.
    Set, it is the answer on every platform, whether or not the file is there - a
    missing one is reported as missing rather than quietly replaced by another browser.
    """
    chosen = configured_browser_path() if configured is None else configured.strip()
    if chosen:
        from common.launcher_path import resolve_launcher_path

        return ChromiumPath(str(resolve_launcher_path(chosen)), True)
    system = platform.system()

    if system == "Windows":
        bundled_path = resource_path("chromium/windows/chrome-win/chrome.exe")
        if os.path.isfile(bundled_path):
            return ChromiumPath(bundled_path, False)

        # Slim builds do not include Chromium, so fall back to Chrome/Chromium.
        # Do not use Edge: it can hand off to an existing browser process and
        # exit immediately, which is a poor fit for kiosk process ownership.
        common_paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Chromium\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Chromium\Application\chrome.exe"),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                return ChromiumPath(path, True)
        return ChromiumPath(bundled_path, False)
    elif system == "Darwin":
        # Check common system Chrome/Chromium install paths first
        common_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            os.path.expanduser(
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
            os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                return ChromiumPath(path, True)
        return ChromiumPath(
            resource_path("chromium/Chromium.app/Contents/MacOS/Chromium"), False
        )
    elif system == "Linux":
        # Check if chromium is locally available on the system.
        # If so use that instead of bundled chromium.
        for binary_name in (
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
        ):
            chromium_path = which(binary_name)
            if chromium_path is not None:
                return ChromiumPath(chromium_path, True)
        return ChromiumPath(resource_path("chromium/linux/chrome/chrome"), False)
    else:
        raise RuntimeError(f"Unsupported OS: {system}")


MonitorInfo = namedtuple("MonitorInfo", ["x", "y", "width", "height"])


# MonitorInfo, which carries the same four fields as screeninfo's Monitor - see
# common/host/display_service.py, where the two meet.
def get_mac_screens() -> list[MonitorInfo]:
    """Get monitor info from NSScreen (macOS only).

    screeninfo.get_monitors() reports coordinates that can mismatch what
    Chromium expects on macOS.  NSScreen uses Cocoa's bottom-left origin
    coordinate system, so we convert to top-left origin for Chromium's
    --window-position flag.
    """
    import AppKit

    screens = AppKit.NSScreen.screens()
    if not screens:
        return []
    # The primary screen's top, not the highest top of any screen.
    primary = screens[0].frame()
    top = primary.origin.y + primary.size.height
    result = []
    for s in screens:
        frame = s.frame()
        x = int(frame.origin.x)
        y = int(top - frame.origin.y - frame.size.height)
        result.append(
            MonitorInfo(
                x=x, y=y, width=int(frame.size.width), height=int(frame.size.height)
            )
        )
    return result


SCREEN_WAIT_S = 5.0
SCREEN_POLL_S = 0.2


def _screens(wanted: int) -> list[Any]:
    if sys.platform == "darwin":
        monitors: list[Any] = get_mac_screens()
        logger.info("Detected %s macOS screens (via NSScreen): %s", len(monitors), monitors)
        return monitors

    from screeninfo import get_monitors

    monitors = get_monitors()
    deadline = time.monotonic() + SCREEN_WAIT_S
    while len(monitors) < wanted and time.monotonic() < deadline:
        time.sleep(SCREEN_POLL_S)
        monitors = get_monitors()
    logger.info("Detected %s monitors: %s", len(monitors), monitors)
    return monitors


PROFILE_PREFIX = "vpinfe_chromium_"


def _profile_dirs_in_use() -> set[str] | None:
    """Profile directories a running Chromium is holding, ours or anyone's."""
    try:
        import psutil
    except ImportError:
        return None                     # cannot prove anything is dead; sweep nothing
    in_use = set()
    try:
        for proc in psutil.process_iter(["cmdline"]):
            try:
                for arg in proc.info["cmdline"] or ():
                    if arg.startswith("--user-data-dir="):
                        in_use.add(arg.split("=", 1)[1])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (psutil.Error, OSError, SystemError):
        logger.debug("Could not read the process list; sweeping nothing", exc_info=True)
        return None
    return in_use


def sweep_stale_profiles() -> int:
    """Remove profile directories left behind by a run that did not shut down.

    terminate_all clears up after itself, but a SIGKILL, a power cut or a `systemctl
    stop` never reaches it - and on a cabinet /tmp is often tmpfs, so what leaks is RAM.
    Only directories no live process is using are touched, because a second instance may
    be running and its profiles are not ours to delete.
    """
    in_use = _profile_dirs_in_use()
    if in_use is None:
        return 0
    root = tempfile.gettempdir()
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(PROFILE_PREFIX):
            continue
        path = os.path.join(root, name)
        if path in in_use or not os.path.isdir(path):
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    if removed:
        logger.info("Removed %s leftover Chromium profile director%s",
                    removed, "y" if removed == 1 else "ies")
    return removed


class ChromiumManager:
    """Manages Chromium subprocess lifecycle for multi-monitor display."""

    def __init__(self) -> None:
        # [(window_name, process, temp_dir, monitor)]. The monitor is whichever of the
        # two shapes this platform answers with.
        self._processes: list[tuple[str, subprocess.Popen[Any], str, Any]] = []
        self._exit_event = threading.Event()
        # Which set of windows a monitor belongs to. Closing the windows moves it on, so
        # a monitor watching the ones that were closed on purpose stays quiet instead of
        # reading their going as a person closing a window.
        self._lock = threading.RLock()
        self._generation = 0
        # Windows only: [(window_name, hwnd)] awaiting restore.
        self._minimized_hwnds: list[tuple[str, int]] = []
        self._controller = ""
        sweep_stale_profiles()

    def launch_window(
        self,
        window_name: str,
        url: str,
        monitor: Any,
        index: int,
        mute_audio: bool = False,
        additional_options: str = "",
        include_default_options: bool = True,
        exclude_options: str = "",
        stderr: IO[bytes] | None = None,
    ) -> subprocess.Popen[Any]:
        """Launch one Chromium instance for a given monitor.

        Args:
            window_name: whatever the theme declared - 'playfield', 'backglass' and
                'scoreview' by default, and anything else a theme asks for
            url: The URL to load (e.g. http://127.0.0.1:8001/app/backglass)
            monitor: screeninfo Monitor object with x, y, width, height
            index: Unique index for temp profile directory
            stderr: where Chromium's error output goes, whatever the third-party log setting
        """
        chrome_path, using_local_install = get_chromium_path()
        if not os.path.exists(chrome_path):
            raise BrowserNotFoundError(f"Chromium binary not found: {chrome_path}")
        logger.debug("Using Chromium executable for '%s': %s", window_name, chrome_path)

        user_data_dir = tempfile.mkdtemp(
            prefix=f"vpinfe_chromium_{window_name}_{index}_"
        )

        # Suppress "Google API keys missing" banner
        env = os.environ.copy()
        env["GOOGLE_API_KEY"] = "no"
        env["GOOGLE_DEFAULT_CLIENT_ID"] = "no"
        env["GOOGLE_DEFAULT_CLIENT_SECRET"] = "no"

        # A local Chromium would load the bundled libraries ahead of its own.
        if using_local_install:
            env = bundled_libraries.for_child(env)

        builtin = get_builtin_chromium_options(
            window_name=window_name,
            url=url,
            monitor=monitor,
            user_data_dir=user_data_dir,
            mute_audio=mute_audio,
            include_default_options=include_default_options,
            exclude_options=parsed_or_none(exclude_options,
                                           "chromium.options_exclude"),
        )
        extra_args = parsed_or_none(additional_options, "chromium.options")
        if extra_args:
            logger.debug("Adding Chromium options for '%s': %s", window_name, extra_args)
        args = [chrome_path, *(line.text for line in option_lines(builtin, extra_args))]

        logger.info(
            "Launching '%s' on monitor %s (%sx%s at %s,%s)",
            window_name,
            index,
            monitor.width,
            monitor.height,
            monitor.x,
            monitor.y,
        )

        # Launch in its own process group so we can kill the entire tree on shutdown
        # (Chromium spawns renderers, GPU, zygote children that must all be killed)
        popen_kwargs: dict[str, Any] = {"env": env}
        if platform.system() != "Windows":
            popen_kwargs["start_new_session"] = True

        # Keep Chromium helper process noise (updater/crashpad) out of app logs
        # unless thirdparty logging is explicitly enabled.
        popen_kwargs["stdin"] = subprocess.DEVNULL
        if not include_thirdparty_logs():
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
        if stderr is not None:
            popen_kwargs["stderr"] = stderr

        proc = subprocess.Popen(args, **popen_kwargs)
        self._processes.append((window_name, proc, user_data_dir, monitor))
        return proc

    def launch_all_windows(self, iniconfig: ConfigStore,
                           base_url: str = "http://127.0.0.1") -> None:
        """Launch Chromium windows for all configured displays.

        Args:
            iniconfig: ConfigStore instance with display and network settings
            base_url: Base URL for the HTTP server
        """
        network = serving.network(iniconfig)
        displays = DisplayConfig.from_config(iniconfig)
        settings = SettingsConfig.from_config(iniconfig)
        theme_assets_port = serving.own_port(iniconfig)
        theme_name = theme_api.get_theme_name(iniconfig)
        splash_enabled = settings.splashscreen
        library = _library_endpoint(network)

        # One definition, in runtime. Reversed so the controller - first in the theme's
        # list - is launched last and takes focus.
        from frontend.runtime import window_configs

        configs = window_configs(iniconfig)
        named = [int(screen) for _, key in configs
                 if (screen := displays.window_screen_id(key).strip())]
        monitors = _screens(max(named) + 1 if named else 1)
        self._controller = theme_windows.controller([name for name, _ in configs])
        for window_name, config_key in reversed(configs):
            screen_id_str = displays.window_screen_id(config_key).strip()
            if not screen_id_str:
                continue

            screen_id = int(screen_id_str)
            if screen_id >= len(monitors):
                logger.warning(
                    "%s=%s but only %s monitors found",
                    config_key,
                    screen_id,
                    len(monitors),
                )
                continue

            monitor = monitors[screen_id]
            url = _build_window_url(
                base_url=base_url,
                theme_assets_port=theme_assets_port,
                theme_name=theme_name,
                window_name=window_name,
                splash_enabled=splash_enabled,
                ws_port=network.ws_port,
                http_port=library.port,
                library_host=library.host,
                device_port=library.device_port,
                page=theme_api.window_page_path(iniconfig, window_name),
            )

            section = OVERRIDE_SECTIONS.get(theme_windows.canonical(window_name))
            override_str = cfg_get(iniconfig, section, "override", "").strip() if section else ""
            if override_str:
                separator = "&" if "?" in url else "?"
                url += f"{separator}override={override_str}"
                logger.debug("Override applied - Final URL: %s", url)

            # The other windows are up before the controller opens and takes focus.
            if window_name == self._controller:
                time.sleep(0.5)

            # One window failing is one window. Unwrapped, whatever went wrong on the
            # backglass took the playfield with it and the cabinet showed nothing.
            try:
                self.launch_window(
                    window_name,
                    url,
                    monitor,
                    screen_id,
                    mute_audio=(window_name != self._controller),
                    additional_options=settings.chrome_options,
                    include_default_options=settings.default_chrome_options,
                    exclude_options=cfg_get(iniconfig, "Settings",
                                            "chromeoptionsexclude", ""),
                )
            except BrowserNotFoundError as missing:
                # The same browser serves every window, so one line says it for all.
                logger.warning("No browser, running headless: %s", missing)
                break
            except Exception:
                logger.exception("Could not launch the %s window", window_name)

        logger.info("Launched %s browser windows", len(self._processes))

        # macOS: ensure the controller gets focus after all windows launch
        if sys.platform == "darwin":
            threading.Thread(target=self._focus_game_window_mac, daemon=True).start()

    def _focus_game_window_mac(self) -> None:
        """macOS: ensure focus goes to the controller after launch."""
        time.sleep(0.5)
        try:
            import AppKit

            game_pid = None
            for win_name, proc, _, _ in self._processes:
                if win_name == self._controller:
                    game_pid = proc.pid
                    break

            if game_pid is None:
                return

            for ns_app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
                if ns_app.processIdentifier() == game_pid:
                    ns_app.activateWithOptions_(
                        AppKit.NSApplicationActivateIgnoringOtherApps
                    )
                    logger.debug("macOS: activating playfield window focus")
                    return

            logger.debug("macOS: playfield app process not available for focus activation")
        except Exception:
            logger.exception("macOS focus activation failed")

    def activate_all_mac(self) -> None:
        """macOS: re-activate all Chromium windows after an external app (e.g. VPX) exits.

        Called after VPX exits so Chromium kiosk windows return to the foreground
        instead of staying hidden behind the desktop.
        """
        try:
            import AppKit

            our_pids = {proc.pid for _, proc, _, _ in self._processes}
            activated = 0
            for ns_app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
                if ns_app.processIdentifier() in our_pids:
                    ns_app.activateWithOptions_(
                        AppKit.NSApplicationActivateIgnoringOtherApps
                    )
                    activated += 1
            logger.info(
                "macOS: re-activated %s Chromium windows after VPX exit", activated
            )
        except Exception:
            logger.exception("macOS re-activation failed")

    def _win_find_our_hwnds(self) -> list[tuple[str, int]]:
        """Windows: top-level HWNDs belonging to our Chromium browser processes.

        Chromium's browser process owns the visible kiosk window, so matching on
        the PIDs we spawned is enough. Invisible helper windows (message pumps,
        IME) share those PIDs, hence the visible + unowned filter.

        Windows only, and it says so: both callers already check, and everything below
        this line is ctypes calling Win32 directly.
        """
        if sys.platform != "win32":
            return []
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # Win32's own names, kept verbatim so a reader can look them up.
        WNDENUMPROC = ctypes.WINFUNCTYPE(  # noqa: N806
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]

        GW_OWNER = 4  # noqa: N806
        pids = {
            proc.pid: window_name
            for window_name, proc, _, _ in self._processes
            if proc.poll() is None
        }
        found = []

        def _on_window(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, GW_OWNER):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            window_name = pids.get(pid.value)
            if window_name:
                found.append((window_name, hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(_on_window), 0)
        return found

    def minimize_all_windows(self) -> int:
        """Windows: minimize our kiosk windows so VPX comes up as the foreground app.

        VPX pauses itself whenever its player window lacks focus, and Windows'
        foreground lock stops a background launcher from handing focus over. Taking
        our windows off the desktop before VPX starts sidesteps that entirely: VPX
        becomes the natural foreground window and never pauses. Restored by
        restore_all_windows() once the table exits.
        """
        if sys.platform != "win32":
            return 0

        self._minimized_hwnds = []
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            SW_MINIMIZE = 6  # noqa: N806 - Win32's own name
            for window_name, hwnd in self._win_find_our_hwnds():
                user32.ShowWindow(hwnd, SW_MINIMIZE)
                self._minimized_hwnds.append((window_name, hwnd))
            logger.info("Windows: minimized %s frontend windows for launch",
                    len(self._minimized_hwnds))
        except Exception:
            logger.exception("Windows minimize failed")

        return len(self._minimized_hwnds)

    def restore_all_windows(self) -> int:
        """Windows: restore the kiosk windows minimized by minimize_all_windows().

        Safe to call more than once, and a no-op if nothing was minimized. The
        controller is restored last so it ends up on top and holding focus.
        """
        if sys.platform != "win32":
            return 0

        minimized, self._minimized_hwnds = getattr(self, "_minimized_hwnds", []), []
        if not minimized:
            return 0

        restored = 0
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            SW_RESTORE = 9  # noqa: N806 - Win32's own name
            # Controller last: whichever window is restored last wins the foreground.
            ordered = sorted(minimized, key=lambda item: item[0] == self._controller)
            for window_name, hwnd in ordered:
                user32.ShowWindow(hwnd, SW_RESTORE)
                restored += 1
                if window_name == self._controller:
                    user32.SetForegroundWindow(hwnd)
            logger.info("Windows: restored %s frontend windows after launch", restored)
        except Exception:
            logger.exception("Windows restore failed")

        return restored

    @staticmethod
    def _get_descendant_pids(pid: int) -> list[int]:
        """Recursively find all descendant PIDs via /proc on Linux."""
        descendants = []
        try:
            with open(f"/proc/{pid}/task/{pid}/children", encoding="utf-8") as f:
                for child_str in f.read().split():
                    child_pid = int(child_str)
                    descendants.append(child_pid)
                    descendants.extend(ChromiumManager._get_descendant_pids(child_pid))
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pass
        return descendants

    def _kill_process_tree(self, proc: subprocess.Popen[Any], window_name: str,
                           force: bool = False) -> None:
        """Kill a Chromium process and all its children (renderers, GPU, zygote)."""
        if platform.system() == "Windows":
            # taskkill /T kills the entire process tree, /F forces it
            try:
                subprocess.call(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                logger.exception("taskkill failed for '%s'", window_name)
        else:
            # Walk /proc to find ALL descendants (regardless of process group)
            # then kill children first, parent last
            sig = signal.SIGKILL if force else signal.SIGTERM
            all_pids = self._get_descendant_pids(proc.pid)
            all_pids.append(proc.pid)
            logger.info(
                "Killing '%s' tree: %s with signal %s", window_name, all_pids, sig
            )
            for pid in all_pids:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
                except Exception:
                    logger.exception("kill %s failed", pid)

    def terminate_all(self) -> None:
        """Terminate all Chromium processes immediately and end the wait for them. Safe
        to call twice."""
        self.close_windows()
        self._exit_event.set()

    def close_windows(self) -> bool:
        """Close the windows and leave the run going, which is what a request to stop
        the frontend means. `terminate_all` is the one that ends the wait. True when
        there was a window to close."""
        with self._lock:
            self._generation += 1
            if not self._processes:
                return False

            logger.debug("Terminating all browser windows...")
            for window_name, proc, _temp_dir, _ in self._processes:
                try:
                    if proc.poll() is None:  # still running
                        # Use force=True (SIGKILL) directly - no need for graceful shutdown
                        self._kill_process_tree(proc, window_name, force=True)
                except Exception:
                    logger.exception("Error terminating '%s'", window_name)

            # Wait for parent processes to be reaped
            for window_name, proc, temp_dir, _ in self._processes:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("'%s' still not reaped", window_name)
                # The profile is a fresh temp directory per window per launch, and
                # nothing was removing it. Three per run adds up fast on a frontend that
                # restarts.
                shutil.rmtree(temp_dir, ignore_errors=True)

            self._processes.clear()
        logger.info("All browser windows closed.")
        return True

    def request_exit(self) -> None:
        """Unblock wait_for_exit and leave the windows to the caller."""
        self._exit_event.set()

    def reset_exit(self) -> None:
        """Clear a requested exit - once set, every wait_for_exit returns at once."""
        self._exit_event.clear()

    def watch(self, is_window_connected: Callable[[str], bool] | None = None,
              on_lost: Callable[[], None] | None = None) -> None:
        """Watch the windows now open, and when one is closed by hand take the rest down
        with it and call `on_lost`.

        Windows closed by `close_windows` do not count. Called again for each set of
        windows, because the monitor belongs to the set it started with.
        """
        with self._lock:
            generation = self._generation
        threading.Thread(target=self._monitor,
                         args=(generation, is_window_connected, on_lost),
                         daemon=True).start()

    def _monitor(self, generation: int,
                 is_window_connected: Callable[[str], bool] | None,
                 on_lost: Callable[[], None] | None) -> None:
        def current() -> bool:
            return self._generation == generation and not self._exit_event.is_set()

        while self._processes and current():
            for window_name, proc, _temp_dir, _ in list(self._processes):
                if proc.poll() is None:
                    continue
                if is_window_connected and is_window_connected(window_name):
                    logger.info(
                        "Window '%s' launcher process exited (code %s) "
                        "but frontend is still connected; waiting for disconnect.",
                        window_name,
                        proc.returncode,
                    )
                    while current() and is_window_connected(window_name):
                        self._exit_event.wait(timeout=0.5)
                    self._exit_event.wait(timeout=0.5)
                    if not current():
                        return
                logger.info("Window '%s' exited (code %s)", window_name, proc.returncode)
                with self._lock:
                    # Closed on purpose while this was deciding.
                    if self._generation != generation:
                        return
                    # One window exited - shut everything down
                    self.terminate_all()
                if on_lost is not None:
                    on_lost()
                return
            self._exit_event.wait(timeout=0.5)

    def wait_for_exit(self,
                      is_window_connected: Callable[[str], bool] | None = None) -> None:
        """Block until all Chromium processes have exited.

        Returns when any one exits (which typically means the user closed a window or
        the app is shutting down). Used where windows are the whole run: Setup's.
        """
        if not self._processes:
            return
        self.watch(is_window_connected)
        # Block the main thread until exit is signaled
        self._exit_event.wait()

    def get_process(self, window_name: str) -> subprocess.Popen[Any] | None:
        """Get the process for a specific window."""
        for name, proc, _temp_dir, _ in self._processes:
            if name == window_name:
                return proc
        return None

    @property
    def has_windows(self) -> bool:
        """Whether any window was launched and not closed, running or not: a launcher
        that has exited can still have its window up."""
        return bool(self._processes)

    @property
    def is_running(self) -> bool:
        """Check if any Chromium processes are still running."""
        return any(proc.poll() is None for _, proc, _, _ in self._processes)
