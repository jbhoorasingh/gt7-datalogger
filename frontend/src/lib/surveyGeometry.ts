// Geometry for the survey map's forming track: the run's accumulated border
// edge points pair up left↔right wherever they face each other across the
// road, filling in the confirmed surface span by span. Matching is purely
// local — no lap ordering or centerline needed — so partial coverage just
// fills in as laps add evidence.

import type { SurveyEdge } from "@/lib/types";

// Matched pairs must look like a road cross-section: the line from the left
// point to the right point should run along the car's right-normal, between
// plausible road widths, with both contacts driven in the same direction.
const ROAD_WIDTH_MIN_M = 3;
const ROAD_WIDTH_MAX_M = 40;
const ACROSS_MIN_DOT = 0.85; // L→R direction vs right-normal: cos ~32°
const HEADING_MIN_DOT = 0.7; // both points from the same direction of travel
const QUAD_HALF_LENGTH_M = 2.5; // along-track extent of one filled span
// Spatial hash cell for the right-side candidates: one cell spans the whole
// search radius, so scanning the 3×3 neighborhood covers every match. Keeps
// the pairing near-linear as edge points accumulate into the thousands.
const CELL_M = ROAD_WIDTH_MAX_M;

// The trail is a time sequence, not a continuous path: pit returns, resets
// and session restarts teleport the car, and naively connecting consecutive
// points across such a jump draws chords through the middle of the map (and
// inflated gap lengths by the jump distance). Steps longer than this are
// treated as discontinuities everywhere the trail is walked.
export const TRAIL_JUMP_M = 30;

// --- travel-direction arrows ---------------------------------------------------

// "Left border" means left OF TRAVEL, which on a map can sit on either
// screen side — these arrows along the driven path are what make the
// distinction readable at a glance.
export interface DirectionArrow {
  x: number;
  z: number;
  dx: number; // unit travel direction at this point
  dz: number;
}

const ARROW_COUNT = 14;

export function directionArrows(trail: [number, number][]): DirectionArrow[] {
  if (trail.length < 10) return [];
  const arrows: DirectionArrow[] = [];
  const step = Math.max(1, Math.floor(trail.length / ARROW_COUNT));
  for (let i = step; i < trail.length - 1; i += step) {
    const [ax, az] = trail[i - 1];
    const [bx, bz] = trail[i + 1];
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 0.5 || len > 2 * TRAIL_JUMP_M) continue; // parked or teleport
    arrows.push({
      x: trail[i][0],
      z: trail[i][1],
      dx: (bx - ax) / len,
      dz: (bz - az) / len,
    });
  }
  return arrows;
}

// --- perimeter completeness --------------------------------------------------

export interface SideCoverage {
  pct: number; // % of the driven loop with this border evidenced nearby
  largestGapM: number; // longest uncovered stretch — where to drive next
  closed: boolean;
  // Uncovered stretches as polylines, offset a few meters toward the side
  // they belong to — drawn dashed on the map so gaps are findable, not
  // just countable. Only stretches long enough to matter are included.
  gapSegments: [number, number][][];
  largestGapAt: [number, number] | null; // midpoint of the largest gap
}

export interface Coverage {
  left: SideCoverage;
  right: SideCoverage;
  roadPct: number; // % of the loop where BOTH borders are known
}

// A trail point counts as covered when border evidence exists nearby, with
// the reach depending on how the evidence's travel direction compares:
//
// - Aligned (within ~60°): full radius, spanning the whole road width — a
//   lap hugging the right edge must not read as "left border missing" when
//   the left border is mapped just across the road (that was a ghost gap).
// - Rotated but not opposite: short radius only. A tight hairpin turns the
//   heading ~180° across a few dozen meters, so the corner's own evidence
//   is heavily rotated yet CLOSE — without this tier hairpins report
//   eternal gaps in sections the driver plainly traced.
// - Opposite (beyond ~120°): a SHORT radius only — shorter than the rotated
//   tier. This used to be "never, at any distance", on the theory that
//   antiparallel evidence must belong to the other leg of a hairpin. That
//   produced a reproducible false gap: on a real East End run the car drove
//   127 m down the middle of a fully-mapped 12.7 m road, left evidence 7.8 m
//   to port and right evidence 5.6 m to starboard, and BOTH borders reported
//   a gap because every point there had been recorded traversing that
//   stretch the other way (dot -1.00). The beacon then sent the driver back
//   over finished ground. A border belongs to the road, not to the direction
//   it was first seen from; at these distances nothing else can physically
//   be beside you. The far leg of a hairpin sits well outside this radius,
//   so the ghost it was guarding against stays guarded.
const COVER_RADIUS_M = 30;
const COVER_RADIUS_NEAR_M = 15;
const COVER_RADIUS_OPPOSITE_M = 10;
const HEADING_ALIGN_MIN_DOT = 0.5;
const HEADING_OPPOSITE_DOT = -0.5;
const COVER_MIN_TRAIL = 50; // too little driving to grade against
const CLOSED_MIN_PCT = 97;
const CLOSED_MAX_GAP_M = 40;
const GAP_MIN_DRAW_M = 12; // don't clutter the map with sub-noise stretches
// Gap beacons are drawn out where the border should be, not beside the
// driven line: a fixed 4 m offset put them ~2.5 m INSIDE a 13 m road, so the
// marker sat between the two borders and pointed at tarmac rather than at
// the edge the driver is being sent to touch. The offset instead follows the
// road's own half-width, taken from how far evidence actually sits from the
// trail on the stretches that DO have it.
const GAP_OFFSET_FALLBACK_M = 6;
const GAP_OFFSET_MIN_M = 3;

