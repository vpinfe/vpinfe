"""This machine's screens, described the same way wherever one is picked."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from common.host import display_service
from common.i18n import t
from manager.kit import panel

_POSITIONED = re.compile(r"(?P<name>.+?)\s*\[\s*(?P<x>-?\d+),\s*(?P<y>-?\d+)\s*\]")


def connected() -> list[Any]:
    """The screens the frontend would open its windows on now, in its order. Blocking."""
    return list(display_service.get_display_monitors(refresh=True))


@dataclass(frozen=True)
class Screen:
    """A screen as a person counts and names it. The first is 1; a window stores it as
    `number - 1`, the index the frontend opens its windows by."""
    number: int
    name: str
    width: int
    height: int
    x: int
    y: int

    @property
    def size(self) -> str:
        return t("manager.screens.size", width=self.width, height=self.height)

    @property
    def said(self) -> str:
        """Its model name, else its size: the next thing a person knows it by."""
        return self.name or self.size


def numbered(monitors: Sequence[Any], names: Sequence[str]) -> list[Screen]:
    return [Screen(at + 1, str(names[at]) if at < len(names) else "",
                   int(monitor.width), int(monitor.height), int(monitor.x), int(monitor.y))
            for at, monitor in enumerate(monitors)]


def described(screen: Any) -> str:
    return t("manager.screens.at", width=screen.width, height=screen.height,
             x=screen.x, y=screen.y)


def reported(names: Iterable[str], screens: Iterable[Any]) -> dict[str, str]:
    at = {(screen.x, screen.y): screen for screen in screens}
    labels = {}
    for name in names:
        found = _POSITIONED.fullmatch(name)
        screen = at.get((int(found["x"]), int(found["y"]))) if found else None
        labels[name] = (name if found is None or screen is None else
                        t("manager.screens.named", name=found["name"], screen=described(screen)))
    return labels


def choices(screens: Sequence[Any], value: str, *, blank: bool) -> dict[str, str]:
    """Each screen by its number, and a stored number no screen answers to."""
    offered = {"": t("word.none")} if blank or not value else {}
    offered.update({str(number): described(screen) for number, screen in enumerate(screens)})
    if value not in offered:
        offered[value] = t("manager.screens.numbered",
                           number=int(value) + 1 if value.isdigit() else value)
    return offered


def mark(screens: Sequence[Any], value: str) -> dict[str, str]:
    if not value or (value.isdigit() and int(value) < len(screens)):
        return {}
    return {"state": "missing", "reason": t("manager.screens.not_connected")}


def picker(screens: Sequence[Any], value: Any, save: Callable[[Any], Any], *,
           blank: bool, disabled: bool = False,
           refill: Callable[[], None] | None = None) -> Callable[[], None]:
    """Stores the number, or "" for no window."""
    said = "" if value is None else str(value).strip()
    found = mark(screens, said)

    async def pick(event: Any) -> None:
        chosen = str(event.value or "")
        if await save(int(chosen) if chosen else "") and found and refill is not None:
            refill()

    return panel.select(choices(screens, said, blank=blank), said, pick, disabled=disabled,
                        status=panel.value_state(found.get("state", ""),
                                                 found.get("reason", "")))
