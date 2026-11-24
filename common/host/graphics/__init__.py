"""Graphics cards: which there are on this machine, and what each is doing.

The OS lists the cards and a reader reads each one. A reading the reader does not have is
left out of the card, never written as 0: a card that cannot report its temperature and a
cold one are different answers.

A card on the wire:

- `id` (stable within a run), `name`, `vendor` (`nvidia`, `amd`, `intel`, `apple`,
  `other`) and `source`, the reader that answered (`nvidia-smi`, `nvtop`, or the OS:
  `linux`, `macos`, `windows`)
- `busy` (percent), `memory_used` and `memory_total` (bytes; no total where the memory is
  shared with the system), `memory_percent` (from a reader with no byte counts),
  `temperature` (°C), `power` (W), `clock` (MHz), `fan_percent`, `fan_rpm`
- `reason`, instead of `source`, where nothing reads the card, with `detail` where the
  program said more, and `tool`, the id of the Tool whose absence or failure it is, so a
  page can point at the setting that fixes it

A lister may also set `pci`, the card's PCI address, which only matches a card to a
reader's answer and is not sent.
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
from collections import Counter
from typing import Any

from common.failures import why
from common.host import bundled_libraries, tools
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.graphics")

TIMEOUT = 3

_PCI_VENDORS = {0x10DE: "nvidia", 0x1002: "amd", 0x8086: "intel", 0x106B: "apple"}
_NAME_VENDORS = (("nvidia", "nvidia"), ("geforce", "nvidia"), ("quadro", "nvidia"),
                 ("amd", "amd"), ("radeon", "amd"), ("intel", "intel"), ("apple", "apple"))


class UnreadableError(Exception):
    """Nothing could be read: the reason a person is shown and, where the program said
    one, what it said. `tool` is the id of the Tool that is missing or does not run."""

    def __init__(self, reason: str, detail: str = "", tool: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.tool = tool


def vendor_from_id(number: int) -> str:
    return _PCI_VENDORS.get(number, "other")


def vendor_from_name(name: str) -> str:
    lowered = name.lower()
    return next((vendor for fragment, vendor in _NAME_VENDORS if fragment in lowered), "other")


def number(text: Any) -> float | None:
    """The figure in `"71C"`, `"54 %"` or `"142.3"`. Anything else - `[N/A]`, `Not
    Supported`, nothing - is a reading the source does not have."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    found = re.search(r"-?\d+(?:\.\d+)?", str(text or ""))
    return float(found.group()) if found else None


