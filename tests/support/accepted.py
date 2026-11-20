"""The accepted list in a test: one of its own, and an Owner who presses Accept.

Extensions and themes VPinFE did not ship stay stopped until accepted, so a test about what
an extension does once it runs has to accept it first. `owner_accepts` does that the way
the Owner does, through `accepted.accept_extension`, for each extension a registry is about
to load. A test about the gate itself uses `isolated` and leaves the accepting to itself.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from common.config import paths
from common.extensions import host
from common.security import accepted


def isolated(test: unittest.TestCase) -> Path:
    """An accepted list that belongs to this test, so nothing another test accepted is
    accepted here. The first read still starts it from whatever the themes folder holds."""
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    path = Path(folder.name) / "accepted.json"
    patcher = mock.patch.object(paths, "ACCEPTED_PATH", path)
    patcher.start()
    test.addCleanup(patcher.stop)
    return path


def owner_accepts_theme(test: unittest.TestCase, key: str, address: str = "") -> None:
    """A theme the Owner has accepted, on an accepted list of this test's own."""
    isolated(test)
    accepted.accept_theme(key, address)


def owner_accepts(test: unittest.TestCase) -> Path:
    """Every extension a registry loads during this test is accepted as it declares itself,
    just before it is loaded. Anything VPinFE ships needs nothing."""
    path = isolated(test)
    real = host.Registry._register

    def accepting(self: Any, record: Any, *, gated: bool = True) -> None:
        if gated and record.manifest is not None and not accepted.is_official(record.origin):
            accepted.accept_extension(
                record.name, record.origin, folder=record.folder,
                version=record.manifest.version, scopes=record.manifest.scopes,
                capabilities=record.manifest.capabilities, play=record.manifest.play)
        real(self, record, gated=gated)

    patcher = mock.patch.object(host.Registry, "_register", accepting)
    patcher.start()
    test.addCleanup(patcher.stop)
    return path
