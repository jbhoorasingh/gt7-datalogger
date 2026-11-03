#!/usr/bin/env python3
"""Make every icon file from the two brand masters in desktop/icons/source/.

    python desktop/icons/make_icons.py      (needs Pillow and macOS's iconutil;
                                             run it by hand)

The outputs are committed, so builds need no Pillow:

    desktop/icons/gt7.icns          macOS app (Apple's grid: 824 of 1024, rounded)
    desktop/icons/gt7.ico           Windows exe and shortcuts
    desktop/icons/gt7.png           Linux AppImage, 256 px
    desktop/icons/tray.png          Windows notification area, 64 px
    desktop/icons/menubar.png       macOS menu bar template, 18 pt tall at @2x
    frontend/public/favicon.ico, favicon-32.png, apple-touch-icon.png,
    icon-192.png, icon-512.png      the web app's tab and home-screen icons

mark-on-navy.png is the square mark on its opaque navy; mark.png is the same
mark alone on transparency.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PUBLIC = ROOT / "frontend" / "public"
SQUARE = Image.open(HERE / "source" / "mark-on-navy.png").convert("RGBA")
MARK = Image.open(HERE / "source" / "mark.png").convert("RGBA")
HI = 1024  # draw large, then shrink each size from it


def rounded(size: int, margin: float = 0.0, radius: float = 0.2237) -> Image.Image:
    """The navy square with rounded corners, inset by `margin` on each side.
    0.2237 is the corner radius of Apple's icon shape, as a share of its side."""
    canvas = Image.new("RGBA", (HI, HI), (0, 0, 0, 0))
    side = round(HI * (1 - 2 * margin))
    at = (HI - side) // 2
    body = SQUARE.resize((side, side), Image.LANCZOS)
    mask = Image.new("L", (side * 4, side * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, side * 4 - 1, side * 4 - 1), radius=round(side * 4 * radius), fill=255)
    body.putalpha(mask.resize((side, side), Image.LANCZOS))
    canvas.alpha_composite(body, (at, at))
    return canvas.resize((size, size), Image.LANCZOS) if size != HI else canvas


def square(size: int) -> Image.Image:
    return SQUARE.resize((size, size), Image.LANCZOS)


def menubar(height: int = 36) -> Image.Image:
    """The bare mark in black on transparency, cropped to it: macOS tints a
    template image to suit the menu bar, so only its alpha matters."""
    alpha = MARK.getchannel("A")
    box = alpha.getbbox()
    alpha = alpha.crop(box)
    width = round(alpha.width * height / alpha.height)
    alpha = alpha.resize((width, height), Image.LANCZOS)
    out = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    out.putalpha(alpha)
    return out


def main() -> None:
    # iconutil, not Pillow: Pillow's .icns leaves out the 1x 16 and 32 px
    # entries Finder's list view uses.
    mac = rounded(HI, margin=100 / 1024)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "gt7.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            name = f"icon_{points}x{points}"
            mac.resize((points, points), Image.LANCZOS).save(iconset / f"{name}.png")
            mac.resize((points * 2, points * 2), Image.LANCZOS).save(iconset / f"{name}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "gt7.icns")],
                       check=True)
    win = rounded(256, radius=0.18)
    win.save(HERE / "gt7.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                      (64, 64), (128, 128), (256, 256)])
    win.save(HERE / "gt7.png", optimize=True)
    rounded(64, radius=0.18).save(HERE / "tray.png", optimize=True)
    menubar().save(HERE / "menubar.png", optimize=True)

    PUBLIC.mkdir(exist_ok=True)
    square(48).save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    square(32).save(PUBLIC / "favicon-32.png", optimize=True)
    # iOS rounds the corners itself and wants no transparency.
    square(180).convert("RGB").save(PUBLIC / "apple-touch-icon.png", optimize=True)
    square(192).save(PUBLIC / "icon-192.png", optimize=True)
    square(512).save(PUBLIC / "icon-512.png", optimize=True)


if __name__ == "__main__":
    main()
