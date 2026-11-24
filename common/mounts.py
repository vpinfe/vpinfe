"""Which network share a folder is on, and whether it is mounted right now.

Read from the mount table the OS keeps in memory, so asking contacts no server. Only a
share the OS mounted is reported; VPinFE mounts nothing itself.
"""

from __future__ import annotations

import logging
import ntpath
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

logger = logging.getLogger("vpinfe.common.mounts")

NFS = "nfs"
SMB = "smb"
PROTOCOLS = {"nfs": NFS, "nfs4": NFS, "cifs": SMB, "smb3": SMB, "smbfs": SMB}

FSTAB = Path("/etc/fstab")
# What macOS appends to a server it found through Bonjour.
_BONJOUR = "._smb._tcp.local"
# Windows: a drive letter remembered as a network connection that is not connected now.
_ERROR_CONNECTION_UNAVAIL = 1201


@dataclass(frozen=True)
class Mount:
    source: str
    point: str
    fstype: str


@dataclass(frozen=True)
class Origin:
    protocol: str
    server: str
    share: str
    # Where this device mounts it.
    point: str

    @property
    def source(self) -> str:
        """The share as it is written for its protocol."""
        if self.protocol == NFS:
            return f"{self.server}:{self.share}"
        return f"//{self.server}/{self.share}"

    def as_dict(self) -> dict[str, str]:
        return {"protocol": self.protocol, "server": self.server,
                "share": self.share, "point": self.point}

    @classmethod
    def from_dict(cls, raw: Any) -> Origin | None:
        if not isinstance(raw, dict):
            return None
        fields = {key: str(raw.get(key, "") or "").strip()
                  for key in ("protocol", "server", "share", "point")}
        if fields["protocol"] not in (NFS, SMB) or not fields["server"] \
                or not fields["point"]:
            return None
        return cls(**fields)


@dataclass(frozen=True)
class Where:
    # The share the folder is on, or None for a folder on this device.
    origin: Origin | None = None
    # False when the folder belongs on a share that is not mounted now.
    connected: bool = True


def origin_of_mount(mount: Mount) -> Origin | None:
    protocol = PROTOCOLS.get(mount.fstype.lower())
    if protocol is None:
        return None
    server, share = (_nfs_source(mount.source) if protocol == NFS
                     else _smb_source(mount.source))
    if not server:
        return None
    return Origin(protocol=protocol, server=server, share=share, point=mount.point)


def _nfs_source(source: str) -> tuple[str, str]:
    if source.startswith("["):
        server, _, share = source[1:].partition("]:")
        return server, share
    server, _, share = source.partition(":")
    return server, share


def _smb_source(source: str) -> tuple[str, str]:
    host, _, share = source.replace("\\", "/").lstrip("/").partition("/")
    server = unquote(host.rpartition("@")[2]).rstrip(".")
    return server.removesuffix(_BONJOUR), unquote(share)


def _covers(point: str, path: str) -> bool:
    point = point.rstrip("/")
    return path == point or path.startswith(point + "/")


def origin_of(path: str, table: Iterable[Mount]) -> Origin | None:
    """The share a path is on, from the mount nearest to it; None when that mount is on
    this device. A later mount at the same point is the one on top."""
    nearest: Mount | None = None
    for mount in table:
        if _covers(mount.point, path) and \
                (nearest is None or len(mount.point) >= len(nearest.point)):
            nearest = mount
    return origin_of_mount(nearest) if nearest is not None else None


def mounted() -> list[Mount]:
    try:
        import psutil
    except ImportError:
        return []
    try:
        partitions = psutil.disk_partitions(all=True)
    except (psutil.Error, OSError, SystemError):
        logger.debug("Could not read the mount table", exc_info=True)
        return []
    return [Mount(one.device, one.mountpoint, one.fstype) for one in partitions]


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda found: chr(int(found.group(1), 8)), field)


def fstab(text: str) -> list[Mount]:
    """The mounts /etc/fstab says belong here, whether they are up or not."""
    held = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0].startswith("#"):
            continue
        held.append(Mount(_unescape(fields[0]), _unescape(fields[1]), fields[2]))
    return held


def _configured() -> list[Mount]:
    if not sys.platform.startswith("linux"):
        return []
    try:
        return fstab(FSTAB.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def _expected(path: str, recorded: Origin | None) -> Origin | None:
    """The share a path belongs on when nothing is mounted there: what was seen there
    before, or what fstab says goes there."""
    candidates = [origin for origin in
                  [recorded, *(origin_of_mount(one) for one in _configured())]
                  if origin is not None and _covers(origin.point, path)]
    return max(candidates, key=lambda origin: len(origin.point), default=None)


def where(path: str, recorded: Origin | None = None) -> Where:
    """Which share a path is on and whether it is mounted now. Reads the mount table and
    nothing else."""
    if sys.platform == "win32":
        return _windows_where(path)
    live = mounted()
    origin = origin_of(path, live)
    if origin is not None:
        return Where(origin)
    expected = _expected(path, recorded)
    if expected is None or any(mount.point == expected.point and origin_of_mount(mount)
                               for mount in live):
        return Where()
    return Where(expected, connected=False)


def _windows_where(path: str) -> Where:
    drive, _ = ntpath.splitdrive(path)
    if drive.startswith(("\\\\", "//")):
        server, share = _smb_source(drive)
        return Where(Origin(SMB, server, share, drive)) if server and share else Where()
    if len(drive) == 2 and drive.endswith(":"):
        remote, connected = mapped_drive(drive)
        server, share = _smb_source(remote)
        if server and share:
            return Where(Origin(SMB, server, share, drive), connected=connected)
    return Where()


def mapped_drive(drive: str) -> tuple[str, bool]:
    """The share a Windows drive letter is mapped to, and whether it is connected now."""
    import ctypes
    from ctypes import wintypes

    buffer = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(len(buffer))
    try:
        result = ctypes.windll.mpr.WNetGetConnectionW(  # type: ignore[attr-defined]
            drive, buffer, ctypes.byref(size))
    except (AttributeError, OSError):
        return "", True
    if result == 0:
        return buffer.value, True
    if result == _ERROR_CONNECTION_UNAVAIL:
        return buffer.value, False
    return "", True
