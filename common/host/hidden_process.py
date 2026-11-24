"""Start a program on a desktop nobody sees, so a message box it raises is never shown.

Whatever windows the program opens, the person never sees them. Windows only; elsewhere
`start` answers None.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from typing import Any

logger = logging.getLogger("vpinfe.common.host.hidden_process")

_GENERIC_ALL = 0x10000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_WAIT_TIMEOUT = 0x102
_INFINITE = 0xFFFFFFFF


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class HiddenProcess:
    """The part of `subprocess.Popen` a caller that waits and then ends the program uses."""

    def __init__(self, info: _ProcessInformation, desktop: Any, kernel32: Any,
                 user32: Any) -> None:
        self._process = info.hProcess
        self._desktop = desktop
        self._kernel32 = kernel32
        self._user32 = user32
        self._kernel32.CloseHandle(info.hThread)
        self._closed = False

    def wait(self, timeout: float | None = None) -> int:
        """The exit code. Raises `subprocess.TimeoutExpired` while it is still running."""
        milliseconds = _INFINITE if timeout is None else int(timeout * 1000)
        if self._kernel32.WaitForSingleObject(self._process, milliseconds) == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired([], timeout or 0)
        code = wintypes.DWORD()
        self._kernel32.GetExitCodeProcess(self._process, ctypes.byref(code))
        self._close()
        return int(code.value)

    def terminate(self) -> None:
        if not self._closed:
            self._kernel32.TerminateProcess(self._process, 1)

    kill = terminate

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._kernel32.CloseHandle(self._process)
        self._user32.CloseDesktop(self._desktop)


def start(command: Sequence[str], env: Mapping[str, str]) -> HiddenProcess | None:
    """The program running on a desktop of its own, or None off Windows or where one cannot
    be made, which the caller answers by starting it the plain way."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfo),
        ctypes.POINTER(_ProcessInformation)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    name = f"vpinfe-hidden-{os.getpid()}-{id(command)}"
    desktop = user32.CreateDesktopW(name, None, None, 0, _GENERIC_ALL, None)
    if not desktop:
        logger.debug("No hidden desktop: error %s", ctypes.get_last_error())
        return None
    startup = _StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    startup.lpDesktop = f"winsta0\\{name}"
    info = _ProcessInformation()
    environment = ctypes.create_unicode_buffer(
        "".join(f"{key}={value}\0" for key, value in env.items()) + "\0")
    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
    made = kernel32.CreateProcessW(None, line, None, None, False, _CREATE_UNICODE_ENVIRONMENT,
                                   environment, None, ctypes.byref(startup), ctypes.byref(info))
    if not made:
        error = ctypes.get_last_error()
        user32.CloseDesktop(desktop)
        raise OSError(error, ctypes.FormatError(error), command[0])
    return HiddenProcess(info, desktop, kernel32, user32)