// Grade the border evidence against the driven loop: for every trail point,
// is there border evidence of each side nearby? The trail is the reference
// "lap loop", so the percentages read as how much of the track's perimeter
// has been established — and the largest gap says where to go touch next.
export function borderCoverage(
  trail: [number, number][],
  edges: SurveyEdge[],
): Coverage | null {
  if (trail.length < COVER_MIN_TRAIL) return null;
  // Evidence bucketed by cell (cell size = radius, so scanning the 3×3
  // neighborhood sees everything within COVER_RADIUS_M), matched by exact
  // distance + travel-direction alignment.
  const cellsBySide: Record<"L" | "R", Map<string, SurveyEdge[]>> = {
    L: new Map(),
    R: new Map(),
  };
  for (const e of edges) {
    // Run-off limits are excluded from the road FILL (they bound the
    // run-off, not the road) but they absolutely count as coverage: the
    // driver traced that section's boundary on purpose, and grading it as
    // a gap forever would send them back to re-drive finished work.
    const key = `${Math.floor(e.x / COVER_RADIUS_M)}:${Math.floor(e.z / COVER_RADIUS_M)}`;
    const bucket = cellsBySide[e.side].get(key);
    if (bucket) bucket.push(e);
    else cellsBySide[e.side].set(key, [e]);
  }
  // Distance to the nearest qualifying evidence, or null when this side is
  // unevidenced here. The distance feeds the gap beacons' offset: on the
  // stretches that ARE mapped it is how far the border sits from the driven
  // line, which is the best estimate available for where to draw the border
  // on the stretches that are not.
  const near = (
    cells: Map<string, SurveyEdge[]>,
    x: number,
    z: number,
    tdx: number,
    tdz: number,
  ): number | null => {
    const cx = Math.floor(x / COVER_RADIUS_M);
    const cz = Math.floor(z / COVER_RADIUS_M);
    let best: number | null = null;
    for (let gx = cx - 1; gx <= cx + 1; gx++) {
      for (let gz = cz - 1; gz <= cz + 1; gz++) {
        for (const e of cells.get(`${gx}:${gz}`) ?? []) {
          const dot = e.hx * tdx + e.hz * tdz;
          const reach =
            dot >= HEADING_ALIGN_MIN_DOT
              ? COVER_RADIUS_M
              : dot >= HEADING_OPPOSITE_DOT
                ? COVER_RADIUS_NEAR_M
                : COVER_RADIUS_OPPOSITE_M;
          const dx = e.x - x;
          const dz = e.z - z;
          const d2 = dx * dx + dz * dz;
          if (d2 > reach * reach) continue;
          const d = Math.sqrt(d2);
          if (best === null || d < best) best = d;
        }
      }
    }
    return best;
  };

  interface Gap {
    start: number; // trail indices, inclusive
    end: number;
    lengthM: number;
  }
  const covered = { L: 0, R: 0 };
  const borderDistances: number[] = [];
  let both = 0;
  const gaps: Record<"L" | "R", Gap[]> = { L: [], R: [] };
  const open: Record<"L" | "R", Gap | null> = { L: null, R: null };
  let prev: [number, number] | null = null;
  trail.forEach(([x, z], i) => {
    const stepM = prev ? Math.hypot(x - prev[0], z - prev[1]) : 0;
    prev = [x, z];
    // A teleport is not driven distance: close any open gap at it (the gap
    // ended where the car vanished) and contribute nothing to gap lengths —
    // otherwise gaps span the jump and draw as chords across the map.
    const jump = stepM > TRAIL_JUMP_M;
    if (jump) {
      for (const side of ["L", "R"] as const) {
        if (open[side]) {
          gaps[side].push(open[side]);
          open[side] = null;
        }
      }
    }
    const drivenStep = jump ? 0 : stepM;
    // Direction of travel at this trail point, for the evidence heading gate.
    const [ax, az] = trail[Math.max(0, i - 1)];
    const [bx, bz] = trail[Math.min(trail.length - 1, i + 1)];
    const dirLen = Math.hypot(bx - ax, bz - az) || 1;
    const tdx = (bx - ax) / dirLen;
    const tdz = (bz - az) / dirLen;
    let onRoad = true;
    for (const side of ["L", "R"] as const) {
      const distance = near(cellsBySide[side], x, z, tdx, tdz);
      if (distance !== null) {
        covered[side]++;
        borderDistances.push(distance);
        if (open[side]) {
          gaps[side].push(open[side]);
          open[side] = null;
        }
      } else {
        onRoad = false;
        if (open[side]) {
          open[side].end = i;
          open[side].lengthM += drivenStep;
        } else {
          open[side] = { start: i, end: i, lengthM: 0 };
        }
      }
    }
    if (onRoad) both++;
  });
  for (const side of ["L", "R"] as const) {
    if (open[side]) gaps[side].push(open[side]);
  }

  // How far this track's border sits from the driven line, median over the
  // stretches that have evidence (see `near`). A gap has none of its own, so
  // its border position can only be inferred from the rest of the lap.
  const borderOffsetM = (() => {
    if (!borderDistances.length) return GAP_OFFSET_FALLBACK_M;
    const sorted = [...borderDistances].sort((a, b) => a - b);
    return Math.max(GAP_OFFSET_MIN_M, sorted[Math.floor(sorted.length / 2)]);
  })();

  // A gap drawn ON the trail would be ambiguous between sides — offset each
  // stretch out toward the side whose border is missing there.
  const offsetSegment = (start: number, end: number, sign: number): [number, number][] => {
    const points: [number, number][] = [];
    for (let i = start; i <= end; i++) {
      const [x, z] = trail[i];
      const [ax, az] = trail[Math.max(0, i - 1)];
      const [bx, bz] = trail[Math.min(trail.length - 1, i + 1)];
      const len = Math.hypot(bx - ax, bz - az) || 1;
      const dx = (bx - ax) / len;
      const dz = (bz - az) / len;
      // Right normal is (dz, -dx); the left side sits along its negation.
      points.push([x + sign * dz * borderOffsetM, z + sign * -dx * borderOffsetM]);
    }
    return points;
  };

  const grade = (side: "L" | "R"): SideCoverage => {
    const pct = (covered[side] / trail.length) * 100;
    const sign = side === "L" ? -1 : 1;
    const drawable = gaps[side].filter((g) => g.lengthM >= GAP_MIN_DRAW_M && g.end > g.start);
    const largestGapM = gaps[side].reduce((max, g) => Math.max(max, g.lengthM), 0);
    let largestGapAt: [number, number] | null = null;
    const largest = drawable.reduce<Gap | null>(
      (best, g) => (g.lengthM > (best?.lengthM ?? 0) ? g : best),
      null,
    );
    if (largest) {
      const line = offsetSegment(largest.start, largest.end, sign);
      largestGapAt = line[Math.floor(line.length / 2)];
    }
    return {
      pct,
      largestGapM,
      closed: pct >= CLOSED_MIN_PCT && largestGapM <= CLOSED_MAX_GAP_M,
      gapSegments: drawable.map((g) => offsetSegment(g.start, g.end, sign)),
      largestGapAt,
    };
  };
  return {
    left: grade("L"),
    right: grade("R"),
    roadPct: (both / trail.length) * 100,
  };
}

