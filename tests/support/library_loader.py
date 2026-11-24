"""Stand a library in front of every module that imported the loader.

`all_games` is bound into five modules by `from ... import`, so patching one of
them leaves the other three holding the real function - which then scans whatever root the
suite before it configured. That reads as a flaky test and is not.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

# The importers, plus the defining module for callers reaching it by attribute. An
# invariant fails when this list and the tree disagree, so a new importer cannot go
# unnoticed. Module-level importers only: a function-local import resolves through
# the defining module when it runs, so the first entry already covers it.
LOADER_SITES = (
    "common.games.game_repository",
    "common.games.exact_ties",
    "common.games.media_service",
    "frontend.api",
    "frontend.library_resolver",
)


@contextmanager
def library_of(games, sites=LOADER_SITES):
    """Every site answers `games` for the duration."""
    with ExitStack() as stack:
        for site in sites:
            stack.enter_context(patch(f"{site}.all_games", return_value=games))
        yield games


def start_library_of(test, games):
    """The same, for a setUp that cannot hold a `with` block open."""
    manager = library_of(games)
    manager.__enter__()
    test.addCleanup(manager.__exit__, None, None, None)
    return games
