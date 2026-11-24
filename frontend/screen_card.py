"""A test card on the playfield screen, for asking whether that screen is rotated correctly.

The card is a page served from this process, so its turn can change without a new window
(a window opened again on Sway can be dropped by Chromium, see `screen_numbers`). It is
turned by the stylesheet's `.vpinfe-upright` rule, the one a theme's page gets from the
frontend, so the card faces the way a theme that opts in will once the same turn is stored.

The page closes itself once nothing answers for `GIVE_UP`, and the card is closed here once
`keep` has not been called for `HOLD`, so a Manager that goes away takes the card with it.
`HOLD` has to stay above how often the Manager page calls `keep`.

A key or a click on the card closes it, so a desktop whose one screen is the playfield can
get back to the Manager. The page ignores input for `GRACE` after it loads and after each
time its window takes focus, since the compositor can place it long after it loads. A key
pressed with a modifier held (Alt+Tab, Cmd+Tab) is left alone so the window can still be
switched away from.
"""

from __future__ import annotations

import html
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from common.i18n import language, t
from common.values import quarter_turn
from frontend import screen_numbers
from frontend.chromium_manager import ChromiumManager

logger = logging.getLogger("vpinfe.frontend.screen_card")

POLL = 0.4
GIVE_UP = 6.0
HOLD = 8.0
GRACE = 1.0

_STYLESHEET = Path(__file__).parent / "static" / "common" / "vpinfe-style.css"
_ARROW = ('<svg viewBox="0 0 100 100" class="arrow"><path d="M50 8 L88 52 H62 V92 H38 V52 H12 Z" '
          'fill="currentColor"/></svg>')


_STYLE = (
    "html,body{margin:0;height:100%;background:#0f0722;color:#fff;overflow:hidden;"
    "font-family:system-ui,sans-serif;cursor:none}"
    ".card{position:fixed;top:0;left:0;width:100vw;height:100vh;box-sizing:border-box;"
    "display:flex;flex-direction:column;justify-content:space-between;"
    "border:1.2vmin solid #00d9ff}"
    ".band{display:flex;flex-direction:column;align-items:center;padding:3vmin;"
    "background:#00d9ff;color:#0f0722}"
    ".band b{font-size:11vmin;line-height:1}.band span{font-size:4.5vmin;font-weight:600}"
    ".arrow{width:12vmin;height:12vmin}.bottom .arrow{transform:rotate(180deg)}"
    ".middle{text-align:center}.middle b{display:block;font-size:30vmin;line-height:1}"
    ".middle span{font-size:6vmin;font-weight:600}"
    ".middle .hint{display:block;margin-top:3vmin;font-size:3.2vmin;font-weight:500;"
    "color:#cbb8ea}")

# Sets what `vpinfe-core.js` `#publishLayout` sets for the turn; keep the two in step.
_SCRIPT = (
    "const root=document.documentElement;let failing=0;"
    "async function ask(){try{"
    "const answer=await fetch('/state',{cache:'no-store'});"
    "const turn=(await answer.json()).rotation;"
    "root.dataset.vpinfeUpright=String(turn);"
    "root.style.setProperty('--vpinfe-upright-rotation',turn+'deg');failing=0;"
    "}catch(e){failing=failing||Date.now();"
    "if(Date.now()-failing>GIVE_UP)window.close();}}"
    "setInterval(ask,POLL);"
    "let born=Date.now();let leaving=false;"
    "addEventListener('focus',()=>{born=Date.now();});"
    "function leave(e){"
    "if(leaving||Date.now()-born<GRACE)return;"
    "if(e.type==='keydown'&&(e.repeat||e.ctrlKey||e.altKey||e.metaKey||"
    "['Shift','Control','Alt','AltGraph','Meta','OS','CapsLock','NumLock','ScrollLock',"
    "'Fn','FnLock'].includes(e.key)))return;"
    "leaving=true;fetch('/dismiss',{method:'POST'}).catch(()=>{leaving=false;});}"
    "addEventListener('keydown',leave);addEventListener('pointerdown',leave);")


def _word(key: str) -> str:
    return html.escape(t(key))


