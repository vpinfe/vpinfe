"""A folder whose every look blocks, the way a stat on a dead network mount does."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

from common.config import path_state


@contextmanager
def never_answering(path: str) -> Iterator[mock.MagicMock]:
    """Looks at `path` block until the block ends; every other path is looked at as usual.
    Waits on the way out for the blocked look to finish, so no question is left in flight
    for the next test."""
    release = threading.Event()
    real = path_state._look

    def look(ask: path_state.Ask) -> path_state.PathState:
        if ask.path == path:
            release.wait()
        return real(ask)

    with mock.patch.object(path_state, "_look", side_effect=look) as looked:
        try:
            yield looked
        finally:
            release.set()
            deadline = time.monotonic() + 5
            while any(key[0] == path for key in path_state._PROBES) \
                    and time.monotonic() < deadline:
                time.sleep(0.01)
