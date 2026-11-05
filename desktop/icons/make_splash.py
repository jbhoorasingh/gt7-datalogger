#!/usr/bin/env python3
"""Draw the desktop app's splash frames (gt7_desktop.splash).

    python desktop/icons/make_splash.py     (needs Pillow and macOS's fonts;
                                             run it by hand)

The outputs are committed, so builds need no Pillow, and macOS and Windows
show the same pictures:

    desktop/icons/splash/lights-0.png … lights-5.png   the start gantry with
                                                       that many red lights on
    desktop/icons/splash/go.png                        all ten lamps green: go

Each is 1280 x 720, the splash's 640 x 360 at 2x, with rounded corners on
transparency. The bottom strip is left clear: the app writes its status
line and version there at run time.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "splash"
MARK = Image.open(HERE / "source" / "mark.png").convert("RGBA")

S = 2  # drawn at 2x
W, H = 640 * S, 360 * S
RADIUS = 16 * S
NAVY_TOP, NAVY_BOTTOM = (30, 32, 52), (17, 18, 30)
LAVENDER = (170, 140, 232)
INK_FAINT = (118, 122, 150)
WORDMARK = "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf"
SMALL = "/System/Library/Fonts/SFNS.ttf"

LIGHTS = 5
GANTRY_W, GANTRY_H = 420 * S, 104 * S
GANTRY_Y = 120 * S  # top of the beam
LAMP = 30 * S


def mix(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def vertical_gradient(size: tuple[int, int], top: tuple[int, int, int],
                      bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    column = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        column.putpixel((0, y), mix(top, bottom, t))
    return column.resize((w, h))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def background() -> Image.Image:
    base = vertical_gradient((W, H), NAVY_TOP, NAVY_BOTTOM).convert("RGBA")
    # The brand mark, large and faint, as the circuit behind everything.
    mark = MARK.resize((int(MARK.width * (560 * S) / MARK.width),
                        int(MARK.height * (560 * S) / MARK.width)), Image.LANCZOS)
    faint = mark.copy()
    faint.putalpha(faint.getchannel("A").point(lambda a: a * 0.07))
    base.alpha_composite(faint, (W - mark.width + 90 * S, H - mark.height + 150 * S))
    # A lavender racing line low across the panel, fading at both ends.
    line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(line)
    points = [(x, 262 * S + 18 * S * math.sin(x / W * math.pi * 1.6 + 0.4)) for x in range(0, W, 4)]
    d.line(points, fill=(*LAVENDER, 70), width=3 * S, joint="curve")
    fade = Image.linear_gradient("L").rotate(90, expand=True).resize((W, 1))
    edge = fade.point(lambda v: min(255, int(255 * math.sin(v / 255 * math.pi) * 1.4)))
    line.putalpha(Image.composite(line.getchannel("A"), Image.new("L", (W, H), 0),
                                  edge.resize((W, H))))
    base.alpha_composite(line)
    # Wordmark, top left: the mark, then the name.
    small = MARK.resize((int(MARK.width * 30 * S / MARK.height), 30 * S), Image.LANCZOS)
    base.alpha_composite(small, (28 * S, 26 * S))
    d = ImageDraw.Draw(base)
    font = ImageFont.truetype(WORDMARK, 19 * S)
    x = 28 * S + small.width + 12 * S
    for ch in "GT7 DATALOGGER":  # letter-spaced by hand
        d.text((x, 31 * S), ch, font=font, fill=(232, 230, 245))
        x += font.getlength(ch) + 2.2 * S
    # The not-affiliated line, bottom right above the status strip.
    note = ImageFont.truetype(SMALL, 9 * S)
    text = "Unofficial. Not affiliated with Sony Interactive Entertainment or Polyphony Digital."
    d.text((W - 28 * S, H - 52 * S), text, font=note, fill=(*INK_FAINT, 255), anchor="rs")
    return base


def glow(size: tuple[int, int], centre: tuple[int, int], radius: int,
         colour: tuple[int, int, int], strength: int) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = centre
    ImageDraw.Draw(layer).ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                                  fill=(*colour, strength))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.6))


# Each lit colour: the lamp's edge, its hot centre, and its glow.
RED = ((140, 14, 22), (255, 150, 140), (255, 48, 40))
GREEN = ((14, 120, 52), (170, 255, 190), (40, 220, 100))


def lamp(lit: tuple[tuple[int, int, int], ...] | None) -> Image.Image:
    size = LAMP
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    steps = 24
    for i in range(steps):  # a radial gradient, lit from the upper left
        t = i / steps
        r = size / 2 * (1 - t)
        ox, oy = size * 0.4 * t + size / 2 * (1 - t) - r, size * 0.35 * t + size / 2 * (1 - t) - r
        if lit is not None:
            colour = mix(lit[0], lit[1], t)
        else:
            colour = mix((20, 22, 28), (44, 48, 58), t)
        d.ellipse((ox, oy, ox + 2 * r, oy + 2 * r), fill=(*colour, 255))
    return img


def gantry(lit: int, colour: tuple[tuple[int, int, int], ...] = RED) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0 = (W - GANTRY_W) // 2
    # Shadow, then the beam.
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0, GANTRY_Y + 14 * S, x0 + GANTRY_W, GANTRY_Y + GANTRY_H + 18 * S), 12 * S,
        fill=(0, 0, 0, 150))
    layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14 * S)))
    beam = vertical_gradient((GANTRY_W, GANTRY_H), (34, 37, 48), (16, 18, 24)).convert("RGBA")
    beam.putalpha(rounded_mask((GANTRY_W, GANTRY_H), 12 * S))
    layer.alpha_composite(beam, (x0, GANTRY_Y))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0, GANTRY_Y, x0 + GANTRY_W - 1, GANTRY_Y + GANTRY_H - 1), 12 * S,
                        outline=(52, 56, 70), width=S)
    pod_w, pod_h = LAMP + 16 * S, 2 * LAMP + 26 * S
    gap = (GANTRY_W - 2 * 20 * S - LIGHTS * pod_w) / (LIGHTS - 1)
    for i in range(LIGHTS):
        px = round(x0 + 20 * S + i * (pod_w + gap))
        py = GANTRY_Y + (GANTRY_H - pod_h) // 2
        d.rounded_rectangle((px, py, px + pod_w, py + pod_h), 9 * S, fill=(7, 8, 10),
                            outline=(30, 33, 41), width=S)
        on = i < lit
        for j in range(2):
            lx, ly = px + 8 * S, py + 9 * S + j * (LAMP + 8 * S)
            if on:
                layer.alpha_composite(glow((W, H), (lx + LAMP // 2, ly + LAMP // 2),
                                           LAMP, colour[2], 150))
            layer.alpha_composite(lamp(colour if on else None), (lx, ly))
    return layer


def finish(img: Image.Image) -> Image.Image:
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (W, H), 0),
                                 rounded_mask((W, H), RADIUS)))
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    base = background()
    for lit in range(LIGHTS + 1):
        frame = base.copy()
        frame.alpha_composite(gantry(lit))
        finish(frame).save(OUT / f"lights-{lit}.png", optimize=True)
    frame = base.copy()
    frame.alpha_composite(gantry(LIGHTS, GREEN))
    finish(frame).save(OUT / "go.png", optimize=True)
    for p in sorted(OUT.glob("*.png")):
        print(p.relative_to(HERE.parents[1]), f"{p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
