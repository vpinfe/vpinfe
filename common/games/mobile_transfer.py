"""Sending games to a device that runs VPX but not VPinFE.

A phone or tablet running VPX Mobile is not an install: it has no library, no API and
nothing to announce. What it has is a small web server, and this speaks to it.

    GET  /files                                    what is on it, top level
    POST /folder?q=<dir>                           make a folder
    POST /upload?q=<dir>&file=<n>&offset=&length=  one chunk of one file
    POST /delete?q=<path>                          remove a folder
    POST /command?cmd=refresh_tables               look again

**What goes in a transfer is not decided here.** `export_bundle` already answers "what
belongs in an export", and the archive somebody downloads asks it too. A send differs in
one thing, by design: the table's settings file stays home unless the sender says it
goes.
Everything else is one answer; the plumbing differs because a zip and a chunked upload are
different transports.

`http.client` rather than `urllib`, because urllib re-encodes a query string and the
device's server matches on the bytes it was given.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import quote, urlparse

from common.devices.device_client import PROBE_SECONDS, why_not
from common.games import info_files
from common.games.export_bundle import bundle_paths, prune_info
from common.i18n import t
from common.security import http_client
from common.service_errors import BlockedError

logger = logging.getLogger("vpinfe.common.games.mobile_transfer")

# A megabyte. Large enough that a table moves in a reasonable number of round trips,
# small enough that a lost connection costs one chunk rather than the file.
CHUNK_BYTES = 1024 * 1024

# Short for the folder, delete and refresh calls, long for the uploads: a chunk of a table
# crossing a mobile device's wifi should not time out. The listing asks at PROBE_SECONDS.
ASK_SECONDS = 10
SEND_SECONDS = 300

Progress = Callable[[int, int, str], None]


class DeviceUnreachableError(BlockedError):
    """The device did not answer. Its own words where it gave any."""


def carried(host: str, port: int, *, timeout: float = PROBE_SECONDS) -> list[str]:
    """The folder names the device holds, sorted.

    Folders only: a file at the top level is not a game, and reporting one as though it
    were would offer a delete that does nothing. Asked once: the retries in `_ask` are for
    a chunk dropped mid-transfer.
    """
    body = _ask(host, port, "GET", "/files", timeout=timeout, tries=1)
    try:
        found = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise DeviceUnreachableError(t("device.reason.unreadable")) from exc
    return sorted(str(one.get("name") or "") for one in found
                  if isinstance(one, dict) and one.get("isDir") and one.get("name"))


def send(game_dir: Path, host: str, port: int, *,
         chunk_bytes: int = CHUNK_BYTES,
         everything: bool = False,
         settings: bool = False,
         mobile_word: str = "",
         on_progress: Progress | None = None) -> int:
    """Put one game folder on the device, and say how many files it took.

    The folder is made first and every file is written under it, so a transfer that is
    interrupted leaves a partial game in a folder of its own rather than files loose at
    the top level where the device would try to read them.
    """
    contents = list(bundle_paths(game_dir, everything=everything, settings=settings,
                                 mobile_word=mobile_word))
    if not contents:
        raise ValueError(t("error.devices.nothing_sendable", game=game_dir.name))

    name = game_dir.name
    _ask(host, port, "POST", f"/folder?q={quote(name, safe='')}", timeout=ASK_SECONDS)

    total = len(contents)
    for index, (path, arcname) in enumerate(_ordered(contents), start=1):
        if on_progress:
            on_progress(index - 1, total, t("said.sending_file", game=name, file=arcname))
        _put(host, port, name, str(arcname).replace(os.sep, "/"), path,
             _content_of(path, arcname, contents, name), chunk_bytes)
    if on_progress:
        on_progress(total, total, t("said.game_sent", game=name))
    _refresh(host, port)
    return total


def remove(name: str, host: str, port: int) -> None:
    """Take one game off the device."""
    _ask(host, port, "POST", f"/delete?q={quote(name, safe='')}", timeout=ASK_SECONDS)
    _refresh(host, port)


def _ordered(contents: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """Files at the folder root first: a device reads a folder as soon as it appears,
    and the table and its metadata landing before the media is what makes it show up
    named rather than as an entry with nothing in it."""
    return sorted(contents, key=lambda pair: ("/" in str(pair[1]).replace(os.sep, "/"),
                                              str(pair[1])))


def _content_of(path: Path, arcname: str, contents: Iterable[tuple[Path, str]],
                name: str) -> bytes | None:
    """The bytes to send, or None to stream the file as it is on disk.

    Only the `.info` differs: what it describes has to match what the transfer actually
    carries, which is the same rule the downloadable archive follows and the same
    function that applies it.
    """
    if arcname != info_files.info_name(name):
        return None
    allowed = {str(one).replace(os.sep, "/") for _, one in contents}
    return prune_info(path.read_text(encoding="utf-8", errors="replace"),
                      allowed).encode("utf-8")


def _put(host: str, port: int, folder: str, arcname: str, path: Path,
         content: bytes | None, chunk_bytes: int) -> None:
    """One file, in chunks, at the offsets the device expects."""
    where = (f"/upload?q={quote(folder, safe='')}"
             f"&file={quote(arcname, safe='')}")
    if content is not None:
        _ask(host, port, "POST", f"{where}&offset=0&length={len(content)}",
             body=content, timeout=SEND_SECONDS)
        return
    size = path.stat().st_size
    if not size:
        # A zero-length file still has to exist on the far side, and a loop over an
        # empty file would never send anything.
        _ask(host, port, "POST", f"{where}&offset=0&length=0", timeout=SEND_SECONDS)
        return
    offset = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            _ask(host, port, "POST", f"{where}&offset={offset}&length={size}",
                 body=chunk, timeout=SEND_SECONDS)
            offset += len(chunk)


def _refresh(host: str, port: int) -> None:
    """Tell the device to look again. Best effort: the files are there either way, and
    a device that will not be told is still a device that was sent to."""
    try:
        _ask(host, port, "POST", "/command?cmd=refresh_tables", timeout=ASK_SECONDS)
    except Exception:
        logger.debug("%s would not reload its list", host, exc_info=True)


def _ask(host: str, port: int, method: str, path: str, *, body: bytes = b"",
         timeout: float = ASK_SECONDS, tries: int = 3) -> bytes:
    """One request, retried on a dropped connection.

    Retried rather than failed because a phone's wifi drops mid-transfer as a matter of
    course, and a whole game resent for one lost chunk is what a person notices.
    """
    where = urlparse(f"http://{host}:{int(port)}{path}")
    target = where.path + (f"?{where.query}" if where.query else "")
    try:
        http_client.require_reachable(f"http://{host}")
    except http_client.OfflineError as exc:
        raise DeviceUnreachableError(t(why_not(exc))) from exc
    last: Exception | None = None
    for attempt in range(tries):
        connection = http.client.HTTPConnection(host, int(port), timeout=timeout)
        try:
            connection.request(method, target, body=body,
                               headers={"Content-Length": str(len(body)),
                                        "Connection": "close"})
            answer = connection.getresponse()
            said = answer.read()
            if answer.status >= 400:
                logger.warning("%s refused %s %s: %s", host, method, where.path,
                               answer.status)
                raise DeviceUnreachableError(t("device.reason.rejected"))
            return said
        except DeviceUnreachableError:
            raise
        except Exception as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
        finally:
            connection.close()
    logger.warning("%s did not answer: %s", host, last)
    raise DeviceUnreachableError(t(why_not(last) if last else "device.reason.unreachable"))
