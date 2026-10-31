"""The support package: built once, in memory, from text that has already been cleaned.

What a person reviews is the exact bytes that leave. The zip is never rebuilt at download
time, and nothing here writes the unredacted text anywhere.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import secrets
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from common import i18n, vpinfe_version
from common.host import log_sources
from common.runtime import log_setup
from common.support import redact, sections
from common.support.collect import SECTIONS
from common.support.identity import build_identity
from common.support.redact import Identity

logger = logging.getLogger("vpinfe.common.support.package")

README = "README.txt"
SUMMARY = "summary" + ".md"
LOG = "vpinfe.log"
PREVIOUS_LOG = "vpinfe.log.1"
VISUAL_PINBALL_LOG = "vpinball.log"
REMOVED_LINE = "-- line removed by you --"

MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_BUILT_BYTES = 10 * 1024 * 1024
# A log over the cap keeps its start, where the run's own setup is, and its end, where
# whatever went wrong is.
HEAD_LINES = 300
HEAD_BYTES = 256 * 1024
TTL_SECONDS = 15 * 60
MAX_HELD = 2

# Fixed, so two packages of the same content are the same bytes.
ENTRY_TIME = (1980, 1, 1, 0, 0, 0)
CHECKSUM_LENGTH = 12

KIND_LABELS = {
    redact.HOME: "home folder",
    redact.USER: "user name",
    redact.HOST: "device name",
    redact.ADDRESS: "network address",
    redact.SECRET: "password, code or token",
    redact.IDENTIFIER: "identifier",
    redact.PERSON: "player name",
    redact.TABLE: "table name",
    redact.COMMAND: "command line",
    redact.PATH: "folder or drive",
    redact.CUSTOM: "word hidden on request",
}


class PackageTooLargeError(Exception):
    """The finished zip is over the cap on what one package may hold."""


@dataclass(frozen=True)
class Options:
    """What the person chose. Every part is in unless it is turned off, and a table name
    is kept unless hiding it is asked for."""

    logs: bool = True
    previous_log: bool = True
    settings: bool = True
    hide_tables: bool = False
    hide: tuple[str, ...] = ()
    # Log files the person took out, by name. The README and the summary always stay.
    left_out: tuple[str, ...] = ()
    # (file, 0-based line) pairs. A removed line is replaced, not deleted, so the numbers
    # of the lines after it do not move.
    removed: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class Part:
    """One file of the package, as cleaned text. `omitted` is how many log lines the cap
    left out."""

    name: str
    text: str
    omitted: int = 0

    @property
    def lines(self) -> int:
        return self.text.count("\n") + (0 if not self.text or self.text.endswith("\n") else 1)

    @property
    def size(self) -> int:
        return len(self.text.encode("utf-8"))


@dataclass
class Package:
    id: str
    filename: str
    parts: tuple[Part, ...]
    data: bytes
    checksum: str
    counts: dict[str, int]
    precautions: tuple[redact.Precaution, ...]
    # Kept for the person's own screen. Never in `data`, and never served.
    originals: dict[str, str] = field(repr=False)
    expires: float = 0.0

    def part(self, name: str) -> Part | None:
        return next((one for one in self.parts if one.name == name), None)

    @property
    def summary(self) -> str:
        found = self.part(SUMMARY)
        return found.text if found else ""


def review(made: Package, now: float | None = None) -> dict:
    """The package as a person reads it before sending: sizes and counts by kind, the
    summary, and the precautions. Never a replaced value."""
    now = time.monotonic() if now is None else now
    return {
        "id": made.id, "filename": made.filename, "checksum": made.checksum,
        "bytes": len(made.data), "expires_in": max(0, round(made.expires - now)),
        "files": [{"name": one.name, "bytes": one.size, "lines": one.lines,
                   "omitted": one.omitted} for one in made.parts],
        "counts": made.counts,
        "precautions": [{"why": one.why, "line": one.line} for one in made.precautions],
        "summary": made.summary,
    }


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _utc_offset(moment: datetime) -> str:
    minutes = round((moment.utcoffset() or moment - moment).total_seconds() / 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _file_name(moment: datetime) -> str:
    version = re.sub(r"[^\w.+-]", "-", vpinfe_version.get_version())
    return f"vpinfe-support-{version}-{moment:%Y%m%d-%H%M}.zip"


def _summary(options: Options, moment: datetime) -> str:
    blocks = [f"# VPinFE Support Summary\n\nMade {moment:%Y-%m-%d %H:%M} ({_utc_offset(moment)}). "
              "Log lines carry local time with no zone, so line them up with that offset.\n"]
    for section in SECTIONS:
        if section.id == "settings" and not options.settings:
            continue
        blocks.append(sections.rendered(section, sections.collected(section)))
    return "\n".join(blocks)


def _count_lines(path: Path) -> int:
    total = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += chunk.count(b"\n")
    return total


def _read_log(path: Path, budget: int) -> tuple[str, int, int]:
    """The log as text, how many lines the cap left out, and how many bytes were read."""
    limit = min(MAX_LOG_BYTES, budget)
    size = path.stat().st_size
    with open(path, "rb") as handle:
        if size <= limit:
            return handle.read().decode("utf-8", errors="replace"), 0, size
        head = handle.read(min(HEAD_BYTES, limit // 2)).decode("utf-8", errors="replace")
        kept = head.splitlines(keepends=True)[:HEAD_LINES]
        if kept and not kept[-1].endswith("\n"):
            kept.pop()
        head = "".join(kept)
        tail_bytes = limit - len(head.encode("utf-8"))
        handle.seek(size - tail_bytes)
        tail = handle.read().decode("utf-8", errors="replace")
    tail = tail.split("\n", 1)[1] if "\n" in tail else ""
    omitted = max(0, _count_lines(path) - len(kept) - tail.count("\n"))
    return f"{head}-- {omitted} lines skipped --\n{tail}", omitted, limit


def _log_jobs(options: Options) -> list[tuple[str, Path]]:
    if not options.logs:
        return []
    current = log_setup.log_file()
    current_name = current.name if current else ""
    found: dict[str, Path] = {}
    for source in log_sources.available():
        if source.record is not None:
            found.setdefault(VISUAL_PINBALL_LOG, source.path)
        elif source.name == current_name:
            found.setdefault(LOG, source.path)
        elif options.previous_log and source.name == f"{current_name}.1":
            found.setdefault(PREVIOUS_LOG, source.path)
    order = (LOG, VISUAL_PINBALL_LOG, PREVIOUS_LOG)
    return [(name, found[name]) for name in order if name in found]


def _logs(options: Options) -> list[Part]:
    parts = []
    budget = MAX_READ_BYTES
    for name, path in _log_jobs(options):
        if budget <= 0:
            break
        try:
            text, omitted, used = _read_log(path, budget)
        except OSError:
            logger.warning("A log could not be read for the support package")
            logger.debug("Log read failed: %s", name, exc_info=True)
            continue
        budget -= used
        parts.append(Part(name, text, omitted))
    return parts


def _without_lines(text: str, dropped: set[int]) -> str:
    if not dropped:
        return text
    lines = text.split("\n")
    for index in dropped:
        if 0 <= index < len(lines) and lines[index]:
            lines[index] = REMOVED_LINE
    return "\n".join(lines)


_REPEATED_ERROR = re.compile(r"^- \*\*\d+x\*\* ")


def _summary_text(summary: str, options: Options) -> str:
    """The summary quotes the run's log in its repeated-error bullets, so a log the person
    left out, or took a line from, cannot keep them: the summary is never regenerated."""
    touched = LOG in options.left_out or any(name == LOG for name, _ in options.removed)
    if not touched:
        return summary
    return "\n".join(line for line in summary.split("\n") if not _REPEATED_ERROR.match(line))


def _checksum(parts: list[Part]) -> str:
    digest = hashlib.sha256()
    for one in parts:
        digest.update(one.name.encode("utf-8") + b"\0" + one.text.encode("utf-8") + b"\0")
    return digest.hexdigest()[:CHECKSUM_LENGTH]


def _readme(parts: list[Part], counts: dict[str, int], checksum: str, moment: datetime) -> str:
    version = vpinfe_version.get_version()
    total = sum(counts.values())
    kinds = [f"  {counts[kind]:>5}  {KIND_LABELS[kind]}" for kind in redact.KINDS
             if counts.get(kind)]
    files = [f"  {one.name}" for one in parts]
    lines = [
        "VPinFE support package",
        "",
        f"Made by VPinFE {version} on {moment:%Y-%m-%d %H:%M} ({_utc_offset(moment)}).",
        "",
        "In this package:",
        *files,
        "",
        f"Private details replaced: {total}",
        *kinds,
        "",
        "Automatic cleanup is thorough but not perfect. Read it before you send it.",
        "Nothing was sent anywhere. This file was made on this device.",
        "",
        f"Checksum of the files above: {checksum}",
        "",
    ]
    return "\n".join(lines)


def _zip(parts: list[Part]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for one in parts:
            entry = zipfile.ZipInfo(one.name, date_time=ENTRY_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, one.text.encode("utf-8"))
    return buffer.getvalue()


@dataclass(frozen=True)
class Raw:
    """What was gathered, before any cleaning. It stays inside the process that gathered
    it and is never written, served or logged; `assemble` is the only reader."""

    moment: datetime
    identity: Identity = field(repr=False)
    summary: str = field(repr=False)
    logs: tuple[Part, ...] = field(repr=False)


def collect(options: Options | None = None) -> Raw:
    """Runs the probes and reads the logs. Which parts are gathered is the options'
    `logs`, `previous_log` and `settings`; what is hidden is decided by `assemble`."""
    options = options or Options()
    moment = _local_now()
    with i18n.in_source_language():
        summary = _summary(options, moment)
    return Raw(moment, build_identity(), summary, tuple(_logs(options)))


def assemble(raw: Raw, options: Options | None = None) -> Package:
    """Clean and zip. Every byte that reaches the zip went through one `Redactor`, so a
    value has the same placeholder in every file."""
    options = options or Options()
    moment = raw.moment
    redactor = redact.Redactor(raw.identity, hide_tables=options.hide_tables,
                               hide=options.hide)
    body = [Part(SUMMARY, redactor.clean(_summary_text(raw.summary, options)))]
    for one in raw.logs:
        if one.name in options.left_out:
            continue
        dropped = {line for name, line in options.removed if name == one.name}
        cleaned = _without_lines(redactor.clean(one.text), dropped)
        body.append(Part(one.name, cleaned, one.omitted))
    counts = {kind: redactor.ledger.counts[kind] for kind in redact.KINDS
              if redactor.ledger.counts[kind]}
    checksum = _checksum(body)
    parts = [Part(README, _readme(body, counts, checksum, moment)), *body]
    data = _zip(parts)
    if len(data) > MAX_BUILT_BYTES:
        raise PackageTooLargeError
    made = Package(secrets.token_urlsafe(24), _file_name(moment), tuple(parts), data,
                   checksum, counts, tuple(redactor.ledger.precautions),
                   dict(redactor.ledger.originals))
    logger.info("Support package built: %d files, %d bytes, replaced %s", len(parts),
                len(data), ", ".join(f"{kind}={number}" for kind, number in counts.items())
                or "nothing")
    return made


def build_package(options: Options | None = None) -> Package:
    options = options or Options()
    return assemble(collect(options), options)


_held: OrderedDict[str, Package] = OrderedDict()
_lock = threading.Lock()


def _drop_expired(now: float) -> None:
    for key in [key for key, one in _held.items() if one.expires <= now]:
        del _held[key]


def hold(made: Package, now: float | None = None) -> Package:
    """Keep a package for the review, at most `MAX_HELD` of them and `TTL_SECONDS` each."""
    now = time.monotonic() if now is None else now
    made.expires = now + TTL_SECONDS
    with _lock:
        _drop_expired(now)
        _held[made.id] = made
        while len(_held) > MAX_HELD:
            _held.popitem(last=False)
    return made


def held(package_id: str, now: float | None = None) -> Package | None:
    """The package under this id, or None when it is unknown, expired or pushed out."""
    now = time.monotonic() if now is None else now
    with _lock:
        _drop_expired(now)
        return _held.get(package_id)


def forget(package_id: str) -> None:
    with _lock:
        _held.pop(package_id, None)
