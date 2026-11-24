"""The release notes an update carries, read back for the versions a person has not seen.

`RELEASE_NOTES.md` holds the running build's notes. Each earlier release's notes are kept
as `releases/<tag>.md`, so a person who skips versions still reads every one they crossed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from common.config.paths import bundled

CURRENT_FILE = "RELEASE_NOTES.md"
ARCHIVE_FOLDER = "releases"

_RELEASE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?$")
_AREA = re.compile(r"^\*\*([^*]+)\*\*\s+[-–—]\s+(.*)$", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


@dataclass(frozen=True)
class Entry:
    text: str
    area: str = ""


@dataclass(frozen=True)
class Section:
    heading: str
    entries: list[Entry] = field(default_factory=list)


@dataclass(frozen=True)
class Release:
    version: str
    sections: list[Section] = field(default_factory=list)


def is_release(version: str) -> bool:
    """Whether this names a tagged release. A `dev-` build name is not one."""
    return bool(_RELEASE.match(str(version or "").strip()))


def is_prerelease(version: str) -> bool:
    """Whether this names a release that leads up to another, like `v3.0.0-beta.2`."""
    found = _RELEASE.match(str(version or "").strip())
    return bool(found and found.group(4))


def _key(version: str) -> tuple:
    found = _RELEASE.match(str(version or "").strip())
    if not found:
        return ()
    major, minor, patch, pre = found.groups()
    # A prerelease sorts before the release it leads to, and compares part by part.
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p)
                  for p in (pre or "").split(".") if p)
    return (int(major), int(minor), int(patch or 0), 0 if pre else 1, parts)


def newer(this: str, than: str) -> bool:
    """Whether `this` is a later release than `than`, a beta before its own release."""
    return _key(this) > _key(than)


def _plain(text: str) -> str:
    text = _LINK.sub(r"\1", text)
    return re.sub(r"\*\*([^*]+)\*\*|`([^`]+)`", lambda m: m.group(1) or m.group(2), text)


def _entry(lines: list[str]) -> Entry:
    joined = " ".join(part.strip() for part in lines).strip()
    found = _AREA.match(joined)
    if found:
        return Entry(text=_plain(found.group(2).strip()), area=found.group(1).strip())
    return Entry(text=_plain(joined))


def parse(text: str) -> list[Section]:
    """Each `###` section with its bullets, or its paragraphs where it holds no bullets.
    A bullet's wrapped lines join into one entry. The `##` title is not a section."""
    sections: list[tuple[str, list[list[str]]]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("### "):
            sections.append((line[4:].strip(), []))
            continue
        if not sections or line.startswith("#"):
            continue
        entries = sections[-1][1]
        if not line.strip():
            if entries and entries[-1]:
                entries.append([])
        elif line.startswith("- "):
            if entries and not entries[-1]:
                entries.pop()
            entries.append([line[2:]])
        elif entries and entries[-1]:
            entries[-1].append(line)
        else:
            if entries:
                entries.pop()
            entries.append([line])
    return [Section(heading, [_entry(lines) for lines in entries if lines])
            for heading, entries in sections]


def _read(path: Path) -> list[Section]:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def crossed(since: str, running: str, folder: Path | None = None,
            current: Path | None = None) -> list[Release]:
    """Notes for each release after `since`, up to and including `running`, newest first.

    Nothing recorded as `since` shows the running version alone: there is no telling how
    far back a person's last run was. The running version reads `RELEASE_NOTES.md`; the
    archived copy of it is the fallback when that file is not shipped.
    """
    folder = folder if folder is not None else bundled(ARCHIVE_FOLDER)
    current = current if current is not None else bundled(CURRENT_FILE)
    wanted = {running} if not since else set()
    if since and folder.is_dir():
        for path in folder.glob("*.md"):
            version = path.stem
            if is_release(version) and newer(version, since) and not newer(version, running):
                wanted.add(version)
    wanted.add(running)
    found: list[Release] = []
    for version in sorted(wanted, key=_key, reverse=True):
        sections = _read(current) if version == running else []
        if not sections:
            sections = _read(folder / f"{version}.md")
        if sections:
            found.append(Release(version, sections))
    return found
