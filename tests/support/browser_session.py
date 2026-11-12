"""Drive a real Chromium over the DevTools protocol, with no new dependencies.

`websockets` is already here for the bridge and `get_chromium_path` already finds the
browser VPinFE ships, so a render test costs a fixture theme and this file rather than a
toolchain. Puppeteer and Playwright would each add an ecosystem to a repo that has no
JavaScript tooling at all.

Only what a smoke test needs: open a page, wait for the DOM to say something, read
attributes back, press a key, and report every request that did not return 200. Anything
more belongs in a real driver, not here.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache

import websockets
from websockets.asyncio.client import ClientConnection

# What a page holds that is wider than the box it is in and is not a name being cut off:
# a scroll container the page did not mean to have, or a row pushed past the screen. The
# document's own scrollWidth misses it, since an inner container takes the overflow.
OVERFLOWING = """(() => [...document.querySelectorAll('body *')]
  .filter(el => el.getBoundingClientRect().width > 0
                && el.scrollWidth > el.clientWidth + 1
                && !el.closest('.truncate, .q-ripple'))
  .map(el => el.tagName + '.' + String(el.className).slice(0, 60)
             + ' ' + el.scrollWidth + '>' + el.clientWidth)
)()"""


# Every Chromium a test starts, or starts through the app under test, carries these. macOS
# asks the login keychain for a "Safe Storage" password at launch, and a process whose HOME
# is a scratch folder has no keychain to ask: the system raises "Keychain Not Found" on
# screen and the browser waits on it.
NO_KEYCHAIN_FLAGS = ("--use-mock-keychain", "--password-store=basic")


def free_port() -> int:
    """A port nothing holds. Probed the way the instance binds, not on loopback.

    `http_bind` defaults to 0.0.0.0, so a port free on 127.0.0.1 can still be taken on
    another interface and the instance then fails with "address already in use". Asking
    for the same wildcard the instance asks for removes that half of it. The window
    between this close and the instance's bind is still a race, and still open.
    """
    with socket.socket() as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


# Chrome for Testing, unpacked into the cache. Never fall back to the Chrome in /Applications.
_MAC_TEST_BROWSER = os.path.expanduser(
    "~/Library/Caches/vpinfe/cft-157/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")


def _installed_browser() -> str | None:
    if sys.platform == "darwin":
        found = _MAC_TEST_BROWSER
        return found if os.path.isfile(found) and os.access(found, os.X_OK) else None
    with suppress(Exception):
        from frontend.chromium_manager import get_chromium_path

        found = get_chromium_path()
        path = getattr(found, "path", None)
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


@lru_cache(maxsize=1)
def chromium_path() -> str | None:
    """A browser this machine can actually be driven through, or None.

    Answers by launching one and driving it, not by finding a file: a browser that is
    installed and cannot be driven makes every test using it fail rather than skip.
    Cached, so the cost is one launch per run.

    The test workflow does not fetch a browser, which is the build job's. Linux and
    Windows runners have none and skip; a macOS runner ships Chrome, drives it here and
    then times out under load. `VPINFE_BROWSER_TESTS=1` asks for them anyway.
    """
    if os.environ.get("CI") and not os.environ.get("VPINFE_BROWSER_TESTS"):
        return None

    path = _installed_browser()
    if path is None:
        return None

    async def drivable() -> bool:
        with suppress(Exception):
            async with BrowserSession(path) as session:
                # Navigating, not just connecting: a browser can do the second and
                # not the first.
                await session.navigate("about:blank")
                return True
        return False

    return path if asyncio.run(drivable()) else None


_PROFILE_REMOVE_ATTEMPTS = 15
_PROFILE_REMOVE_DELAY = 0.3
# Between one input event and the next, and after the last. Drives take a click's return as
# the page having begun to respond, and a page reads a press and its release as separate
# events.
_INPUT_GAP_S = 0.02
# How long a target must hold its position before a click presses it.
_STEADY_MS = 150


async def _rmtree_persistently(path: str) -> None:
    """Retry `rmtree` until `path` is actually gone, or the attempts run out."""
    for _ in range(_PROFILE_REMOVE_ATTEMPTS):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        await asyncio.sleep(_PROFILE_REMOVE_DELAY)


@dataclass
class PageResult:
    body: dict = field(default_factory=dict)
    page_log: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)


class BrowserSession:
    """One headless Chromium, one tab. Use as an async context manager."""

    def __init__(self, binary: str, timeout: float = 30.0):
        self.binary = binary
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._group = False
        self._profile: str | None = None
        self._ws: ClientConnection | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self.page_log: list[str] = []
        self.failed_requests: list[str] = []
        self._status: dict[str, int] = {}

    async def __aenter__(self) -> BrowserSession:
        # __aexit__ only runs after this returns, so a raise partway through - the
        # handshake below never coming up - has to tear down through it directly, or
        # the process and its profile outlive the session with nothing watching them.
        try:
            port = free_port()
            self._profile = tempfile.mkdtemp(prefix="vpinfe-smoke-")
            self._proc = subprocess.Popen(
                [self.binary, "--headless=new", f"--remote-debugging-port={port}",
                 f"--user-data-dir={self._profile}", "--no-first-run",
                 "--no-default-browser-check", "--disable-gpu", "--disable-dev-shm-usage",
                 # A CI runner has no user namespaces to build a sandbox in, so Chrome
                 # exits immediately without this. Safe here: the only pages it opens
                 # are ours, on loopback, in a profile thrown away afterwards.
                 "--no-sandbox", "--disable-setuid-sandbox",
                 *NO_KEYCHAIN_FLAGS,
                 "--window-size=1280,720", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                start_new_session=os.name == "posix")
            self._group = os.name == "posix"

            endpoint = await self._page_endpoint(port)
            # 30s, not the 10s default: a cold CI runner can take longer than that to
            # get Chrome to the point of answering a WebSocket upgrade, and the failure
            # reads as a render regression rather than as a slow machine.
            self._ws = await websockets.connect(endpoint, max_size=None, open_timeout=30)
            asyncio.create_task(self._pump())
            for domain in ("Page", "Runtime", "Network", "Log"):
                await self.send(f"{domain}.enable")
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_exc) -> None:
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
        if self._proc is not None:
            # The child can already be gone - a crash, or the exit `_page_endpoint`
            # detects through `poll()` - and terminate() then signals a reaped pid.
            group = getattr(self._proc, "pid", None) if self._group else None
            with suppress(ProcessLookupError, PermissionError):
                if isinstance(group, int):
                    os.killpg(group, signal.SIGTERM)
                else:
                    self._proc.terminate()
            with suppress(Exception):
                self._proc.wait(timeout=10)
            if isinstance(group, int):
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(group, signal.SIGKILL)
            for pipe in (self._proc.stdout, self._proc.stderr, self._proc.stdin):
                if pipe is not None:
                    with suppress(Exception):
                        pipe.close()   # Popen does not, and the handle outlives the run
            self._proc = None
        if self._profile:
            await _rmtree_persistently(self._profile)

    async def _page_endpoint(self, port: int) -> str:
        """The tab's own socket, not the browser's.

        Page, Network and Log are page domains: on the browser-level endpoint they do not
        exist, and `Page.enable` comes back "wasn't found" rather than doing nothing.
        """
        deadline = asyncio.get_event_loop().time() + self.timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/list", timeout=1) as handle:
                    targets = json.load(handle)
                for target in targets:
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
            except Exception:
                pass
            assert self._proc is not None
            if self._proc.poll() is not None:
                break
            await asyncio.sleep(0.1)
        assert self._proc is not None
        raise RuntimeError(
            "Chromium never opened a page to attach to. It exited "
            f"{self._proc.returncode}; stderr:\n{self._stderr()}")

    def _stderr(self) -> str:
        """Why the browser gave up, which a timeout on its own never says. Only what
        the pipe already holds: the browser can still be running."""
        if self._proc is None or self._proc.stderr is None:
            return "(none)"
        said = b""
        with suppress(Exception):
            fd = self._proc.stderr.fileno()
            os.set_blocking(fd, False)
            while chunk := os.read(fd, 65536):
                said += chunk
        return said.decode(errors="replace")[-2000:] or "(silent)"

    async def _pump(self) -> None:
        """Read every frame once, so replies and events cannot consume each other."""
        assert self._ws is not None
        with suppress(Exception):
            async for raw in self._ws:
                message = json.loads(raw)
                if "id" in message:
                    waiting = self._pending.pop(message["id"], None)
                    if waiting is not None and not waiting.done():
                        waiting.set_result(message)
                    continue
                self._on_event(message.get("method", ""), message.get("params") or {})

    def _on_event(self, method: str, params: dict) -> None:
        if method == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", "")))
                            for a in params.get("args", []))
            self.page_log.append(f"{params.get('type', 'log')}: {text}")
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") in ("error", "warning"):
                self.page_log.append(f"{entry['level']}: {entry.get('text', '')}")
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            status, url = response.get("status", 0), response.get("url", "")
            self._status[url] = status
            # The browser asks for a favicon on its own. A theme never requests one, so
            # counting it would mean every page "failed" and the assertion would have to
            # be loosened - which is how a real 404 gets waved through.
            if status >= 400 and not url.endswith("/favicon.ico"):
                self.failed_requests.append(f"{status} {url}")
        elif method == "Network.loadingFailed":
            # A request that never got a response at all - a dead port, a blocked scheme.
            if not params.get("canceled"):
                self.failed_requests.append(
                    f"failed {params.get('errorText', '?')} ({params.get('type', '?')})")

    async def send(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        message_id = self._next_id
        assert self._ws is not None
        answer: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = answer
        try:
            try:
                await asyncio.wait_for(self._ws.send(json.dumps(
                    {"id": message_id, "method": method, "params": params or {}})),
                    self.timeout)
            except TimeoutError:
                raise TimeoutError(f"{method} was never taken") from None
            try:
                reply = await asyncio.wait_for(answer, self.timeout)
            except TimeoutError:
                raise TimeoutError(f"{method} did not answer") from None
        finally:
            self._pending.pop(message_id, None)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply.get("result") or {}

    async def navigate(self, url: str) -> None:
        await self.send("Page.navigate", {"url": url})

    async def evaluate(self, expression: str):
        result = await self.send("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"]))
        return (result.get("result") or {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 20.0):
        """Poll a JavaScript expression until it is truthy. Returns its value."""
        deadline = asyncio.get_event_loop().time() + timeout
        last = None
        while asyncio.get_event_loop().time() < deadline:
            with suppress(Exception):
                last = await self.evaluate(expression)
                if last:
                    return last
            await asyncio.sleep(0.1)
        raise TimeoutError(f"never became truthy: {expression} (last={last!r})")

    async def _centre(self, selector: str, nth: int) -> dict:
        """Where the element is once it exists, has a size and has not moved for a while.

        Two identical frames are not enough: a menu Quasar fills and repositions after it
        opens can hold still for a frame and then jump, so the press misses it.
        """
        where = await self.evaluate(
            "(async () => {"
            f" const pick = () => document.querySelectorAll({json.dumps(selector)})[{int(nth)}];"
            " const frame = () => new Promise(done => requestAnimationFrame(done));"
            " let until = performance.now() + 5000;"
            " while (!pick()) { if (performance.now() > until) return null; await frame(); }"
            " pick().scrollIntoView({block: 'center'});"
            " const measure = () => pick()?.getBoundingClientRect();"
            " let box = measure(), was, since = performance.now();"
            " until = since + 5000;"
            " do {"
            "  if (performance.now() > until) return {unsettled: true};"
            "  was = box; await frame(); box = measure();"
            "  if (!box || !was || ['x', 'y', 'width', 'height'].some(k => box[k] !== was[k]))"
            "   since = performance.now();"
            " } while (!box || !box.width || !box.height"
            f"          || performance.now() - since < {_STEADY_MS});"
            " return {x: box.left + box.width / 2, y: box.top + box.height / 2};"
            " })()")
        if not where:
            raise AssertionError(f"nothing to click at {selector!r}[{nth}]")
        if where.get("unsettled"):
            raise AssertionError(f"{selector!r}[{nth}] had no steady size after 5s")
        return where

    async def click(self, selector: str, *, nth: int = 0) -> None:
        """Click something the way a finger does, at its own coordinates, once it has
        stopped moving.

        A real mouse event, not `element.click()`. Quasar builds several of its controls
        - a select, a menu item - out of listeners that a synthetic click never reaches,
        so a test that drives them with `.click()` reports success and changes nothing.
        """
        where = await self._centre(selector, nth)
        for event in ("mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent",
                            {"type": event, "x": where["x"], "y": where["y"],
                             "button": "left", "clickCount": 1})
            await asyncio.sleep(_INPUT_GAP_S)

    async def as_phone(self, width: int = 390, height: int = 844) -> None:
        """A phone's screen and its input: touch events, and a pointer that cannot hover,
        so a `(hover: hover)` rule does not apply as it would to the headless mouse."""
        await self.send("Emulation.setDeviceMetricsOverride",
                        {"width": width, "height": height, "deviceScaleFactor": 2,
                         "mobile": True})
        await self.send("Emulation.setTouchEmulationEnabled",
                        {"enabled": True, "maxTouchPoints": 1})
        await self.send("Emulation.setEmulatedMedia",
                        {"features": [{"name": "hover", "value": "none"},
                                      {"name": "any-hover", "value": "none"},
                                      {"name": "pointer", "value": "coarse"}]})

    async def tap(self, selector: str, *, nth: int = 0) -> None:
        """Touch something once it has stopped moving, as a finger does."""
        where = await self._centre(selector, nth)
        point = {"x": where["x"], "y": where["y"]}
        await self.send("Input.dispatchTouchEvent",
                        {"type": "touchStart", "touchPoints": [point]})
        await asyncio.sleep(_INPUT_GAP_S)
        await self.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        await asyncio.sleep(_INPUT_GAP_S)

    async def hold(self, selector: str, seconds: float, *, nth: int = 0) -> None:
        """Keep a finger on something for `seconds`, then lift it."""
        where = await self._centre(selector, nth)
        point = {"x": where["x"], "y": where["y"]}
        await self.send("Input.dispatchTouchEvent",
                        {"type": "touchStart", "touchPoints": [point]})
        await asyncio.sleep(seconds)
        await self.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        await asyncio.sleep(_INPUT_GAP_S)

    async def hover(self, selector: str, *, nth: int = 0) -> None:
        """Rest the pointer on something, as a mouse does, so `:hover` applies to it."""
        where = await self._centre(selector, nth)
        await self.send("Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": where["x"], "y": where["y"],
                         "button": "none"})

    async def unhover(self) -> None:
        """Move the pointer to a corner of the page, off everything."""
        await self.send("Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1, "y": 1, "button": "none"})

    async def key_down(self, key: str, code: str, key_code: int = 0) -> None:
        await self._key("keyDown", key, code, key_code)

    async def key_up(self, key: str, code: str, key_code: int = 0) -> None:
        await self._key("keyUp", key, code, key_code)

    async def press(self, key: str, code: str, key_code: int = 0) -> None:
        await self.key_down(key, code, key_code)
        await self.key_up(key, code, key_code)

    async def _key(self, event: str, key: str, code: str, key_code: int) -> None:
        await self.send("Input.dispatchKeyEvent",
                        {"type": event, "key": key, "code": code,
                         "windowsVirtualKeyCode": key_code})
        await asyncio.sleep(_INPUT_GAP_S)

    async def escape(self) -> None:
        """Escape as Quasar reads it, by the legacy `keyCode` that `press` leaves at 0."""
        await self.press("Escape", "Escape", 27)

    async def body_data(self) -> dict:
        return await self.evaluate("Object.assign({}, document.body.dataset)") or {}

    def status_of(self, url_fragment: str) -> int | None:
        for url, status in self._status.items():
            if url_fragment in url:
                return status
        return None
