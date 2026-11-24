"""The device subject."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from manager.devices import devices as devices_page
from manager.workbench.core import _rows

# --- Device sections --------------------------------------------------------
#
# Each is a thin adapter: the rows themselves are `manager/devices/devices.py`'s, because what a
# device is and what may be asked of it belongs with the device, not with the panel that
# draws it. The panel's job here is the rail and the frame.

def _device(context: dict[str, Any]) -> dict[str, Any]:
    return context.get("device") or {}


async def _device_details(context: dict[str, Any]) -> None:
    """Everything a device is, in groups, read down in one go.

    Descriptive, then operational, then what this install holds about it: what a thing
    *is* comes before what can be done to it, and the record we keep of it is nobody's
    first question.
    """
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, await devices_page.detail_groups(context))


async def _device_software(context: dict[str, Any]) -> None:
    """What it is running, and whether there is a newer build to take."""
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, await devices_page.software_rows(context))


async def _device_capabilities(context: dict[str, Any]) -> None:
    """What it can be asked for, as that install declares it."""
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, devices_page.capability_rows(context))


async def _device_logs(context: dict[str, Any]) -> None:
    """What it has been writing down."""
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, await devices_page.log_rows(context))
