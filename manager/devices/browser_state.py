"""What a frontend browser plays, where it is worth noticing - one drawing for the Devices
page and Settings > Frontend > Browser."""

from __future__ import annotations

from typing import Any

from common.host import frontend_browser
from common.i18n import t
from manager.kit import panel, states

# `plays` is not here: the normal case draws nothing.
STATES = {
    "no_h264": {"label": t("manager.devices.browser_no_h264"), "tier": "warn",
                "why": t("frontend_browser.finding.no_h264")},
    "no_video": {"label": t("manager.devices.browser_no_video"), "tier": "bad",
                 "why": t("frontend_browser.finding.no_video")},
    "no_browser": {"label": t("manager.devices.browser_no_browser"), "tier": "bad",
                   "why": t("frontend_browser.finding.no_browser")},
    "unknown": {"label": t("manager.devices.browser_not_checked"), "tier": "unknown",
                "why": t("manager.devices.browser_checked_when")},
}


def _one(key: str, tone: str) -> states.State:
    return states.State(key, STATES[key]["label"], STATES[key]["why"], tone=tone)


# The grid draws every row, so unlike `STATES` it has the normal case: Plays is a tick.
TABLE = states.Table(
    covers=t("manager.devices.browser.help"),
    states=(
        states.State(frontend_browser.PLAYS, t("manager.devices.browser_plays"),
                     t("manager.devices.browser_plays.help"), glyph=states.TICK),
        _one(frontend_browser.NO_H264, states.WARN),
        _one(frontend_browser.NO_VIDEO, states.BAD),
        _one(frontend_browser.NO_BROWSER, states.BAD),
        states.State(frontend_browser.UNKNOWN, STATES[frontend_browser.UNKNOWN]["label"],
                     STATES[frontend_browser.UNKNOWN]["why"], glyph=states.UNKNOWN)),
    blank=states.State("", t("manager.devices.browser_unreported"),
                       t("manager.devices.browser_unreported.help")))


def value(name: str, state: str, unnamed: str) -> Any:
    """The browser's name as text, with a chip beside it only for a state worth noticing.
    `unnamed` stands in where no name was reported and there is no chip."""
    limited = STATES.get(state)
    if limited is None:
        return name or unnamed
    return panel.state(limited["label"], limited["tier"], beside=name, hint=limited["why"])
