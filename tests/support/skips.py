"""What a test cannot construct on the platform it is running on.

Skipping is the honest answer where the *setup* is impossible, not the behaviour: the code
under one of these is fine on Windows, the fixture is not.

**Do not name a module here after a standard library one.** It shadows it for everything
that imports this package.
"""
from __future__ import annotations

import sys
import unittest

WINDOWS = sys.platform.startswith("win")

needs_posix_permissions = unittest.skipIf(
    WINDOWS,
    "chmod cannot make a directory read-only for its owner on Windows, so the fixture "
    "this asserts against cannot be built here")

needs_executable_bit = unittest.skipIf(
    WINDOWS, "a file's mode on Windows has no execute bit to set or read")

needs_colons_in_names = unittest.skipIf(
    WINDOWS, "Windows refuses a colon in a file name, and the fixture names a file or "
    "folder with one (a PCI address such as 0000:03:00.0, a title such as 'Name: Subtitle')")

needs_selectable_files = unittest.skipIf(
    WINDOWS, "select() takes only sockets on Windows, and the events it waits on are an "
    "evdev device's, read as a file descriptor")
