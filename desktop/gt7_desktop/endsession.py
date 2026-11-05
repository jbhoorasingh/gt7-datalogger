"""Windows: close the session cleanly when Windows signs out or shuts down (#194).

Before a session ends, Windows asks every top-level window whether it may
(WM_QUERYENDSESSION), then tells it (WM_ENDSESSION), and may end the process
as soon as that handler returns. The server child has no window, so nothing
asked it: at sign-out it went down with the supervisor's job object in the
middle of a lap. The supervisor keeps a hidden window for this, on a thread
of its own, and stops the server inside the WM_ENDSESSION handler, with a
shutdown-block reason Windows can show if that takes a few seconds. Sign-out
is never refused.

WM_CLOSE, which `taskkill` without /F and Restart Manager send, quits the
app as `--quit` does.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

# Imported only on Windows (gt7_desktop.main); this also tells mypy to check
# the rest of the file for Windows only.
assert sys.platform == "win32"

log = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_APP_STOP = 0x8000 + 1  # WM_APP + 1: our own request to take the window down
ENDSESSION_CLOSEAPP = 0x00000001
ENDSESSION_LOGOFF = 0x80000000

REASON = "Saving the GT7 session"

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Own instances, so these prototypes never change ctypes.windll's for the tray.
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _prototype(fn: Any, restype: Any, *argtypes: Any) -> None:
    fn.restype = restype
    fn.argtypes = list(argtypes)


_prototype(user32.DefWindowProcW, LRESULT,
           wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_prototype(user32.RegisterClassW, wintypes.ATOM, ctypes.POINTER(WNDCLASSW))
_prototype(user32.UnregisterClassW, wintypes.BOOL, wintypes.LPCWSTR, wintypes.HINSTANCE)
_prototype(user32.CreateWindowExW, wintypes.HWND,
           wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
_prototype(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_prototype(user32.GetMessageW, wintypes.BOOL,
           ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_prototype(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
_prototype(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
_prototype(user32.PostMessageW, wintypes.BOOL,
           wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_prototype(user32.SendMessageW, LRESULT,
           wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_prototype(user32.PostQuitMessage, None, ctypes.c_int)
_prototype(user32.ShutdownBlockReasonCreate, wintypes.BOOL, wintypes.HWND, wintypes.LPCWSTR)
_prototype(user32.ShutdownBlockReasonDestroy, wintypes.BOOL, wintypes.HWND)
_prototype(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)


class SessionWatcher:
    """A hidden top-level window that hears the end of the Windows session.

    `on_end` runs inside WM_ENDSESSION and must return only once the server
    has stopped; `on_close` runs for WM_CLOSE and may return at once.
    """

    def __init__(self, on_end: Callable[[], None], on_close: Callable[[], None]) -> None:
        self.on_end = on_end
        self.on_close = on_close
        self.hwnd: int | None = None
        self._class = f"GT7DataloggerSession{id(self):x}"
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        # Kept for the window's life: ctypes must not free the callback.
        self._wndproc = WNDPROC(self._proc)

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="gt7-session-end", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout) or not self.hwnd:
            raise OSError("could not create the session-end window")

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_STOP, 0, 0)
        if self._thread is not None:
            self._thread.join(5)

    def _run(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=instance,
                        lpszClassName=self._class)
        if not user32.RegisterClassW(ctypes.byref(cls)):
            log.warning("RegisterClassW failed: %s", ctypes.get_last_error())
            self._ready.set()
            return
        # Style 0 and no parent: an invisible top-level window, which is what
        # Windows sends the end-of-session messages to (a message-only window
        # would never hear them).
        hwnd = user32.CreateWindowExW(0, self._class, self._class, 0, 0, 0, 0, 0,
                                      None, None, instance, None)
        self.hwnd = hwnd or None
        self._ready.set()
        if not hwnd:
            log.warning("CreateWindowExW failed: %s", ctypes.get_last_error())
        else:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None
        user32.UnregisterClassW(self._class, instance)

    def _proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_QUERYENDSESSION:
                return 1  # the session may end; WM_ENDSESSION does the work
            if msg == WM_ENDSESSION:
                if wparam:
                    log.info("Windows is ending the session (0x%x); stopping the server",
                             lparam & 0xFFFFFFFF)
                    user32.ShutdownBlockReasonCreate(hwnd, REASON)
                    try:
                        self.on_end()
                    finally:
                        user32.ShutdownBlockReasonDestroy(hwnd)
                return 0
            if msg == WM_CLOSE:
                log.info("WM_CLOSE received; quitting")
                self.on_close()
                return 0
            if msg == WM_APP_STOP:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 - an exception must not escape into Windows
            log.exception("session-end window: message 0x%x failed", msg)
            return 0
        return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))