def page(number: int, rotation: int) -> str:
    """The card for screen `number`, already turned by `rotation`."""
    script = _SCRIPT.replace("GIVE_UP", str(int(GIVE_UP * 1000))).replace(
        "POLL", str(int(POLL * 1000))).replace("GRACE", str(int(GRACE * 1000)))
    return (
        f'<!doctype html><html lang="{html.escape(language())}" data-vpinfe-upright="{rotation}" '
        f'style="--vpinfe-upright-rotation:{rotation}deg"><head><meta charset=utf-8>'
        '<title>VPinFE</title><link rel="stylesheet" href="/vpinfe-style.css">'
        f"<style>{_STYLE}</style></head><body>"
        '<div class="vpinfe-upright card">'
        f'<div class="band top">{_ARROW}<b>{_word("manager.screens.card_top")}</b>'
        f'<span>{_word("manager.screens.card_top.help")}</span></div>'
        f'<div class="middle"><b>{number}</b>'
        f'<span>{_word("manager.screens.playfield")}</span>'
        f'<span class="hint">{_word("manager.screens.card_dismiss")}</span></div>'
        f'<div class="band bottom"><b>{_word("manager.screens.card_bottom")}</b>'
        f'<span>{_word("manager.screens.card_bottom.help")}</span>{_ARROW}</div>'
        f"</div><script>{script}</script></body></html>")


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, number: int, rotation: int) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.number = number
        self.rotation = rotation

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        route = self.path.split("?", 1)[0]
        if route == "/":
            self._send("text/html; charset=utf-8",
                       page(self.server.number, self.server.rotation).encode())
        elif route == "/state":
            self._send("application/json", json.dumps({"rotation": self.server.rotation}).encode())
        elif route == "/vpinfe-style.css":
            self._send("text/css; charset=utf-8", _STYLESHEET.read_bytes())
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        if self.path.split("?", 1)[0] != "/dismiss":
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.address.rstrip("/"):
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()
        # Closing waits on `_lock`, which `show` holds while the window is placed.
        threading.Thread(target=_dismissed, args=(self.server,), daemon=True).start()

    def _send(self, kind: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server's
        logger.debug(format, *args)


class _Open:
    def __init__(self, server: _Server, manager: ChromiumManager, compositor: Any,
                 focus: str | None, opened: list[screen_numbers.Opened]) -> None:
        self.kept = time.monotonic()
        self.server = server
        self.manager = manager
        self.compositor = compositor
        self.focus = focus
        self.opened = opened

    def showing(self) -> bool:
        return any(proc.poll() is None for _screen, proc, _errors in self.opened)


_lock = threading.RLock()
_card: _Open | None = None


def is_open() -> bool:
    with _lock:
        return _card is not None and _card.showing()


def _watch(held: _Open) -> None:
    while True:
        time.sleep(1.0)
        with _lock:
            if _card is not held:
                return
            if not held.showing() or time.monotonic() - held.kept > HOLD:
                close()
                return


def keep() -> bool:
    """Say the card is still wanted, and whether it is still up."""
    with _lock:
        if _card is None:
            return False
        _card.kept = time.monotonic()
        return _card.showing()


def show(screen: Any, rotation: Any) -> None:
    """Open the card on `screen` turned by `rotation`, or turn the one that is open there.

    Blocking while the compositor places the window. Raises what stopped a window opening
    at all; a window that opened and then closed shows as `is_open()` being False.
    """
    global _card
    turned = quarter_turn(rotation)
    with _lock:
        if _card is not None and _card.showing() and _card.server.number == screen.number:
            _card.server.rotation = turned
            keep()
            return
        close()
        server = _Server(screen.number, turned)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        try:
            manager = ChromiumManager()
            compositor = screen_numbers.window_commands()
            held = _card = _Open(server, manager, compositor,
                                 screen_numbers.focused(compositor), [])
            held.opened.append(screen_numbers.launch(manager, screen, server.address))
            if compositor is not None:
                screen_numbers.place_all(manager, compositor, held.opened,
                                         lambda _screen: server.address)
        except BaseException:
            if _card is None:
                _stop_serving(server)
            else:
                close()
            raise
        threading.Thread(target=_watch, args=(held,), daemon=True).start()


def turn(rotation: Any) -> None:
    """Turn the open card by `rotation`. Nothing happens where none is open."""
    with _lock:
        if _card is not None:
            _card.server.rotation = quarter_turn(rotation)


def _stop_serving(server: _Server) -> None:
    server.shutdown()
    server.server_close()


def _dismissed(server: _Server) -> None:
    """The card on `server` was dismissed from its page. A page left over from a card already
    closed closes nothing."""
    with _lock:
        if _card is not None and _card.server is server:
            close()


def close() -> None:
    """Close the card. Nothing happens where none is open."""
    global _card
    with _lock:
        held, _card = _card, None
        if held is None:
            return
        try:
            for one in held.opened:
                screen_numbers.report(*one)
            held.manager.terminate_all()
            screen_numbers.refocus(held.compositor, held.focus)
        finally:
            _stop_serving(held.server)
