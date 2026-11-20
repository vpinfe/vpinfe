"""Windows: who has the keyboard, and giving it to a window.

Win32 through ctypes, so nothing is installed. Only imported on Windows.
"""

from __future__ import annotations

import sys

from common.host.front import Window

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _GW_OWNER = 4
    _SW_RESTORE = 9
    _SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd",
                                "Shell_SecondaryTrayWnd"})

    # Win32's own names, kept verbatim so a reader can look them up.
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class WindowsBackend:
        name = "Windows"
        # The foreground can name a window whose own input never heard it gained the
        # focus, so the table's window being the holder does not mean it is running.
        holder_can_be_wrong = True

        def __init__(self) -> None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetWindow.restype = wintypes.HWND
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetShellWindow.restype = wintypes.HWND
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.BringWindowToTop.argtypes = [wintypes.HWND]
            user32.SetActiveWindow.argtypes = [wintypes.HWND]
            user32.SetFocus.argtypes = [wintypes.HWND]
            user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.AttachThreadInput.argtypes = [
                wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            self._user32 = user32
            self._kernel32 = kernel32

        def _window(self, hwnd: int) -> Window:
            user32 = self._user32
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return Window(handle=int(hwnd), pid=int(pid.value), title=buffer.value)

        def windows(self) -> list[Window]:
            """Visible windows with no owner: the ones that are on the desktop in their
            own right. Invisible helper windows (message pumps, IME) share a program's
            id and are not them."""
            user32 = self._user32
            found: list[Window] = []

            def on_window(hwnd: int, _lparam: int) -> bool:
                if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, _GW_OWNER):
                    found.append(self._window(hwnd))
                return True

            user32.EnumWindows(_ENUM_PROC(on_window), 0)
            return found

        def _is_the_shell(self, hwnd: int) -> bool:
            """The desktop and the taskbar: the keyboard rests there when a window closes,
            and no program has it."""
            user32 = self._user32
            if hwnd == user32.GetShellWindow():
                return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            return name.value in _SHELL_CLASSES

        def holder(self) -> Window | None:
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd or self._is_the_shell(hwnd):
                return None
            return self._window(hwnd)

        def hand_over(self, window: Window) -> str:
            """Asks for the foreground and for the focus inside the window, from inside the
            input of the window that has the keyboard and of the one being given it. Sends
            no key press, never touches the system's foreground-lock setting, and never
            makes the window stay on top."""
            user32 = self._user32
            hwnd = int(window.handle)
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, _SW_RESTORE)
            foreground = user32.GetForegroundWindow()
            ours = self._kernel32.GetCurrentThreadId()
            threads = {user32.GetWindowThreadProcessId(foreground, None) if foreground else 0,
                       user32.GetWindowThreadProcessId(hwnd, None)}
            attached = [one for one in threads
                        if one and one != ours and user32.AttachThreadInput(ours, one, True)]
            try:
                user32.AllowSetForegroundWindow(window.pid)
                ctypes.set_last_error(0)
                taken = user32.SetForegroundWindow(hwnd)
                code = ctypes.get_last_error()
                user32.BringWindowToTop(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetFocus(hwnd)
            finally:
                for one in attached:
                    user32.AttachThreadInput(ours, one, False)
            if taken:
                return ""
            return ctypes.FormatError(code).strip() if code else "the system did not say why"
