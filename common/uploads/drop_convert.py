"""The pictures, sounds and videos of one drop that are converted on the way in.

A drop's files are read where they are. A sheet asks what each one would become, the plan
carries the answer on the asset, and the import converts every one of them before it writes
anything: a conversion that fails or is stopped adds nothing.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from common import atomic_write, service_errors
from common.failures import why
from common.games import media_convert
from common.games.media_files import SkipError
from common.host import launch_state
from common.i18n import t
from common.runtime import shutdown
from common.uploads import working_folder
from common.uploads.asset_analyzer_service import (
    DetectedAsset,
    SourceEntry,
    open_source,
)
from common.uploads.conversion import Conversion

logger = logging.getLogger("vpinfe.common.uploads.drop_convert")

_PAUSE_SECONDS = 5

# What a part of the import is doing, as `Job.parts` carries it.
WAITING = "waiting"
WORKING = "working"
DONE = "done"

Said = Callable[[str, str, float], None]


class StoppedError(Exception):
    """The person stopped the import before every file was ready."""


class Drop:
    """The conversions of one upload session: what each convertible file would be written
    as, and the converting of them all."""

    def __init__(self, source: Path, folder: Path, convert: Mapping[str, Any] | None) -> None:
        convert = convert or {}
        self.source = source
        self.folder = folder
        self.rows: Mapping[str, Mapping[str, Any]] = convert.get("rows") or {}
        self.wait = bool(convert.get("wait", True))
        self.devices = media_convert.default_devices()
        self.ffmpeg = media_convert.Ffmpeg()

    def row(self, member: str, kind: str) -> dict[str, Any]:
        """What the sheet chose for this file, or where FFmpeg is missing for a kind that
        needs it, that it is kept as it is."""
        row = dict(self.rows.get(member) or {})
        if media_convert.needs_ffmpeg(kind) and not self.ffmpeg.usable:
            row["to"] = media_convert.AS_IS
        return row

    def path_of(self, entry: SourceEntry) -> Path:
        """The file as a real path, taken out of the drop once. The same name is the same
        file for the life of the session."""
        suffix = PurePosixPath(entry.arcname).suffix.lower()
        kept = self.folder / "convert" / (
            hashlib.blake2b(entry.arcname.encode(), digest_size=8).hexdigest() + suffix)
        if kept.is_file():
            return kept
        reader = open_source(self.source)
        try:
            with atomic_write.staged_for(kept) as partial:
                reader.extract_member(entry.path, partial)
        finally:
            reader.close()
        return kept

    def conversion_for(self, asset: DetectedAsset) -> Conversion | None:
        """What this media file is written as, or None where it needs no converting."""
        entry = asset.entries[0]
        suffix = PurePosixPath(entry.arcname).suffix.lower()
        kind = media_convert.kind_of(suffix)
        if not kind:
            return None
        row = self.row(entry.arcname, kind)
        keep = Conversion(kind, suffix, as_is=True, choices=row)
        if media_convert.needs_ffmpeg(kind) and not self.ffmpeg.usable:
            return Conversion(kind, suffix, as_is=True, choices=row,
                              blocked=t("error.media_convert.no_ffmpeg"))
        if row.get("to") == media_convert.AS_IS:
            return keep
        try:
            item = media_convert.plan_staged(self.path_of(entry), row, self.devices, self.ffmpeg)
        except (SkipError, service_errors.ServiceError, OSError) as exc:
            return Conversion(kind, suffix, as_is=True, choices=row, blocked=why(exc))
        return Conversion(kind, media_convert.target_suffix(item), choices=row)

    def view_of(self, asset: DetectedAsset) -> dict[str, Any] | None:
        """What the row of this file shows and offers, or None for a file that converts
        to nothing."""
        entry = asset.entries[0]
        suffix = PurePosixPath(entry.arcname).suffix.lower()
        kind = media_convert.kind_of(suffix)
        if not kind:
            return None
        row = self.row(entry.arcname, kind)
        try:
            source = self.path_of(entry)
            return media_convert.staged_view(source, row, self.devices, self.ffmpeg)
        except (SkipError, service_errors.ServiceError, OSError) as exc:
            logger.warning("Could not read %s to convert it", entry.arcname, exc_info=True)
            return {"kind": kind, "from": suffix[1:].upper(), "to": media_convert.AS_IS,
                    "options": [], "settings": [], "line": "", "note": "",
                    "problem": why(exc), "reencodes": False}

    def lacks_ffmpeg_for(self, views: Sequence[Mapping[str, Any] | None]) -> bool:
        """Whether FFmpeg is missing and a file in the drop needs it. A drop of pictures does
        not, since those are converted without it."""
        return not self.ffmpeg.usable and any(
            one and media_convert.needs_ffmpeg(str(one.get("kind") or "")) for one in views)

    def ffmpeg_state(self) -> str:
        return str(self.ffmpeg.found.state.value)

    def convert_all(self, assets: Sequence[DetectedAsset], said: Said,
                    halted: Callable[[], bool]) -> tuple[dict[str, Path], Callable[[], None]]:
        """Every file to be converted, converted to a scratch folder: the files by their
        name in the drop, and what removes the folder. Raises where one cannot be
        converted, after removing it, and `StoppedError` where the person stopped it."""
        scratch = working_folder.make("convert-")

        def clean() -> None:
            shutil.rmtree(scratch, ignore_errors=True)

        ready: dict[str, Path] = {}
        work = [asset for asset in assets if asset.convert and not asset.convert.as_is]
        for asset in work:
            said(asset.entries[0].arcname, WAITING, 0.0)
        try:
            for number, asset in enumerate(work):
                self._one(number, asset, scratch, ready, said, halted)
        except BaseException:
            clean()
            raise
        return ready, clean

    def _one(self, number: int, asset: DetectedAsset, scratch: Path, ready: dict[str, Path],
             said: Said, halted: Callable[[], bool]) -> None:
        entry, conversion = asset.entries[0], asset.convert
        assert conversion is not None
        member = entry.arcname
        if conversion.kind == media_convert.VIDEO:
            self._wait_for_table(member, said, halted)
        if halted() or shutdown.requested():
            raise StoppedError
        said(member, WORKING, 0.0)
        out = scratch / f"{number}{conversion.suffix}"
        try:
            media_convert.convert_staged(
                self.path_of(entry), conversion.choices, out, self.devices, self.ffmpeg,
                lambda fraction: said(member, WORKING, fraction), halted)
        except SkipError as exc:
            if halted():
                raise StoppedError from exc
            reason = why(exc)
            raise service_errors.RefusedError(t(
                "error.uploads.convert_failed", name=PurePosixPath(member).name,
                reason=reason), details={"member": member, "reason": reason}) from exc
        ready[member] = out
        said(member, DONE, 1.0)

    def _wait_for_table(self, member: str, said: Said, halted: Callable[[], bool]) -> None:
        while (self.wait and launch_state.current().launching and not halted()
               and not shutdown.requested()):
            said(member, WAITING, 0.0)
            time.sleep(_PAUSE_SECONDS)
