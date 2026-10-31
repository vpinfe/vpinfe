"""What this instance can do, declared for discovery.

Only capabilities with endpoints behind them - advertising one that nothing serves
would make discovery a wish list. See docs/http-api.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

from common.capture import preflight
from common.host import frontend_browser, metrics
from common.i18n import t

from . import capabilities

logger = logging.getLogger("vpinfe.httpapi.core_capabilities")


def _peripherals_available() -> bool | tuple[bool, str]:
    """Available if any peripheral is switched on, not only DOF."""
    try:
        from common.config.paths import get_ini_config
        from common.host.dof_service import _is_enabled as dof_enabled
        from common.host.libdmdutil_service import _is_enabled as dmd_enabled

        config = get_ini_config()
        enabled = [name for name, check in (("DOF", dof_enabled), ("real-DMD", dmd_enabled))
                   if check(config)]
        if not enabled:
            return False, t("error.capabilities.no_peripherals")
        return True
    except Exception:
        logger.exception("Could not tell whether DOF or a real DMD is on")
        return False, t("error.capabilities.peripheral_state_unknown")


def _launch_available() -> bool | tuple[bool, str]:
    """Whether this device can actually start a game.

    Reading play state works without a launcher; starting one does not. Discovery
    has to say so, or an instance advertises a Play button that always fails.
    """
    try:
        from common.games import launchers

        found = launchers.default_launcher()
        if found is None:
            return False, t("error.capabilities.no_launcher")
        configured = str(found.value("bin_path") or "").strip()
        if not configured:
            return False, t("said.no_program", launcher_name=found.display_name)
        if not Path(configured).exists():
            return False, t("said.program_not_there",
                             launcher_name=found.display_name, path=configured)
        return True
    except Exception:
        logger.exception("Could not tell whether the default launcher can play")
        return False, t("error.capabilities.launcher_state_unknown")


def _rom_audit_available() -> bool | tuple[bool, str]:
    """Whether this device can run PinMAME's own ROM audit."""
    try:
        from common.games import launchers
        from common.host import pinmame_catalog

        vpx_bin = launchers.default_value("bin_path")
        # The catalog reports no reason when it is simply available, and the shape the
        # other three predicates share has no room for a None one.
        available, reason = pinmame_catalog.availability(vpx_bin)
        return (available, reason) if reason else available
    except Exception:
        logger.exception("Could not tell whether the ROM audit can run")
        return False, t("error.capabilities.libpinmame_state_unknown")


def _actions_available() -> bool | tuple[bool, str]:
    """Whether anything on this build performs any of them. A build with no performer
    registered can be asked for nothing - which a fleet surface is better off being told
    than discovering."""
    from common.runtime import lifecycle

    if any(lifecycle.performable(*pair) for pair in lifecycle.offered()):
        return True
    return False, t("error.capabilities.nothing_performs_actions")


def declare_core() -> None:
    """Declare the capabilities this build actually serves."""
    capabilities.declare(capabilities.Capability(
        name="library",
        feature=capabilities.install_identity.LIBRARY,
        description="Game inventory, identity, metadata and media",
    ))
    capabilities.declare(capabilities.Capability(
        name="uploads",
        feature=capabilities.install_identity.LIBRARY,
        description="Upload sessions and the asset import pipeline",
    ))
    capabilities.declare(capabilities.Capability(
        name="play",
        feature=capabilities.install_identity.FRONTEND,
        description="Launch lifecycle state for this device",
    ))
    capabilities.declare(capabilities.Capability(
        name="launch",
        feature=capabilities.install_identity.FRONTEND,
        description="Starting a game on this device",
        is_available=_launch_available,
    ))
    capabilities.declare(capabilities.Capability(
        name="metrics",
        description="Live readings from this device",
        is_available=lambda: metrics.measurable(),
    ))
    capabilities.declare(capabilities.Capability(
        name="media_playback",
        feature=capabilities.install_identity.FRONTEND,
        description="Which video and audio formats this device's frontend browser plays",
        is_available=frontend_browser.available,
    ))
    capabilities.declare(capabilities.Capability(
        name="capture",
        feature=capabilities.install_identity.FRONTEND,
        description="Recording this device's own screens into its tables' media",
        is_available=lambda: preflight.available(),
    ))
    capabilities.declare(capabilities.Capability(
        name="launchers",
        feature=capabilities.install_identity.FRONTEND,
        description="The configured ways this device runs a table",
    ))
    capabilities.declare(capabilities.Capability(
        name="players",
        feature=capabilities.install_identity.FRONTEND,
        description="Who plays on this device, and who the next game counts for",
    ))
    capabilities.declare(capabilities.Capability(
        name="peripherals",
        feature=capabilities.install_identity.FRONTEND,
        description="DOF, real-DMD and other attached hardware",
        is_available=_peripherals_available,
    ))
    capabilities.declare(capabilities.Capability(
        name="rom_audit",
        feature=capabilities.install_identity.LIBRARY,
        description="ROM set verification through the VPX install's own PinMAME",
        is_available=_rom_audit_available,
    ))
    capabilities.declare(capabilities.Capability(
        name="events",
        description="Game lifecycle, play state and job progress as they happen",
    ))
    capabilities.declare(capabilities.Capability(
        name="jobs",
        description="Slow work runs in the background and reports progress",
    ))
    capabilities.declare(capabilities.Capability(
        name="devices",
        feature=capabilities.install_identity.DEVICES,
        description="The other VPinFE installs and mobile devices on your network",
    ))
    capabilities.declare(capabilities.Capability(
        name="logs",
        description="What this install has been writing down",
    ))
    capabilities.declare(capabilities.Capability(
        name="actions",
        description="Closing a table, reopening the windows, restarting, rebooting",
        is_available=_actions_available,
    ))
