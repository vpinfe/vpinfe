"""Setup's Appearance step: how the Manager looks, drawn as the miniatures Settings uses.

A choice repaints the page under the dialog as it is made, so the person sees the result
before pressing Next. The answer is written on Next, like every other step's.
"""

from __future__ import annotations

from typing import Any

from common.i18n import t
from manager.kit import appearance, appearance_picker, offload
from manager.kit.api import ApiClient

KEY = "appearance"


def _chosen(values: dict[str, Any]) -> str:
    return appearance.mode_or_default(str(values.get(KEY) or ""))


def repaint(values: dict[str, Any]) -> None:
    """The page in the mode the person has just picked. Drawn only, not saved."""
    appearance.repaint(_chosen(values))


def restore() -> None:
    """The page back in the look that is saved, for a Setup closed before the pick was."""
    appearance.repaint(appearance.configured_mode())


async def ask() -> dict[str, Any]:
    question = t("manager.setup.appearance.title")
    return {
        "title": question,
        "help": t("manager.setup.appearance.lead"),
        "fields": [{
            "key": KEY, "type": "choice", "label": question,
            "value": appearance.configured_mode(),
            "choices": appearance_picker.choices(),
        }],
        "changed": repaint,
    }


async def answer(values: dict[str, Any]) -> None:
    mode = _chosen(values)
    if mode != appearance.configured_mode():
        await offload.io(ApiClient().put_config,
                         {appearance.MODE_SECTION: {appearance.MODE_KEY: mode}})
