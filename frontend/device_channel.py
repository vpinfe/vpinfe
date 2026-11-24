"""The connection between a device and its own windows.

Each window opens one, naming itself in the query string:
  ws://127.0.0.1:8002?window=playfield

JS to Python: calls, gated by `API_ALLOWED_METHODS`, answered by id.
Python to JS: events pushed out to the windows.

Never to another install and never window to window - each window holds its own connection to
this process, which fans events out to all of them. Was `ws_bridge`: "bridge" said
where it sat rather than what it did, and dated from replacing 2.x's JS API bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import ServerConnection

from common.config.config_access import NetworkConfig
from common.devices import serving
from common.devices.port_check import WINDOW_CHANNEL_BIND
from common.failures import why
from common.i18n import t
from common.service_errors import ServiceError
from frontend.api import API, API_ALLOWED_METHODS

logger = logging.getLogger("vpinfe.frontend.device_channel")

# Compared against urlparse().hostname, which unwraps the brackets an IPv6 url is
# written with - so "::1" matches "http://[::1]:8000" and "[::1]" would match nothing.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class DeviceChannel:
    """The WebSocket server a device's windows connect to."""

    # Public API methods that JS is allowed to call
    ALLOWED_METHODS = API_ALLOWED_METHODS

    def __init__(self, port: int = NetworkConfig.ws_port) -> None:
        self.port = port
        self._api_instances: dict[str, API] = {}
        self._connections: dict[str, ServerConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: websockets.Server | None = None
        self._stop_event = threading.Event()
        self._windows_changed: Callable[[int], None] | None = None

    def register_api(self, window_name: str, api_instance: API) -> None:
        """Register an API instance for a window name."""
        self._api_instances[window_name] = api_instance

    def registered_windows(self) -> frozenset[str]:
        """The names of the windows that have an API."""
        return frozenset(self._api_instances)

    def apis(self) -> list[API]:
        """The API of every window this process registered."""
        return list(self._api_instances.values())

    def on_windows_changed(self, callback: Callable[[int], None]) -> None:
        """Be told how many windows are connected, each time one connects or goes."""
        self._windows_changed = callback

    def _report_windows(self) -> None:
        if self._windows_changed is None:
            return
        try:
            self._windows_changed(len(self._connections))
        except Exception:
            logger.exception("A windows-changed callback failed")

    def is_window_connected(self, window_name: str) -> bool:
        """Return whether a frontend window currently has an active websocket."""
        return window_name in self._connections

    def start(self) -> None:
        """Start the WebSocket server in a daemon thread."""
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        # Wait briefly for the server to be ready
        for _ in range(50):
            if self._loop is not None and self._server is not None:
                break
            import time
            time.sleep(0.05)
        logger.info("Device channel listening on ws://127.0.0.1:%s/", self.port)

    def _run_server(self) -> None:
        """Run the async event loop in the daemon thread, and close it on the way out."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        """Start the WebSocket server and run until stopped."""
        # Allow immediate rebind after restart (avoids TIME_WAIT blocking)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((WINDOW_CHANNEL_BIND, self.port))
        sock.listen()
        sock.setblocking(False)

        self._server = await websockets.serve(
            self._handle_connection,
            sock=sock,
            max_size=10 * 1024 * 1024,  # 10MB max message size for large game data
        )
        # Wait until stop is signaled
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)
        self._server.close()
        await self._server.wait_closed()

    def _origin_allowed(self, origin: str | None) -> bool:
        """Whether a handshake's Origin may open a channel that can power off the machine.

        A browser sets Origin itself and a page cannot forge it, so this is what keeps any
        other page open on this machine from reaching `shutdown_system` - loopback binding
        does not, because WebSockets are not subject to same-origin the way XHR is. The
        port is held too: a page on another local port is another program's page.

        No Origin at all is allowed: that is a non-browser client, which already runs code
        here and so is not held back by anything this check could do.
        """
        if origin is None:
            return True
        parts = urlparse(origin)
        try:
            port = parts.port
        except ValueError:
            return False
        ours = {serving.bound_port("http_port"), serving.bound_port("loopback_port")}
        return parts.hostname in LOOPBACK_HOSTS and port is not None and port in ours

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        """Handle a new WebSocket connection from a Chromium window."""
        # A handler is only reached after a handshake, so there is always a request -
        # but the Origin check below is what keeps another page on this machine off a
        # channel that can power the machine down, and it must not be skipped because
        # something arrived in a shape nobody expected.
        request = websocket.request
        if request is None:
            logger.warning("Refused a websocket connection with no handshake request")
            await websocket.close(code=1008, reason="no request")
            return

        origin = request.headers.get("Origin")
        if not self._origin_allowed(origin):
            logger.warning("Refused a websocket connection from origin %r", origin)
            await websocket.close(code=1008, reason="origin not allowed")
            return

        # Parse window name from query params
        parsed = urlparse(request.path if hasattr(request, 'path') else str(request))
        params = parse_qs(parsed.query)
        window_name = params.get('window', ['unknown'])[0]

        # A window this process never opened is not one of ours. Every real window is
        # registered before its browser is launched, so the set is already known and a
        # name outside it can only be something else dialling in.
        if window_name not in self._api_instances:
            logger.warning("Refused a connection naming unknown window %r", window_name)
            await websocket.close(code=1008, reason="unknown window")
            return

        # One live connection per window. This used to overwrite, so a second client
        # naming an open window displaced it silently and inherited its events - and its
        # whole API surface, `shutdown_system` included. A real window that dropped is
        # already cleaned up in the `finally` below, so a genuine reconnect still fits.
        existing = self._connections.get(window_name)
        if existing is not None:
            logger.warning("Refused a second connection for window '%s'", window_name)
            await websocket.close(code=1008, reason="window already connected")
            return

        logger.info("Window '%s' connected", window_name)
        self._connections[window_name] = websocket
        self._report_windows()

        try:
            async for raw_message in websocket:
                try:
                    data = json.loads(raw_message)
                    await self._dispatch(window_name, websocket, data)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Invalid JSON'
                    }))
                except Exception:
                    logger.exception("Error handling message from '%s'", window_name)
        except websockets.exceptions.ConnectionClosed as exc:
            logger.info(
                "Window '%s' disconnected (code=%s, reason=%s)",
                window_name,
                getattr(exc, "code", "unknown"),
                getattr(exc, "reason", ""),
            )
        finally:
            if self._connections.get(window_name) is websocket:
                del self._connections[window_name]
                self._report_windows()

    async def _dispatch(self, window_name: str, websocket: ServerConnection,
                        data: dict[str, Any]) -> None:
        """Dispatch an incoming message from JS."""
        msg_type = data.get('type')

        if msg_type == 'api_call':
            await self._handle_api_call(window_name, websocket, data)
        else:
            logger.warning("Unknown message type from '%s': %s", window_name, msg_type)

    async def _handle_api_call(self, window_name: str, websocket: ServerConnection,
                               data: dict[str, Any]) -> None:
        """Handle a JS→Python API call."""
        call_id = data.get('id')
        method = data.get('method')
        args = data.get('args', [])

        if method not in self.ALLOWED_METHODS:
            logger.warning("Window '%s' called disallowed/unknown API method: %s", window_name,
                    method)
            await websocket.send(json.dumps({
                'type': 'api_response',
                'id': call_id,
                'error': f'Method not allowed: {method}'
            }))
            return

        api = self._api_instances.get(window_name)
        if api is None:
            await websocket.send(json.dumps({
                'type': 'api_response',
                'id': call_id,
                'error': f'No API instance for window: {window_name}'
            }))
            return

        fn = getattr(api, method, None)
        if fn is None or not callable(fn):
            await websocket.send(json.dumps({
                'type': 'api_response',
                'id': call_id,
                'error': f'Method not found: {method}'
            }))
            return

        try:
            # Run the API method in a thread to avoid blocking the event loop
            result = await asyncio.to_thread(fn, *args)

            await websocket.send(json.dumps({
                'type': 'api_response',
                'id': call_id,
                'result': result
            }))
        except websockets.exceptions.ConnectionClosed:
            pass  # Client disconnected before response (e.g. close_app)
        except Exception as e:
            if isinstance(e, ServiceError) and str(e):
                said = why(e)
            else:
                logger.exception("API call error: %s(%s)", method, args)
                said = t("error.frontend.call_failed", method=method)
            try:
                await websocket.send(json.dumps({
                    'type': 'api_response',
                    'id': call_id,
                    'error': said
                }))
            except websockets.exceptions.ConnectionClosed:
                pass

    # -----------------------------------------------------------
    # Python-callable methods for pushing events to browsers
    # -----------------------------------------------------------

    def send_event(self, window_name: str, message: dict[str, Any],
                   forward_iframe: bool = False) -> None:
        """Send an event to a specific window's browser."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_event_async(window_name, message, forward_iframe),
            self._loop
        )

    def send_event_all(self, message: dict[str, Any],
                       exclude: str | None = None) -> None:
        """Broadcast an event to all connected windows, optionally excluding one."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_event_all_async(message, exclude=exclude, forward_iframe=False),
            self._loop
        )

    def send_event_all_with_iframe(self, message: dict[str, Any]) -> None:
        """Broadcast an event to all windows, including iframe forwarding."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_event_all_async(message, exclude=None, forward_iframe=True),
            self._loop
        )

    async def _send_event_async(self, window_name: str, message: dict[str, Any],
                                forward_iframe: bool = False) -> None:
        """Internal async: send event to one window."""
        ws = self._connections.get(window_name)
        if ws is None:
            return
        try:
            await ws.send(json.dumps({
                'type': 'event',
                'message': message,
                'forward_iframe': forward_iframe
            }))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _send_event_all_async(self, message: dict[str, Any],
                                    exclude: str | None = None,
                                    forward_iframe: bool = False) -> None:
        """Internal async: broadcast event to windows."""
        payload = json.dumps({
            'type': 'event',
            'message': message,
            'forward_iframe': forward_iframe
        })
        for name, ws in list(self._connections.items()):
            if exclude and name == exclude:
                continue
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                pass

    def stop(self) -> None:
        """Stop the WebSocket server."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Device channel stopped.")
