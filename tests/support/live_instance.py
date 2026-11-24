"""A real VPinFE, started against a throwaway library, for tests that need a browser.

Not the servers assembled by hand: `main.py --headless` is what a user runs, and the
breaks this exists to catch have all been in the wiring between the pieces rather than in
a piece. Two of them were a hardcoded window list, which no unit test could see because
each unit was correct.

The instance gets its own config dir, its own ports and its own library, so it cannot
touch the developer's install or collide with one already running.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from io import TextIOWrapper
from pathlib import Path
from tempfile import TemporaryDirectory

from common.devices import discovery
from common.security import http_client

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS_THEME = REPO_ROOT / "tests" / "fixtures" / "theme-harness"

# Matched as text: the errno belongs to the subprocess and never reaches this side.
_PORT_TAKEN = "address already in use"
_START_ATTEMPTS = 3

# A window's monitor is set in `windows.<name>`, except where the section is spelled apart.
_WINDOW_SECTIONS = {"scoreview": "windows.scoreview"}


class LiveInstance:
    """Start `main.py --headless`, wait for it to serve, and stop it afterwards."""

    def __init__(self, games_root: Path, theme: str = "Harness",
                 extra_settings: dict | None = None,
                 windows: tuple[str, ...] = ("playfield", "backglass", "scoreview")):
        self.games_root = Path(games_root)
        self.theme = theme
        self.windows = windows
        self.extra_settings = extra_settings or {}
        # No trailing hyphen on purpose. The Metrics page renders this path, and the
        # pseudo-locale check reads the screen for bare English words - a hyphen is a
        # word boundary, so "vpinfe-live-<random>" offered the suffix as a word and it
        # was all letters about 6% of the time. A digit denies the boundary.
        self._tmp = TemporaryDirectory(prefix="vpinfe-live0")
        self.config_dir = Path(self._tmp.name)
        # The instance's home folder, so Setup's write of Visual Pinball's ini stays here.
        self._home_tmp = TemporaryDirectory(prefix="vpinfe-home0")
        self.home = Path(self._home_tmp.name)
        # What the instance makes in its temp folder (an upload session left open when a
        # drive ends) goes with the instance. Stopping it with a signal runs none of its own
        # clean-up, so in the shared one it outlives the test.
        self._scratch_tmp = TemporaryDirectory(prefix="vpinfe-scratch0")
        self.proc: subprocess.Popen | None = None
        self._log: TextIOWrapper | None = None
        self.ports: dict[str, int] = {}

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> LiveInstance:
        self._install_harness_theme()
        # Retried only when the port was taken between the probe and the bind. Anything
        # else that kills the instance raises on the first attempt.
        for attempt in range(_START_ATTEMPTS):
            self._write_config()
            self._start()
            try:
                self._wait_until_serving()
            except BaseException as exc:
                last = attempt == _START_ATTEMPTS - 1
                retryable = isinstance(exc, RuntimeError) and _PORT_TAKEN in str(exc).lower()
                self._stop()        # before the raise, not after: nothing else closes it
                if last or not retryable:
                    raise
                continue
            return self
        raise AssertionError("unreachable")

    def _start(self) -> None:
        # To a file, not a pipe. Nothing drains a pipe until the process is stopped, so
        # a chatty startup fills the buffer and the child blocks on its own logging -
        # which looks exactly like an instance that never finished booting.
        self._log_path = self.config_dir / "instance.log"
        self._log = open(self._log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            # -u because the child buffers its own stdout when it is not a tty, so
            # nothing reaches the file until it exits. `output()` flushes this end of the
            # pipe, which does nothing about the far end - every failure of a still-running
            # instance reported an empty log, which is why they were hard to diagnose.
            [sys.executable, "-u", "main.py", "--headless"],
            cwd=str(REPO_ROOT),
            # Off the network as well as off the developer's config: an instance that
            # announced itself would turn up in the device list of whatever install is
            # already running here, which is exactly what its own config dir prevents.
            env={**os.environ, "VPINFE_CONFIG_DIR": str(self.config_dir),
                 discovery.OFF: "1", http_client.OFFLINE: "1", "PYTHONUNBUFFERED": "1",
                 **self._home_env()},
            stdout=self._log, stderr=subprocess.STDOUT, text=True)

    def _home_env(self) -> dict[str, str]:
        """Every variable a platform resolves home and preferences from: any one left
        pointing at the real folder is the one that gets written."""
        scratch = self._scratch_tmp.name
        return {
            "TMPDIR": scratch, "TEMP": scratch, "TMP": scratch,
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
        }

    def _stop(self) -> None:
        """Take the instance down without touching the config dir it was built in."""
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __exit__(self, *_exc) -> None:
        self._stop()
        self._tmp.cleanup()
        self._home_tmp.cleanup()
        self._scratch_tmp.cleanup()

    # -- setup -------------------------------------------------------------

    def _write_config(self) -> None:
        from common.config.config_access import cfg_set
        from common.config.config_store import ConfigStore

        # Ports of its own, so a running instance is not a reason this fails.
        from tests.support.browser_session import free_port
        self.ports = {"manager": free_port(), "ws": free_port()}

        store = ConfigStore(str(self.config_dir / "vpinfe.ini"))
        from common.games.library_folders import Location, mint_location_id
        store.set_list("locations", [Location(mint_location_id(), str(self.games_root)).as_item()])
        cfg_set(store, "general", "theme", self.theme)
        # A window with no screen assigned gets no API instance, which is right on a
        # desktop with one monitor and wrong for a test that wants to open all three.
        for index, window in enumerate(self.windows):
            section = _WINDOW_SECTIONS.get(window, f"windows.{window}")
            cfg_set(store, section, "screen_id", index)
        cfg_set(store, "network", "http_port", self.ports["manager"])
        cfg_set(store, "network", "ws_port", self.ports["ws"])
        cfg_set(store, "themes", "refresh", "never")
        # A fresh config is a first run, which opens Setup over the Manager's root and
        # holds the sync watchers back. These instances stand for ones already set up;
        # `extra_settings` below puts it back for a test of the first run itself.
        cfg_set(store, "device", "setup_pending", False)
        for (section, key), value in self.extra_settings.items():
            cfg_set(store, section, key, value)
        # A window the instance opens (Identify screens, Reopen windows) is a real Chromium
        # under this HOME, which has no login keychain. Appended to a test's own options,
        # never replaced by them.
        from tests.support.browser_session import NO_KEYCHAIN_FLAGS
        own = str(self.extra_settings.get(("chromium", "options"), "") or "")
        cfg_set(store, "chromium", "options", " ".join([*NO_KEYCHAIN_FLAGS, own]).strip())
        store.save()

    def _install_harness_theme(self) -> None:
        target = self.config_dir / "frontend_themes" / self.theme
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(HARNESS_THEME, target)

    def _wait_until_serving(self, timeout: float = 180.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            assert self.proc is not None
            if self.proc.poll() is not None:
                raise RuntimeError("VPinFE exited during startup:\n" + self.output())
            try:
                with urllib.request.urlopen(self.url("/themes/"), timeout=1) as handle:
                    handle.read()
                return
            except urllib.error.HTTPError as answered:
                answered.close()   # an HTTPError is a response, and holds a socket
                return             # answering at all is what we are waiting for
            except Exception:
                time.sleep(0.25)
        raise TimeoutError("VPinFE never served its assets:\n" + self.output())

    # -- reading -----------------------------------------------------------

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.ports['manager']}{path}"

    def manager_url(self, path: str = "/") -> str:
        """The Manager and the remote. The same server as `url`, which is a theme's."""
        return f"http://127.0.0.1:{self.ports['manager']}{path}"

    def theme_url(self, window: str = "playfield") -> str:
        query = (f"/themes/{self.theme}/index_{window}.html?window={window}"
                 f"&wsPort={self.ports['ws']}"
                 f"&themeAssetsPort={self.ports['manager']}")
        # What the launcher appends for a device, and the reason it has to be here too:
        # without it the page dials this machine for the library's art, which a device
        # does not have. See `_build_window_url`, which is what does this for real.
        library_url = str(self.extra_settings.get(("network", "library_url"), "") or "")
        if library_url:
            parsed = urllib.parse.urlparse(library_url)
            query += (f"&libraryHost={urllib.parse.quote(parsed.hostname or '', safe='')}"
                      f"&libraryPort={parsed.port or self.ports['manager']}"
                      f"&devicePort={self.ports['manager']}")
        return self.url(query)

    def api(self, path: str):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.ports['manager']}{path}", timeout=10) as handle:
            return json.load(handle)

    def post(self, path: str, body: dict):
        """A POST to the instance's API, for a test that has to make something happen
        rather than read what already did."""
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.ports['manager']}{path}",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as handle:
            return json.load(handle)

    def wait_for_api(self, timeout: float = 120.0) -> None:
        """Block until the API answers. Separate from `_wait_until_serving`, which waits
        on the theme routes: they answer before the library has loaded, so the api is the
        slower."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            assert self.proc is not None
            if self.proc.poll() is not None:
                raise RuntimeError("VPinFE exited before its api served:\n" + self.output())
            try:
                self.api("/api/v1/library/entries")
                return
            except urllib.error.HTTPError:
                return          # answering at all is what we are waiting for
            except Exception:
                time.sleep(0.25)
        raise TimeoutError("VPinFE never served its api:\n" + self.output())

    def output(self, tail: int | None = 4000) -> str:
        """Whatever the instance has logged so far, or all of it with `tail=None`.
        Readable while it is still running, which is the moment a test needs it."""
        path = getattr(self, "_log_path", None)
        if path is None or not path.exists():
            return ""
        with suppress(Exception):
            if self._log is not None:
                self._log.flush()
        logged = path.read_text(encoding="utf-8", errors="replace")
        return logged if tail is None else logged[-tail:]
