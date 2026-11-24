"""The package a person is drafting: what was gathered, what they chose to change, and the
package those choices make.

Gathering and rebuilding block, so a caller on an event loop runs them off the loop; the
choices mutate under a lock and only mark the package stale, which `refresh` rebuilds. A
draft is one person's, held by the dialog that made it, and `drop` lets go of every cleaned
and original byte when that dialog closes - nothing here is kept for a later request.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from common.support import package


@dataclass
class Draft:
    logs: bool = True
    previous_log: bool = True
    settings: bool = True
    hide_tables: bool = False
    hide: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    removed: list[tuple[str, int]] = field(default_factory=list)
    raw: package.Raw | None = field(default=None, repr=False)
    made: package.Package | None = field(default=None, repr=False)
    stale: bool = False
    _gathered: tuple[bool, bool, bool] | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def options(self) -> package.Options:
        return package.Options(
            logs=self.logs, previous_log=self.previous_log, settings=self.settings,
            hide_tables=self.hide_tables, hide=tuple(self.hide),
            left_out=tuple(self.left_out), removed=tuple(self.removed))

    def gather(self) -> package.Package:
        """Read the device and the logs, unless the three parts asked for are the ones
        already read. Choices made against what was read before belong to it, so a new read
        starts them again."""
        wanted = (self.logs, self.previous_log, self.settings)
        with self._lock:
            if self.raw is None or wanted != self._gathered:
                self.raw = package.collect(self.options())
                self._gathered = wanted
                self.left_out.clear()
                self.removed.clear()
            return self.reassemble()

    def reassemble(self) -> package.Package:
        """Clean and zip again from what was read, without asking the device anything."""
        with self._lock:
            if self.raw is None:
                raise RuntimeError("nothing has been gathered")
            self.made = package.assemble(self.raw, self.options())
            self.stale = False
            return self.made

    def refresh(self) -> package.Package:
        """The package the choices so far make, built again only if one changed since."""
        with self._lock:
            if self.stale or self.made is None:
                return self.reassemble()
            return self.made

    def set_hide_tables(self, hidden: bool) -> None:
        with self._lock:
            self.hide_tables = hidden
            self.stale = True

    def hide_word(self, word: str) -> bool:
        """Add a word to hide. False for one that is empty or already there."""
        word = word.strip()
        with self._lock:
            if not word or word.lower() in (one.lower() for one in self.hide):
                return False
            self.hide.append(word)
            self.stale = True
            return True

    def unhide(self, word: str) -> None:
        with self._lock:
            self.hide = [one for one in self.hide if one != word]
            self.stale = True

    def remove_line(self, name: str, index: int) -> None:
        with self._lock:
            if name and (name, index) not in self.removed:
                self.removed.append((name, index))
                self.stale = True

    def restore_lines(self) -> None:
        with self._lock:
            self.removed.clear()
            self.stale = True

    def leave_out(self, name: str, out: bool) -> None:
        """Leave a log file out of the package, or put it back. A blank name is nothing."""
        with self._lock:
            if out and name and name not in self.left_out:
                self.left_out.append(name)
                self.stale = True
            elif not out and name in self.left_out:
                self.left_out.remove(name)
                self.stale = True

    def drop(self) -> None:
        with self._lock:
            self.raw = None
            self.made = None
            self._gathered = None
            self.stale = False
            self.hide.clear()
            self.left_out.clear()
            self.removed.clear()
