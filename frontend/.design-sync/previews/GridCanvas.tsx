// GridCanvas is GridRenderer's editable twin, the surface inside the layout
// builder: the layout at its true canvas size, CSS-scaled to whatever width it
// is given, with cell outlines, a selection ring, a resize handle on every
// widget and a caption saying what percentage of actual size you are looking
// at. It sizes itself from its container, so every cell here gives it one.
//
// Dragging is the one thing a still cannot show — the snapped green/red ghost
// only exists between pointerdown and pointerup. Everything else the canvas
// draws is here.

import {
  DASH_PRESETS,
  DEFAULT_LAYOUT,
  GridCanvas,
  demoFrame,
  type LayoutConfig,
} from "gt7-datalogger-frontend";
import { useState } from "react";
import { FUEL_LAPS } from "../fixtures/app";
import { Panel, Surface } from "../preview-shell";

// Lap 12 of a 26-lap race at Mount Panorama in the 911 GT3 R, flat out in
// sixth — the car id matches the fixture laps, which is what projectStrategy
// needs before the fuel, strategy and alert widgets will show anything. 30 L
// against ~11.8 a lap trips the fuel warning, so the (frameless) alerts cell
// has content rather than just its builder label.
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

/** The builder's own wiring: the canvas owns nothing, the page holds the
 *  layout and the selection and hands both back down. */
function Canvas({
  initial,
  select = null,
  live = true,
  width = 800,
}: {
  initial: LayoutConfig;
  select?: string | null;
  live?: boolean;
  width?: number;
}) {
  const [layout, setLayout] = useState(initial);
  const [selected, setSelected] = useState<string | null>(select);
  return (
    <div style={{ width }}>
      <GridCanvas
        layout={layout}
        frame={live ? frame : null}
        laps={FUEL_LAPS}
        selected={selected}
        onSelect={setSelected}
        onCellsChange={(cells) => setLayout({ ...layout, cells })}
      />
    </div>
  );
}

export function OverlayCanvas() {
  // The default OBS strip on the checkerboard that stands for a transparent
  // page, with the lap-times widget picked: accent ring, resize handle at its
  // corner, every other cell outlined on the grid.
  return (
    <Surface>
      <Panel title="Canvas · OBS strip">
        <Canvas initial={DEFAULT_LAYOUT} select="c5" />
      </Panel>
    </Surface>
  );
}

export function DashCanvas() {
  // A fill-the-screen dash layout: no fixed size, so the canvas falls back to
  // its 1280x720 preview frame and says so under the grid.
  return (
    <Surface>
      <Panel title="Canvas · driver dash">
        <Canvas initial={DASH_PRESETS["race-engineer"].layout} select="re-delta" />
      </Panel>
    </Surface>
  );
}

export function ChromaKey() {
  // `page: "green"` is the chroma-key preview: the builder paints the real
  // #00ff00 the OBS source will key out, and forces the cards opaque.
  return (
    <Surface>
      <Panel title="Canvas · chroma key">
        <Canvas initial={{ ...DEFAULT_LAYOUT, page: "green" }} />
      </Panel>
    </Surface>
  );
}

export function WaitingForTelemetry() {
  // With no frame the canvas draws each cell as its widget's name instead of a
  // readout — the state the builder is in before telemetry or demo mode is on,
  // and the only thing that makes an empty frameless widget draggable.
  return (
    <Surface>
      <Panel title="Canvas · no telemetry">
        <Canvas initial={DASH_PRESETS["race-engineer"].layout} live={false} />
      </Panel>
    </Surface>
  );
}
