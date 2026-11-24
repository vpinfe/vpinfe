"""Why something failed, in words, for the line under a failure's message."""

from __future__ import annotations

import errno
import os
import socket
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import requests

from common.i18n import t
from common.service_errors import CertificateChangedError

# errno: the key with no path, and the key naming the path.
_FILES: dict[int, tuple[str, str]] = {
    errno.EACCES: ("said.why.no_permission", "said.why.no_permission_at"),
    errno.EPERM: ("said.why.no_permission", "said.why.no_permission_at"),
    errno.ENOSPC: ("said.why.disk_full", "said.why.disk_full_at"),
    errno.EDQUOT: ("said.why.disk_full", "said.why.disk_full_at"),
    errno.EROFS: ("said.why.read_only", "said.why.read_only_at"),
    errno.ENOENT: ("said.why.nothing_there", "said.why.nothing_at"),
    errno.EEXIST: ("said.why.already_there", "said.why.already_at"),
    errno.ENOTDIR: ("said.why.not_folder", "said.why.not_folder_at"),
    errno.EISDIR: ("said.why.is_folder", "said.why.is_folder_at"),
    errno.EBUSY: ("said.why.in_use", "said.why.in_use_at"),
    errno.ETXTBSY: ("said.why.in_use", "said.why.in_use_at"),
    errno.ENAMETOOLONG: ("said.why.long_name", "said.why.long_name"),
    errno.ELOOP: ("said.why.link_loops", "said.why.link_loops_at"),
}

# SQLite's primary result code (the low byte of the extended one) and the key saying it;
# 0 is every code not listed.
_DATABASE: dict[int, str] = {
    0: "said.why.db_unread",
    5: "said.why.in_use",
    6: "said.why.in_use",
    8: "said.why.read_only",
    11: "said.why.db_damaged",
    13: "said.why.disk_full",
    14: "said.why.db_unopened",
    26: "said.why.not_database",
}

_UNREACHABLE = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EHOSTDOWN, errno.ENETDOWN}

# The key with no host, and the key naming the host.
_TIMED_OUT = ("said.why.timed_out", "said.why.timed_out_at")
_NOTHING_ANSWERS = ("said.why.nothing_answers", "said.why.no_answer_at")
_NOT_REACHED = ("said.why.unreachable", "said.why.unreachable_at")


def why(exc: BaseException, at: str | os.PathLike[str] = "") -> str:
    """The failure in words where it is one a person can act on, else the exception's text.

    Never empty, and never a sentence built around the exception: it is the whole line.
    `at` is the URL or path the caller was reaching, used where the exception does not
    carry its own.
    """
    exc = _unwrapped(exc)
    host, path = _where(at)
    if upstream(exc):
        return _unanswered(exc, host)
    if isinstance(exc, OSError) and _errno(exc) in _FILES:
        bare, named = _FILES[_errno(exc)]
        path = _path(exc.filename) or path
        return t(named, path=path) if path else t(bare)
    if isinstance(exc, sqlite3.Error):
        return t(_DATABASE.get(getattr(exc, "sqlite_errorcode", 0) & 0xFF, _DATABASE[0]))
    return _raw(exc)


def _errno(exc: OSError) -> int:
    """The exception's errno, with what Windows reports under the wrong one corrected.

    Opening a folder as a file fails there with EACCES, and opening a link that leads
    back to itself with EINVAL. Both come from the C runtime, which sets no winerror,
    so a refused folder (which does set one) is not mistaken for a file.
    """
    if os.name == "nt" and exc.filename and getattr(exc, "winerror", None) is None:
        path = _path(exc.filename)
        if exc.errno == errno.EACCES and os.path.isdir(path):
            return errno.EISDIR
        if exc.errno == errno.EINVAL and _loops(path):
            return errno.ELOOP
    return exc.errno or 0


def _loops(path: str) -> bool:
    if not os.path.islink(path):
        return False
    try:
        os.path.realpath(path, strict=True)
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError):
        return True
    return False


def upstream(exc: BaseException) -> bool:
    """Whether another machine failed: it did not answer, could not be reached, or
    answered with an error. False for a failure on this one, such as a refused write."""
    exc = _unwrapped(exc)
    return isinstance(exc, (requests.RequestException, URLError, TimeoutError,
                            socket.timeout, ConnectionRefusedError, socket.gaierror)) \
        or (isinstance(exc, OSError) and exc.errno in _UNREACHABLE)


def _unwrapped(exc: BaseException) -> BaseException:
    """urllib's error around the one that happened, which may be a file's."""
    while isinstance(exc, URLError) and isinstance(exc.reason, BaseException):
        exc = exc.reason
    return exc


def _unanswered(exc: BaseException, host: str) -> str:
    if isinstance(exc, CertificateChangedError):
        return _raw(exc)
    if isinstance(exc, requests.RequestException):
        return _network(exc, host) or _raw(exc)
    if isinstance(exc, HTTPError):
        return _answered(exc.code, urlsplit(str(exc.url or "")).hostname or host) \
            or _raw(exc)
    if isinstance(exc, URLError):
        return str(exc.reason or "") or _raw(exc)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return _named(_TIMED_OUT, host)
    if isinstance(exc, ConnectionRefusedError):
        return _named(_NOTHING_ANSWERS, host)
    return _named(_NOT_REACHED, host)


def _where(at: object) -> tuple[str, str]:
    """(host, path): a URL gives its host, anything else is a path."""
    text = _path(at)
    try:
        parts = urlsplit(text)
        if parts.scheme and parts.netloc:
            return parts.hostname or "", ""
    except ValueError:
        pass
    return "", text


def _named(keys: tuple[str, str], host: str) -> str:
    return t(keys[1], host=host) if host else t(keys[0])


def _raw(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


def _path(filename: object) -> str:
    return os.fsdecode(filename) if isinstance(filename, (str, bytes, os.PathLike)) else ""


def _host(exc: requests.RequestException) -> str:
    url = getattr(exc.request, "url", None) or getattr(exc.response, "url", None)
    return (urlsplit(str(url)).hostname or "") if url else ""


def _wrapped(exc: BaseException) -> list[BaseException]:
    """The exception and what it wraps: requests keeps the socket's error in `args`."""
    found: list[BaseException] = []
    waiting: list[object] = [exc]
    while waiting:
        one = waiting.pop()
        if isinstance(one, BaseException) and not any(one is seen for seen in found):
            found.append(one)
            waiting += [one.__cause__, getattr(one, "reason", None), *one.args]
    return found


def _network(exc: requests.RequestException, reaching: str) -> str:
    host = _host(exc) or reaching
    if isinstance(exc, requests.Timeout):
        return _named(_TIMED_OUT, host)
    if isinstance(exc, requests.ConnectionError):
        if any(isinstance(one, ConnectionRefusedError) for one in _wrapped(exc)):
            return _named(_NOTHING_ANSWERS, host)
        return _named(_NOT_REACHED, host)
    if not isinstance(exc, requests.HTTPError):
        return ""
    return _answered(getattr(exc.response, "status_code", 0), host)


def _answered(status: int, host: str) -> str:
    if not host:
        return ""
    if status in (404, 410):
        return t("said.why.not_found_at", host=host)
    if status in (400, 401, 403):
        return t("said.why.refused_at", host=host)
    if status >= 500:
        return t("said.why.in_trouble_at", host=host)
    return ""
