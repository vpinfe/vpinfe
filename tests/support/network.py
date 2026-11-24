"""A test that stubs the transport lets a call reach the stub, wherever it is addressed.

The suite runs with VPINFE_OFFLINE set, so a call to any host outside this machine is
refused before it reaches `requests`. A test that stubs `requests` to stand in for a
service clears it for its own length and nothing else, and a call it did not stub still
fails the test.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from common.security import http_client


def stubbed(case: unittest.TestCase) -> None:
    patched = mock.patch.dict(os.environ, {http_client.OFFLINE: ""})
    patched.start()
    case.addCleanup(patched.stop)
