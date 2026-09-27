// GridRenderer draws a whole layout: a LayoutConfig's cells placed on a CSS
// grid, each widget zoomed to its span. Every cell here is one of the app's
// own layouts, with the same reshaping the views apply (DashView forces
// `size: null` and a dark page; OverlayView renders the layout as saved).
//
// The layouts are 1280–1920 px wide by design, so they are shown through a CSS
// transform: the grid still measures its true width (the ResizeObserver reads
// layout px, which a transform does not touch), so the cells and zoom factors
// are exactly what a 1920-wide browser source gets.

import {
  DASH_PRESETS,
  DEFAULT_LAYOUT,
  GridRenderer,
  demoFrame,
} from "gt7-datalogger-frontend";
import type { ReactNode } from "react";
import { FUEL_LAPS } from "../fixtures/app";
import { Surface } from "../preview-shell";

// What DashView hands the renderer: the preset, forced to fill the screen on a
// dark page whatever the saved layout said.
const asDash = (key: string) => ({ ...DASH_PRESETS[key].layout, size: null, page: "dark" as const });

// Lap 12 of a 26-lap race at Mount Panorama in the 911 GT3 R, flat out in
// sixth. The car id matches the fixture laps on purpose: the fuel, strategy
// and alert widgets all route through projectStrategy, which drops laps from
// another car — paired with the demo frame's own car they would show "–".
// 30 L against ~11.8 a lap is about two and a half laps left, so the alert
// engine fires its fuel warning and the banner row has something to say.
const frame = {
  ...demoFrame(8_000),
  car_id: 3600,
  car_name: "911 GT3 R (992) '22",
  track_name: "Mount Panorama Motor Racing Circuit",
  fuel_capacity: 100,
  fuel_level: 30,
  current_lap: 12,
  total_laps: 26,
};

/** A layout at its true canvas size, scaled down to fit the card.
 *
 *  `offX`/`vw` window onto part of that canvas: the 1920-wide OBS strip puts
 *  its widgets in columns 3–13, so the ~370 px of deliberately empty gutter at
 *  each end is scrolled off rather than paid for in scale. */
function Scaled({
  w,
  h,
  scale,
  offX = 0,
  vw,
  children,
}: {
  w: number;
  h: number;
  scale: number;
  offX?: number;
  vw?: number;
  children: ReactNode;
}) {
  return (
    <div style={{ width: (vw ?? w) * scale, height: h * scale, overflow: "hidden" }}>
      <div
        style={{
          width: w,
          height: h,
          transform: `scale(${scale}) translateX(${-offX}px)`,
          transformOrigin: "0 0",
        }}
      >
        {children}
      </div>
    </div>
  );
}

// Where the strip's occupied columns begin and how wide they run, from the
// layout's own geometry: padX + 3 columns in, 10 columns across.
const CELL_W = (1920 - 2 * 16 - 8 * 15) / 16;
const STRIP_OFF = 16 + 3 * (CELL_W + 8);
const STRIP_W = 10 * CELL_W + 9 * 8;

export function RaceEngineerDash() {
  // The built-in race-engineer screen: alerts across the top, strategy and
  // timing in the middle, car health and race context below.
  return (
    <Surface>
      <Scaled w={1280} h={720} scale={0.66}>
        <GridRenderer layout={asDash("race-engineer")} frame={frame} laps={FUEL_LAPS} />
      </Scaled>
    </Surface>
  );
}

export function EnduranceDash() {
  // The other preset: fuel is the hero and the engine gets a wide readout.
  return (
    <Surface>
      <Scaled w={1280} h={720} scale={0.66}>
        <GridRenderer layout={asDash("endurance")} frame={frame} laps={FUEL_LAPS} />
      </Scaled>
    </Surface>
  );
}

export function ObsStrip() {
  // The app's DEFAULT_LAYOUT as saved: a 1920×260 browser source, 16 columns by 2 rows,
  // cards at 70% opacity over a transparent page.
  return (
    <Surface>
      <Scaled w={1920} h={260} scale={0.66} offX={STRIP_OFF} vw={STRIP_W}>
        <GridRenderer layout={DEFAULT_LAYOUT} frame={frame} laps={FUEL_LAPS} />
      </Scaled>
    </Surface>
  );
}

export function BareStrip() {
  // `bg: 0` is the renderer's other mode: no cards at all, every widget
  // frameless and floating straight on the stream.
  return (
    <Surface>
      <Scaled w={1920} h={260} scale={0.66} offX={STRIP_OFF} vw={STRIP_W}>
        <GridRenderer layout={{ ...DEFAULT_LAYOUT, bg: 0 }} frame={frame} laps={FUEL_LAPS} />
      </Scaled>
    </Surface>
  );
}
