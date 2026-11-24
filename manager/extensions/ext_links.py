"""An extension's links to its own UI, as the Manager draws them.

An address carries `{host}` where the extension means the machine the Manager was opened
at, so a port of its own works from a laptop as well as at the cabinet.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from nicegui import ui

from common.extensions.pictures import HOST_SLOT, is_openable

PREFIX = "link:"
# Whatever the extension answers: the page it opens cannot reach back into the Manager.
REL = "noopener noreferrer"
HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")


def view_key(extension: str, key: str) -> str:
    """The rail entry of one link. Not a view: nothing resolves it as an address."""
    return f"{PREFIX}{extension}:{key}"


def is_link(view: str) -> bool:
    return view.startswith(PREFIX)


def opened_at() -> str:
    """The host the Manager page being drawn was opened at, as it goes in an address."""
    try:
        said = ui.context.client.request.headers.get("host", "")
    except (RuntimeError, AttributeError):
        said = ""
    return host_of(said)


def host_of(said: str) -> str:
    try:
        name = urlsplit(f"//{said}").hostname or ""
    except ValueError:
        name = ""
    try:
        if ipaddress.ip_address(name).version == 6:
            return f"[{name}]"
    except ValueError:
        pass
    return name if HOSTNAME.match(name) else "localhost"


def filled(url: str, host: str) -> str:
    """The address with `{host}` filled in, or nothing where that is not a web address."""
    done = url.replace(HOST_SLOT, host)
    return done if is_openable(done) else ""


def drawn(extension: dict[str, Any], host: str) -> list[dict[str, str]]:
    """The links of one extension that can be opened from `host`: key, area, label, tip, url."""
    found = []
    for one in extension.get("links") or []:
        url = filled(str(one.get("url") or ""), host)
        if url:
            found.append({**one, "url": url})
    return found


def find(extensions: Sequence[dict[str, Any]], view: str, host: str) -> dict[str, str] | None:
    """The link a rail entry stands for."""
    for extension in extensions:
        for one in drawn(extension, host):
            if view_key(str(extension.get("name") or ""), one["key"]) == view:
                return one
    return None
