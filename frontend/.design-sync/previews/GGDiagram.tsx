// The traction circle: every moment of the lap as lateral g against
// longitudinal g, coloured by what the driver's feet were doing. How much of
// the ring gets used is the reading — the diagonals are trail-braking and
// picking up power while still turning.
//
// Straight off the recording: `COMPARE_FULL` carries the broadcast `acc_lat`
// and `acc_long` columns, and `ggLap` converts them with the server's own
// fitted scale, exactly as the Analysis view does.

import { GGDiagram, ggLap, type AccelCalibration, type MapLap } from "gt7-datalogger-frontend";
import { COMPARE_FULL } from "../fixtures/analysis-full";
import { REF_LAP, SELECTED } from "../fixtures/analysis";
import { Panel, Surface, lapColors } from "../preview-shell";

const IDS = Object.keys(COMPARE_FULL.laps);
const COLORS = lapColors([...SELECTED], REF_LAP);
const LABELS: Record<string, string> = {
  "1063": "L2 · 0:58.963 (ref)",
  "1065": "L4 · 1:00.235",
  "1068": "L7 · 1:00.207",
};

const ACCEL = COMPARE_FULL.accel;
const MAP_LAPS: MapLap[] = IDS.map((id) => ({
  id,
  entry: COMPARE_FULL.laps[id],
  color: COLORS[id],
  label: LABELS[id] ?? `Lap ${id}`,
  isRef: id === String(REF_LAP),
}));
// Exactly AnalysisView's `ggLaps`.
const LAPS = MAP_LAPS.map((lap) => ggLap(lap, ACCEL)).filter((l) => l != null);

// The fit's own verdict, turned down: what a lap with too little steady
// cornering or braking in it leaves the panel able to say.
const UNVERIFIED: AccelCalibration = {
  ...ACCEL,
  lateral: { ...ACCEL.lateral!, fitted: false, r2: 0.38, samples: 210 },
  longitudinal: { ...ACCEL.longitudinal!, fitted: false, r2: 0.44, samples: 265 },
};

// The most loaded moment of the reference lap that still has the brake on —
// the trail-braking corner of the envelope, where the cursor is worth parking.
const TRAIL_BRAKE = (() => {
  const ref = LAPS.find((l) => l.isRef)!;
  const { brake, dist } = ref.entry.series;
  let best = 0;
  let at = dist[0];
  for (let i = 0; i < ref.lat.length; i++) {
    if ((brake?.[i] ?? 0) < 10) continue;
    const load = Math.hypot(ref.lat[i], ref.long[i]);
    if (load > best) {
      best = load;
      at = dist[i];
    }
  }
  return at;
})();

function Card({
  laps = LAPS,
  accel = ACCEL,
  cursorDist = null,
}: {
  laps?: typeof LAPS;
  accel?: AccelCalibration;
  cursorDist?: number | null;
}) {
  return (
    <Surface width={330}>
      <Panel title="Traction circle — g-g">
        <GGDiagram laps={laps} accel={accel} cursorDist={cursorDist} />
      </Panel>
    </Surface>
  );
}

export function Session() {
  // Three Tsukuba laps in an AE86. The ring is full at the bottom and the
  // sides and thin at the top — the car brakes and corners far harder than it
  // accelerates — and the four peak figures under it are the lap's own, taken
  // from the raw ticks rather than this resampled trace.
  return <Card />;
}

export function AtTheCursor() {
  // The shared playhead parked on the lap's hardest trail-brake: every lap's
  // ring picks up a hollow marker at where it was at that point on track, so
  // the three can be read against each other at one moment of the lap.
  return <Card cursorDist={TRAIL_BRAKE} />;
}

export function ReferenceOnly() {
  // One lap selected. The reference draws heavier than the others do, so a
  // single lap reads as its own envelope rather than as one of a set.
  return <Card laps={LAPS.filter((l) => l.isRef)} />;
}

export function ScaleUnverified() {
  // GT7 documents neither the unit nor the sign of these two channels, so the
  // scale is fitted against physics the lap already recorded. A lap with too
  // little steady cornering or braking cannot support that fit, and the panel
  // says so instead of printing confident numbers on an unproven scale.
  return <Card accel={UNVERIFIED} />;
}