def only_present(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def program(tool: tools.Tool) -> str:
    """Where this device runs `tool` from. Raises `UnreadableError` where nothing here
    does, naming the Tool so its remedy can be offered."""
    found = tools.resolve(tool)
    if found.state is tools.State.FOUND and found.path is not None:
        return str(found.path)
    if found.state is tools.State.UNUSABLE:
        why_not = (found.probe.reason if found.probe is not None else "") or tools.FAILED
        raise UnreadableError(t("error.graphics.tool_unusable_here", tool=tool.name),
                              tools.words({"key": why_not}), tool.id)
    raise UnreadableError(t("error.graphics.tool_missing", tool=tool.name), tool=tool.id)


def run(program: str, arguments: list[str], tool: str) -> str:
    """What the program printed. Raises `UnreadableError` saying why it did not."""
    try:
        done = subprocess.run([program, *arguments], capture_output=True, text=True,
                              timeout=TIMEOUT, check=False,
                              env=bundled_libraries.for_child())
    except subprocess.TimeoutExpired:
        raise UnreadableError(t("error.graphics.tool_timeout", tool=tool)) from None
    except Exception as exc:  # noqa: BLE001 - a probe must not take the page with it
        raise UnreadableError(t("error.graphics.tool_failed", tool=tool), why(exc)) from exc
    text = (done.stdout or "").strip()
    if done.returncode != 0 or not text:
        said = (done.stderr or text).strip()
        if said:
            raise UnreadableError(t("error.graphics.tool_failed", tool=tool), said)
        raise UnreadableError(t("error.graphics.tool_said_nothing", tool=tool))
    return text


def _lister() -> Any:
    system = platform.system()
    if system == "Darwin":
        from common.host.graphics import darwin
        return darwin
    if system == "Linux":
        from common.host.graphics import linux
        return linux
    if system == "Windows":
        from common.host.graphics import windows
        return windows
    return None


def _listed() -> tuple[list[dict[str, Any]], str, str]:
    lister = _lister()
    if lister is None:
        return [], t("error.graphics.not_here", system=platform.system()), ""
    try:
        cards = lister.cards()
    except UnreadableError as exc:
        return [], exc.reason, exc.detail
    except Exception as exc:  # noqa: BLE001 - a reader that breaks is a reason, not a 500
        logger.debug("Could not list the graphics cards", exc_info=True)
        return [], t("error.graphics.tool_failed", tool=lister.__name__.rsplit(".", 1)[-1]), \
            why(exc)
    return (cards, "", "") if cards else ([], t("error.graphics.none"), "")


def needs_nvidia_smi() -> bool:
    """An NVIDIA card is listed, and nvidia-smi is what reads it."""
    return any(card.get("vendor") == "nvidia" for card in _listed()[0])


def needs_nvtop() -> bool:
    """A card of a vendor nothing built in reads is listed. One nvtop reads still counts,
    whatever its vendor: the row stays where it works."""
    return any(card.get("vendor") == "other" or card.get("source") == "nvtop"
               for card in _listed()[0])


def names() -> tuple[list[str], str]:
    """What About reports: the cards by name, and the reason where there are none."""
    cards, reason, _detail = _listed()
    return [str(card.get("name") or t("word.unknown")) for card in _told_apart(cards)], reason


def _told_apart(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cards that share a name are numbered in listed order, so two of one model read apart."""
    counts = Counter(str(card.get("name") or "") for card in cards)
    seen: Counter[str] = Counter()
    for card in cards:
        name = str(card.get("name") or "")
        if name and counts[name] > 1:
            seen[name] += 1
            card["name"] = t("metrics.gpu.same_name", name=name, number=seen[name])
    return cards


def read() -> dict[str, Any]:
    """`{cards, reason, detail}`. Each card goes to the first reader that answers for it,
    and a card nothing reads is still listed, with the reason."""
    cards, reason, detail = _listed()
    if not cards:
        return {"cards": [], "reason": reason, "detail": detail}
    failed = _overlay_nvidia_smi(cards)
    for card in cards:
        card.pop("pci", None)
        if "source" not in card and "reason" not in card:
            said = failed.get(card.get("vendor", "")) \
                or UnreadableError(t("error.graphics.not_read"))
            card.update(only_present({"reason": said.reason, "detail": said.detail or None,
                                      "tool": said.tool or None}))
    return {"cards": _told_apart(cards), "reason": "", "detail": ""}


def _overlay_nvidia_smi(cards: list[dict[str, Any]]) -> dict[str, UnreadableError]:
    """NVIDIA's own program reads an NVIDIA card better than anything else does. Matched
    by PCI address where the listing has one, else in the order both list them. Answers
    why it could not, by vendor."""
    from common.host.graphics import nvidia_smi

    mine = [card for card in cards if card.get("vendor") == "nvidia"]
    if not mine:
        return {}
    try:
        unused = nvidia_smi.read()
    except UnreadableError as exc:
        return {"nvidia": exc}
    except Exception:  # noqa: BLE001 - a reader that breaks is a reason, not a 500
        logger.debug("Could not read with nvidia-smi", exc_info=True)
        return {"nvidia": UnreadableError(t("error.graphics.tool_failed", tool=nvidia_smi.TOOL))}
    for card, pick in pair(mine, unused):
        kept = {key: value for key, value in card.items() if key in ("id", "pci")}
        card.clear()
        card.update(kept)
        card.update({key: value for key, value in pick.items() if key != "pci"})
    return {}


def pair(cards: list[dict[str, Any]], readings: list[dict[str, Any]]
         ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Each card with the reading that is its own: by PCI address, else by name, else in
    the order both list them where the counts agree. A card with an address never pairs
    by anything but it."""
    left, waiting = list(readings), []
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for card in cards:
        address = card.get("pci")
        pick = next((one for one in left if address and one.get("pci") == address), None)
        if pick is None and not address:
            pick = next((one for one in left if _same_name(one, card)), None)
        if pick is not None:
            left.remove(pick)
            pairs.append((card, pick))
        elif not address:
            waiting.append(card)
    if waiting and len(waiting) == len(left):
        pairs.extend(zip(waiting, left, strict=True))
    return pairs


def _same_name(one: dict[str, Any], other: dict[str, Any]) -> bool:
    first, second = str(one.get("name") or "").lower(), str(other.get("name") or "").lower()
    return bool(first and second and (first in second or second in first))
