"""Which output shows each window: the desktop once the table is up, then the app's own
settings, then VPinFE's screens."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from common import apps
from common.host import launch

from . import adapters
from .adapters import Output, Screen

NOT_SHOWN = "capture.screen.not_shown"
NOT_SHOWN_LAST = "capture.screen.not_shown_last"


def _nothing(app_id: str, title: str) -> str:
    return ""


class Placer(NamedTuple):
    """The app that would play a table, the table it would play, and the hook's
    `placed_by` for it."""

    app: str
    table: str
    placed_by: str


@dataclass(frozen=True)
class Shown:
    """What the app that plays a table says of its windows, as its capture hook answers.
    `app` is the app's name, said to people. `placer` is None with no table."""

    app: str
    outputs: Mapping[str, str] = field(default_factory=dict)
    window: Callable[[str, str], str] = _nothing
    placer: Placer | None = None


def _asked(game: Any, table: str | None) -> tuple[Any, apps.Entry, dict[str, Any]] | None:
    try:
        found = launch.launched_by(game, table)
    except launch.LaunchUnavailableError:
        return None
    if found is None or found[0].capture is None:
        return None
    return found


def _placer(app: Any, entry: apps.Entry, settings: Mapping[str, Any]) -> Placer | None:
    table = entry.table or entry.key
    if not table:
        return None
    return Placer(apps.app_name(app.id), table, app.capture.placed_by(entry, settings))


def shown(game: Any = None, table: str | None = None) -> Shown | None:
    """For the app a launch of this table would run, or the default launcher's with no
    game. None where no app with a capture hook would play it."""
    found = _asked(game, table)
    if found is None:
        return None
    app, entry, settings = found
    return Shown(apps.app_name(app.id), dict(app.capture.outputs(entry, settings)),
                 app.capture.window, _placer(app, entry, settings))


def views(game: Any, table: str | None = None) -> frozenset[str]:
    """The views, besides its own, the app that plays this table can start it in for a
    recording. Empty where no app with a capture hook would play it."""
    found = _asked(game, table)
    if found is None:
        return frozenset()
    app, entry, settings = found
    return frozenset(app.capture.views(entry, settings))


def placer(game: Any, table: str | None = None) -> Placer | None:
    """`shown(game, table).placer`, without reading where the windows go."""
    found = _asked(game, table)
    return _placer(*found) if found is not None else None


def seen(adapter: adapters.Adapter, said: Shown) -> dict[str, str] | None:
    """The output each of the app's windows is on, as the desktop says, or None where it
    cannot say or shows no playfield window of the app's."""
    try:
        windows = adapter.windows()
    except (OSError, ValueError):
        return None
    found: dict[str, str] = {}
    for one in windows:
        window = said.window(one.app_id, one.title)
        if window:
            found.setdefault(window, one.output)
    return found if adapters.PLAYFIELD in found else None


@dataclass(frozen=True)
class Placing:
    """Everything that says where a window is, kept so it can be asked again once the
    desktop can say."""

    outputs: Sequence[Output]
    config: Any
    monitors: Sequence[Any]
    shown: Shown | None = None

    def screen(self, window: str, seen: Mapping[str, str] | None = None) -> Screen:
        named = {one.name: one for one in self.outputs if one.name}
        said = self.shown
        if said is not None and seen is not None:
            if window not in seen:
                return Screen(window, reason=NOT_SHOWN, params={"app": said.app})
            if seen[window] in named:
                return Screen(window, named[seen[window]])
        if said is not None and window in said.outputs:
            name = said.outputs[window]
            if not name:
                return Screen(window, reason=NOT_SHOWN, params={"app": said.app})
            if name in named:
                return Screen(window, named[name])
        return adapters.screen_of(window, self.outputs, self.config, self.monitors)

    def screens(self, seen: Mapping[str, str] | None = None) -> dict[str, Screen]:
        return {window: self.screen(window, seen) for window in adapters.WINDOWS}