// Quads [x1,z1, x2,z2, x3,z3, x4,z4] spanning left→right wherever a left
// border point has a right border point directly across from it. Run-off
// limits bound the run-off, not the road, so they never contribute.
export function roadQuads(edges: SurveyEdge[]): number[][] {
  const usable = edges.filter((e) => e.kind !== "runoff");
  const rights = new Map<string, SurveyEdge[]>();
  for (const e of usable) {
    if (e.side !== "R") continue;
    const key = `${Math.floor(e.x / CELL_M)}:${Math.floor(e.z / CELL_M)}`;
    const bucket = rights.get(key);
    if (bucket) bucket.push(e);
    else rights.set(key, [e]);
  }
  const quads: number[][] = [];
  for (const l of usable) {
    if (l.side !== "L") continue;
    const rnx = l.hz; // right-normal of the left point's heading
    const rnz = -l.hx;
    let best: SurveyEdge | null = null;
    let bestDist = ROAD_WIDTH_MAX_M;
    const cx = Math.floor(l.x / CELL_M);
    const cz = Math.floor(l.z / CELL_M);
    for (let gx = cx - 1; gx <= cx + 1; gx++) {
      for (let gz = cz - 1; gz <= cz + 1; gz++) {
        for (const r of rights.get(`${gx}:${gz}`) ?? []) {
          const dx = r.x - l.x;
          const dz = r.z - l.z;
          const dist = Math.hypot(dx, dz);
          if (dist < ROAD_WIDTH_MIN_M || dist >= bestDist) continue;
          if ((dx * rnx + dz * rnz) / dist < ACROSS_MIN_DOT) continue;
          if (l.hx * r.hx + l.hz * r.hz < HEADING_MIN_DOT) continue;
          best = r;
          bestDist = dist;
        }
      }
    }
    if (!best) continue;
    let ax = l.hx + best.hx;
    let az = l.hz + best.hz;
    const alen = Math.hypot(ax, az) || 1;
    ax = (ax / alen) * QUAD_HALF_LENGTH_M;
    az = (az / alen) * QUAD_HALF_LENGTH_M;
    quads.push([
      l.x - ax, l.z - az,
      l.x + ax, l.z + az,
      best.x + ax, best.z + az,
      best.x - ax, best.z - az,
    ]);
  }
  return quads;
}
