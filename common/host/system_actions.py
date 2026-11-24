"""Asking the operating system to restart or power off, and re-launching ourselves.

The three platforms want three different commands for the same thing. Nothing here
decides whether to do it - that is `common.runtime.lifecycle`, which confirms first.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from common.config.paths import APP_ROOT
from common.devices import device_registry

RESTART_KEY = "restart_requested"


def system_command_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "_MEIPASS2"):
        env.pop(key, None)
    return env


def request_app_restart() -> None:
    device_registry.get_device_registry().set_self(**{RESTART_KEY: True})


def cancel_app_restart() -> None:
    device_registry.get_device_registry().set_self(**{RESTART_KEY: None})


def restart_if_requested(
        logger: logging.Logger, main_script: Path | None = None,
        sleep_func: Callable[[float], None] | None = None) -> None:
    if not device_registry.get_device_registry().self_get(RESTART_KEY):
        logger.info("No restart requested, exiting.")
        return

    cancel_app_restart()
    logger.info("Restart requested, re-launching...")
    if sleep_func is None:
        import time

        sleep_func = time.sleep
    sleep_func(1)
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, os.path.abspath(main_script or APP_ROOT / "main.py")]
    os.execvp(sys.executable, [*command, *sys.argv[1:]])


def shutdown_system() -> None:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/s", "/t", "1"], shell=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", 'tell app "System Events" to shut down'])
    else:
        subprocess.Popen(["systemctl", "poweroff", "-i"], env=system_command_env())


def reboot_system() -> None:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/r", "/t", "1"], shell=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", 'tell app "System Events" to restart'])
    else:
        subprocess.Popen(["systemctl", "reboot"], env=system_command_env())
