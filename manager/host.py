"""The web server: one NiceGUI process serving the Manager, the HTTP API and the theme assets."""

from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Any, cast

from nicegui import app, ui

from common import i18n
from common.config.config_access import NetworkConfig
from common.devices import install_presence, serving
from common.host import addresses
from common.i18n import t
from common.security import encrypted_connections, machine_secrets

from . import loopback
from .kit import ghost_timers

logger = logging.getLogger("vpinfe.manager.host")

# Set to unblock a headless run's main loop.
_shutdown_event = threading.Event()

_ui_thread = None

_ui_port = NetworkConfig.http_port
# Every interface, which is what VPinFE has always answered on. Configurable so an install
# can pull it back to this machine, not because the default changes.
_ui_bind = NetworkConfig.http_bind
_tls: dict[str, Any] = {}


def _server_urls(port: int) -> list[str]:
    scheme = "https" if _tls else "http"
    urls = [f"{scheme}://localhost:{port}"]
    seen = {"127.0.0.1", "0.0.0.0", "::1"}
    resolved_ips: list[str] = []
    resolve_error: list[Exception] = []

    def _resolve() -> None:
        try:
            hostname = socket.gethostname()
            for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
                if family != socket.AF_INET:
                    continue
                ip = str(sockaddr[0])
                if ip in seen:
                    continue
                resolved_ips.append(ip)
        except Exception as e:
            resolve_error.append(e)

    resolver = threading.Thread(target=_resolve, daemon=True, name="server-ip-resolver")
    resolver.start()
    resolver.join(timeout=1.0)

    if resolver.is_alive():
        logger.warning(
            "Timed out while enumerating network addresses; "
            "continuing startup with localhost URL only."
        )
        return urls

    if resolve_error:
        logger.warning("Failed to enumerate network addresses: %s", resolve_error[0])
        return urls

    for ip in resolved_ips:
        seen.add(ip)
        urls.append(f"{scheme}://{ip}:{port}")
    return urls


# The route is named /favicon.ico whatever the file is; the browser reads the content
# type. One route covers every page - none of them set their own.
_FAVICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "img", "vpinfe-favicon.svg")


def _say_ready() -> None:
    """Print the addresses NiceGUI settled on, localhost first, as VPinFE's own line.

    Runs as a startup handler, after NiceGUI has filled `app.urls`, so a narrowed
    `HTTP Address` still lists only what answers. `ui.run` is given
    `show_welcome_message=False`; without it NiceGUI prints a second line of its own.
    """
    urls = sorted(app.urls, key=lambda url: ("//localhost:" not in url, url))
    if len(urls) >= 2:
        urls[-1] = f"and {urls[-1]}"
    print(f"VPinFE ready to go on {', '.join(urls)}", flush=True)


def _run_ui() -> None:
    storage_secret = machine_secrets.session_secret()
    logger.info("Starting the web server on host=%s port=%s", _ui_bind, _ui_port)
    logger.info("Expected URLs: %s", ", ".join(_server_urls(_ui_port)))
    app.on_startup(_say_ready)
    app.on_startup(addresses.warm)
    ghost_timers.install()
    # The language is set here, not on the @ui.page decorators: `resolve_language()` reads
    # app.config per request. Labels built at import still need a restart to change.
    # proxy_headers stays off: uvicorn rewriting the peer would hide the real socket
    # address from `common.security.encrypted_connections`.
    ui.run(title=t("manager.page.vpinfe_manager"),
           favicon=_FAVICON,
           host=_ui_bind,
           port=_ui_port,
           reload=False,
           show=False,
           language=cast(Any, i18n.nicegui_language()),
           storage_secret=storage_secret,
           log_config=None,
           proxy_headers=False,
           show_welcome_message=False,
           **_tls)


def _certificate_to_serve() -> dict[str, Any]:
    """The keyword arguments that make the network port answer HTTPS, or none where no
    certificate is chosen or the one chosen cannot be read, in which case this says so and
    the run is plain HTTP."""
    certfile, keyfile = encrypted_connections.instance().certificate()
    if not certfile:
        encrypted_connections.https_failed("")
        return {}
    reason = encrypted_connections.unreadable_reason(certfile, keyfile)
    if reason:
        logger.warning("The HTTPS certificate cannot be used (%s); serving plain HTTP", reason)
        encrypted_connections.https_failed(reason)
        return {}
    encrypted_connections.https_started()
    install_presence.announce_again()
    return {"ssl_certfile": certfile, "ssl_keyfile": keyfile}


def start_server(port: int = NetworkConfig.http_port,
                 bind: str = NetworkConfig.http_bind) -> threading.Thread:
    global _ui_thread, _ui_port, _ui_bind, _tls
    serving.bound("http_port", port)
    _ui_port = port
    _ui_bind = bind
    if _ui_thread and _ui_thread.is_alive():
        logger.info("The web server is already running")
        return _ui_thread
    _tls = _certificate_to_serve()
    if _tls:
        loopback.serve_on_startup(loopback.bind())
    _ui_thread = threading.Thread(target=_run_ui, daemon=True)
    _ui_thread.start()
    return _ui_thread


def stop_server() -> None:
    app.shutdown()
