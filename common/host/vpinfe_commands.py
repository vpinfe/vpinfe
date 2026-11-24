"""The commands that run around VPinFE itself.

The other pair in the set brackets a table; this one brackets the whole run. A launcher
has no opinion about VPinFE starting, so there are three slots and not four, and these
two are install settings that happen to share a mechanism with the table ones rather
than one feature with a scope on it.

There is no game, no launcher and no entry here, which is the first real test of tokens
being declared per context: a line written for a table is refused by name if it is
pasted into one of these.
"""

from __future__ import annotations

import logging

from common import tokens
from common.config.config_access import cfg_get, cfg_int
from common.config.config_store import ConfigStore
from common.host import bundled_libraries, commands, table_commands

logger = logging.getLogger("vpinfe.common.host.vpinfe_commands")


def on_start(ini_config: ConfigStore) -> None:
    """Anything a previous run left half done, and then this run's opening commands.

    The unfinished half goes first because it is putting the machine back to where the
    person expects to find it, and every command after it is written expecting that.
    """
    try:
        table_commands.finish_unfinished()
    except Exception:  # noqa: BLE001 - a stale note must never stop VPinFE starting
        logger.exception("Could not finish what a previous run left")
    _run(ini_config, "on_vpinfe_start", "VPinFE starts")


def on_exit(ini_config: ConfigStore) -> None:
    """This run's closing commands. Best effort: there is nothing left to stop."""
    _run(ini_config, "on_vpinfe_exit", "VPinFE exits")


def _run(ini_config: ConfigStore, key: str, when: str) -> None:
    text = cfg_get(ini_config, "commands", key, "")
    if not str(text or "").strip():
        return
    outcome = commands.run(
        text, tokens.filled(tokens.VPINFE, {}), context=tokens.VPINFE,
        timeout=cfg_int(ini_config, "commands", "timeout",
                        commands.DEFAULT_TIMEOUT),
        on_failure=commands.BEST_EFFORT, env=bundled_libraries.for_child())
    for failed in outcome.failed:
        logger.warning("A command set to run when %s did not work: %s", when, failed.said)
