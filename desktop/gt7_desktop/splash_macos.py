"""macOS: the splash as a borderless AppKit window (gt7_desktop.splash).

Everything AppKit runs on the main thread, where the menu-bar item's run
loop is: the splash's own thread only queues calls with AppHelper.callAfter.
The picture carries its rounded corners on transparency, so the window is
clear and the system draws the shadow.
"""

from __future__ import annotations

from typing import Any

from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSTextAlignmentRight,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from PyObjCTools import AppHelper

from gt7_desktop import paths
from gt7_desktop.splash import HEIGHT, WIDTH, Frame

STATUS_COLOUR = (0.60, 0.62, 0.72)
MARGIN = 28


def _label(x: float, width: float, align_right: bool = False) -> Any:
    label = NSTextField.labelWithString_("")
    label.setFrame_(NSMakeRect(x, 16, width, 18))
    label.setFont_(NSFont.systemFontOfSize_(12))
    label.setTextColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(*STATUS_COLOUR, 1.0))
    if align_right:
        label.setAlignment_(NSTextAlignmentRight)
    return label


class MacSplashView:
    def __init__(self) -> None:
        self.window: Any = None
        self.image: Any = None
        self.status: Any = None
        self._images: dict[str, Any] = {}

    def _picture(self, name: str) -> Any:
        if name not in self._images:
            path = paths.icon(f"splash/{name}")
            self._images[name] = NSImage.alloc().initWithContentsOfFile_(str(path))
        return self._images[name]

    def show(self, frame: Frame, version: str) -> None:
        AppHelper.callAfter(self._show, frame, version)

    def render(self, frame: Frame) -> None:
        AppHelper.callAfter(self._render, frame)

    def close(self) -> None:
        AppHelper.callAfter(self._close)

    def _show(self, frame: Frame, version: str) -> None:
        rect = NSMakeRect(0, 0, WIDTH, HEIGHT)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        window.setReleasedWhenClosed_(False)
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setHasShadow_(True)
        window.setMovableByWindowBackground_(True)
        image = NSImageView.alloc().initWithFrame_(rect)
        image.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        content = window.contentView()
        content.addSubview_(image)
        self.status = _label(MARGIN, WIDTH / 2)
        content.addSubview_(self.status)
        ver = _label(WIDTH / 2, WIDTH / 2 - MARGIN, align_right=True)
        ver.setStringValue_(version)
        content.addSubview_(ver)
        self.window, self.image = window, image
        self._render(frame)
        window.center()
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _render(self, frame: Frame) -> None:
        if self.window is None:
            return
        self.image.setImage_(self._picture(frame.image))
        self.status.setStringValue_(frame.status)

    def _close(self) -> None:
        if self.window is not None:
            self.window.orderOut_(None)
            self.window.close()
            self.window = None
