"""Where a reviewed support package goes: text for a post, and the links that open one.

Everything here is a pure function of the cleaned summary. Nothing is sent: both
destinations are addresses the person's own browser opens, and the person attaches the zip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode

DISCORD_URL = "https://discord.gg/SFBfA6Te2A"
GITHUB_NEW_ISSUE = "https://github.com/vpinfe/vpinfe/issues/new"
ISSUE_TEMPLATE = "support.yml"

DISCORD_LIMIT = 1500
# Browsers and GitHub both cut an address somewhere past a few thousand characters; the
# prefill is short facts, so this only stops a runaway value.
LINK_LIMIT = 1800
REPEATED_ERRORS = 3
ERROR_WIDTH = 110

_FACT = re.compile(r"^- \*\*(?P<label>[^*]+?):\*\*\s*(?P<value>.*)$")
_COUNTED = re.compile(r"^- \*\*(?P<count>\d+)x\*\*\s*(?P<text>.*)$")


@dataclass(frozen=True)
class Block:
    """One `##` or `###` section of the summary."""

    heading: str
    sub: str
    facts: tuple[tuple[str, str], ...]
    counted: tuple[tuple[int, str], ...]


def blocks(summary: str) -> list[Block]:
    found: list[Block] = []
    heading = sub = ""
    facts: list[tuple[str, str]] = []
    counted: list[tuple[int, str]] = []

    def close() -> None:
        if heading or sub or facts or counted:
            found.append(Block(heading, sub, tuple(facts), tuple(counted)))

    for line in summary.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            close()
            facts, counted = [], []
            if line.startswith("## "):
                heading, sub = line[3:].strip(), ""
            else:
                sub = line[4:].strip()
            continue
        match = _COUNTED.match(line)
        if match:
            counted.append((int(match["count"]), match["text"].strip()))
            continue
        match = _FACT.match(line)
        if match:
            facts.append((match["label"].strip(), match["value"].strip()))
    close()
    return found


def fact(summary: str, label: str, heading: str | None = None) -> str:
    """The first fact with this label, under this `##` heading when one is named."""
    for block in blocks(summary):
        if heading is not None and block.heading != heading:
            continue
        for name, value in block.facts:
            if name == label:
                return value
    return ""


def _screens(summary: str) -> int:
    return sum(1 for block in blocks(summary)
               if block.heading == "Displays And Layout" and block.sub.startswith("Screen "))


def _findings(summary: str) -> list[str]:
    for block in blocks(summary):
        if block.heading == "Displays And Layout" and block.sub == "Findings":
            return [f"{label}: {value}" for label, value in block.facts]
    return []


def _log_block(summary: str) -> Block | None:
    return next((block for block in blocks(summary)
                 if block.heading == "Health Now" and block.sub == "This Run's Log"), None)


def _clipped(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


def _device_line(summary: str) -> str:
    version = fact(summary, "Version", "VPinFE")
    build = fact(summary, "Build", "VPinFE")
    system = fact(summary, "Operating system", "This Device")
    windowing = fact(summary, "Windowing system", "This Device")
    head = " ".join(part for part in (f"VPinFE {version}" if version else "", f"({build})"
                                      if build and version else "") if part)
    where = ", ".join(part for part in (system, windowing) if part)
    return f"{head} on {where}" if head and where else head or where


def discord_summary(summary: str, limit: int = DISCORD_LIMIT) -> str:
    """The short post: what runs where, what the display check found, and the few errors
    that repeat. Always under `limit`; the zip carries the rest."""
    lines = ["**VPinFE support summary**"]
    device = _device_line(summary)
    if device:
        lines.append(device)
    screens = _screens(summary)
    if screens:
        lines.append(f"Screens found: {screens}")
    findings = _findings(summary)
    if findings:
        lines.append("Display findings:")
        lines += [f"- {_clipped(text, 160)}" for text in findings[:4]]
    log = _log_block(summary)
    if log is not None:
        counts = dict(log.facts)
        errors, warnings = counts.get("Errors"), counts.get("Warnings")
        if errors is not None or warnings is not None:
            lines.append(f"This run's log: {errors or 0} errors, {warnings or 0} warnings")
        repeated = sorted(log.counted, key=lambda one: -one[0])[:REPEATED_ERRORS]
        lines += [f"- {count}x {_clipped(text, ERROR_WIDTH)}" for count, text in repeated]
    closing = "The full package is attached."
    while len("\n".join([*lines, closing])) > limit and len(lines) > 1:
        lines.pop()
    return _clipped("\n".join([*lines, closing]), limit)


def github_fields(summary: str) -> dict[str, str]:
    """The only facts a new issue is prefilled with. A fixed list of non-identifying
    labels, so a value the cleanup missed cannot ride in through a free-text line."""
    wanted = {
        "version": fact(summary, "Version", "VPinFE"),
        "build": fact(summary, "Build", "VPinFE"),
        "os": fact(summary, "Operating system", "This Device"),
        "session": fact(summary, "Windowing system", "This Device"),
    }
    return {key: value for key, value in wanted.items() if value}


def github_link(summary: str) -> str:
    query = {"template": ISSUE_TEMPLATE, **github_fields(summary)}
    link = f"{GITHUB_NEW_ISSUE}?{urlencode(query, quote_via=quote)}"
    if len(link) > LINK_LIMIT:
        return f"{GITHUB_NEW_ISSUE}?{urlencode({'template': ISSUE_TEMPLATE})}"
    return link
