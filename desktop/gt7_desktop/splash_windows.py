"""Windows: the splash as a layered popup window (gt7_desktop.splash).

A WS_POPUP window with WS_EX_LAYERED, so the picture's rounded corners stay
transparent, and WS_EX_TOOLWINDOW, so it never gets a taskbar button. Each
frame is the PNG with the status line and the version drawn on by Pillow
(which the Windows build already carries for the tray icon), premultiplied,
and handed to UpdateLayeredWindow. The window lives on a thread of its own
that is per-monitor DPI aware, so the splash is sharp at any scale without
changing how the rest of the app is scaled.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from gt7_desktop import endsession, paths
from gt7_desktop.splash import HEIGHT, WIDTH, Frame

# Imported only on Windows (splash.make_view); this also tells mypy to check
# the rest of the file for Windows only.
assert sys.platform == "win32"

# The window class and prototypes the session-end window already set up.
WNDCLASSW, WNDPROC = endsession.WNDCLASSW, endsession.WNDPROC
user32, kernel32 = endsession.user32, endsession.kernel32

log = logging.getLogger(__name__)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWNORMAL = 1
ULW_ALPHA = 0x2
AC_SRC_OVER, AC_SRC_ALPHA = 0x0, 0x1
SPI_GETWORKAREA = 0x0030
WM_DESTROY = 0x0002
WM_APP_RENDER = 0x8000 + 2
WM_APP_CLOSE = 0x8000 + 3
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

STATUS_COLOUR = (153, 158, 184, 255)
MARGIN = 28

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def _prototype(fn: Any, restype: Any, *argtypes: Any) -> None:
    fn.restype = restype
    fn.argtypes = list(argtypes)


_prototype(user32.GetDC, wintypes.HDC, wintypes.HWND)
_prototype(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_prototype(user32.UpdateLayeredWindow, wintypes.BOOL,
           wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
           ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
           wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
_prototype(user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_prototype(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_prototype(user32.SystemParametersInfoW, wintypes.BOOL,
           wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT)
_prototype(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_prototype(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_prototype(gdi32.CreateDIBSection, wintypes.HBITMAP,
           wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
_prototype(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_prototype(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)


def _dpi_aware_thread() -> float:
    """Make this thread per-monitor DPI aware; the primary display's scale."""
    try:
        fn = user32.SetThreadDpiAwarenessContext
        fn.restype = ctypes.c_void_p
        fn.argtypes = [ctypes.c_void_p]
        fn(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        get = user32.GetDpiForSystem
        get.restype = wintypes.UINT
        return float(get()) / 96.0
    except (AttributeError, OSError):  # before Windows 10 1607: unscaled
        return 1.0


def _font(size: int) -> Any:
    try:
        return ImageFont.truetype("segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


class WindowsSplashView:
    def __init__(self) -> None:
        self.hwnd: int | None = None
        self.version = ""
        self.scale = 1.0
        self._frame: Frame | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._class = f"GT7DataloggerSplash{id(self):x}"
        self._pictures: dict[str, Image.Image] = {}
        self._wndproc = WNDPROC(self._proc)  # kept: ctypes must not free it

    # --- the splash's calls, from any thread ---------------------------------

    def show(self, frame: Frame, version: str) -> None:
        self._frame, self.version = frame, version
        self._thread = threading.Thread(target=self._run, name="gt7-splash-window",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self.hwnd:
            raise OSError("could not create the splash window")

    def render(self, frame: Frame) -> None:
        with self._lock:
            self._frame = frame
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_RENDER, 0, 0)

    def close(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_CLOSE, 0, 0)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2)

    # --- the window's thread ------------------------------------------------

    def _run(self) -> None:
        self.scale = _dpi_aware_thread()
        w, h = round(WIDTH * self.scale), round(HEIGHT * self.scale)
        area = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        x = area.left + (area.right - area.left - w) // 2
        y = area.top + (area.bottom - area.top - h) // 2
        instance = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=instance,
                        lpszClassName=self._class)
        if not user32.RegisterClassW(ctypes.byref(cls)):
            log.warning("RegisterClassW failed: %s", ctypes.get_last_error())
            self._ready.set()
            return
        hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW, self._class,
                                      "GT7 Datalogger", WS_POPUP, x, y, w, h,
                                      None, None, instance, None)
        if hwnd:
            self.hwnd = hwnd
            self._push()
            user32.ShowWindow(hwnd, SW_SHOWNORMAL)
            user32.SetForegroundWindow(hwnd)
        else:
            log.warning("CreateWindowExW failed: %s", ctypes.get_last_error())
        self._ready.set()
        if hwnd:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None
        user32.UnregisterClassW(self._class, instance)

    def _proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_APP_RENDER:
                self._push()
                return 0
            if msg == WM_APP_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 - an exception must not escape into Windows
            log.exception("splash window: message 0x%x failed", msg)
            return 0
        return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))

    def compose(self, frame: Frame) -> Image.Image:
        """The frame's picture at this display's size, with its text."""
        w, h = round(WIDTH * self.scale), round(HEIGHT * self.scale)
        if frame.image not in self._pictures:
            picture = Image.open(paths.icon(f"splash/{frame.image}")).convert("RGBA")
            self._pictures[frame.image] = picture.resize((w, h), Image.LANCZOS)
        img = self._pictures[frame.image].copy()
        draw = ImageDraw.Draw(img)
        font = _font(round(12 * self.scale))
        baseline = h - round(20 * self.scale)
        margin = round(MARGIN * self.scale)
        draw.text((margin, baseline), frame.status, font=font, fill=STATUS_COLOUR, anchor="ls")
        draw.text((w - margin, baseline), self.version, font=font, fill=STATUS_COLOUR,
                  anchor="rs")
        return img

    def _push(self) -> None:
        with self._lock:
            frame = self._frame
        if frame is None or not self.hwnd:
            return
        img = self.compose(frame)
        w, h = img.size
        data = img.convert("RGBa").tobytes("raw", "BGRa")  # premultiplied, as Windows wants
        screen = user32.GetDC(None)
        memory = gdi32.CreateCompatibleDC(screen)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth, info.bmiHeader.biHeight = w, -h  # top-down rows
        info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(memory, ctypes.byref(info), 0, ctypes.byref(bits),
                                        None, 0)
        try:
            if not bitmap or not bits.value:
                log.warning("CreateDIBSection failed: %s", ctypes.get_last_error())
                return
            ctypes.memmove(bits.value, data, len(data))
            old = gdi32.SelectObject(memory, bitmap)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            size, origin = wintypes.SIZE(w, h), wintypes.POINT(0, 0)
            if not user32.UpdateLayeredWindow(self.hwnd, screen, None, ctypes.byref(size),
                                              memory, ctypes.byref(origin), 0,
                                              ctypes.byref(blend), ULW_ALPHA):
                log.warning("UpdateLayeredWindow failed: %s", ctypes.get_last_error())
            gdi32.SelectObject(memory, old)
        finally:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory)
            user32.ReleaseDC(None, screen)

