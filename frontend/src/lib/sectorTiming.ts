import { timeAtDist } from "@/lib/playback";
import type { Sector } from "@/lib/types";

type TimedSeries = Parameters<typeof timeAtDist>[0];

// Sector boundaries arrive rounded to 0.1 m and the series' distances to
// 0.01 m, so a complete lap's line can sit up to 0.05 m past its last sample
// (a lap ending at 300.06 m has its final boundary at 300.1 m). That much is
// rounding; a boundary any further out is telemetry that stops short of it.
// The extra millimetre absorbs float error in the comparison.
const BOUNDARY_ROUNDING_M = 0.051;

// How far a lap's own clock may disagree with its reported lap time and
// still be the clock of that whole lap: the window a salvaged lap has to
// meet to be believed, and that processing.sectors.splits_ms applies
// (laps.SALVAGE_TOLERANCE_MS / SALVAGE_TOLERANCE_RATIO).
const CLOCK_TOLERANCE_MS = 500;
const CLOCK_TOLERANCE_RATIO = 0.005;

export interface SectorTiming {
  /** Duration for the reference lap, in milliseconds. */
  referenceMs: number | null;
  /** Duration for the focused comparison lap, or the reference if alone. */
  selectedMs: number | null;
  /** Positive means slower than reference; negative means faster. */
  deltaMs: number | null;
  /** Fallback distance thirds are not official circuit sectors. */
  approximate: boolean;
}

/** A lap as sector timing reads it. */
export interface TimedLap {
  /** The distance series, with the lap's own clock in `t` (seconds). */
  series: TimedSeries;
  /** The lap time GT7 reported, in milliseconds: the clock at the finish. */
  timeMs: number | null | undefined;
}

/** Which ends of a sector lie on the start/finish line. */
export interface LineEnds {
  /** The first sector: its clock starts at 0, the moment the lap began. */
  start: boolean;
  /** The last sector: its clock stops at the reported lap time. */
  finish: boolean;
}

const INSIDE_LAP: LineEnds = { start: false, finish: false };

/** Whether the lap's clock ran the whole of its reported lap time.
 *
 *  Every recorded lap has a lap time, GT7's for the whole lap, even one the
 *  logger joined part way round; but that lap's clock starts where the
 *  recording did, and one that froze through a stop the sampler skipped
 *  stops short too. The series cannot say where the missing time went (its
 *  grid starts at 0 whatever the lap covered, holding the first sample's
 *  clock before it), so a lap whose clock does not reach its lap time has
 *  no sector times at all, as in processing.sectors.splits_ms. */
function clockRanTheLap(time: number[], timeMs: number | null | undefined): timeMs is number {
  if (timeMs == null || !Number.isFinite(timeMs) || timeMs <= 0) return false;
  const end = time[time.length - 1] * 1000;
  const tolerance = Math.max(CLOCK_TOLERANCE_MS, timeMs * CLOCK_TOLERANCE_RATIO);
  return Number.isFinite(end) && Math.abs(timeMs - end) <= tolerance;
}

/** Time spent between two sector boundaries, using the lap's own clock.
 *
 *  The clock is anchored on the line (t = 0 is the crossing), but the
 *  series' first and last samples fall up to a frame inside the lap, and
 *  further where packets were dropped: read there, the first and last
 *  sectors come up short and two laps differ by where their samples fell.
 *  A boundary on the line reads 0 at the start and the lap time at the
 *  finish instead, as processing.sectors.splits_ms does, so the telemetry
 *  only has to reach the boundaries inside the lap. A compared lap's last
 *  sample, put on the reference's axis, can fall a metre or so either side
 *  of the reference's own; it still finished on the line. */
export function sectorDurationMs(
  sector: Sector,
  lap: TimedLap | null | undefined,
  line: LineEnds = INSIDE_LAP,
): number | null {
  const series = lap?.series;
  const dist = series?.dist;
  const time = series?.t;

  if (
    !lap ||
    !series ||
    !dist ||
    !time ||
    dist.length < 2 ||
    time.length !== dist.length ||
    !clockRanTheLap(time, lap.timeMs)
  ) {
    return null;
  }

  const start = sector.start_dist;
  const end = sector.end_dist;
  const first = dist[0];
  const last = dist[dist.length - 1];
  const covered = (d: number) =>
    d >= first - BOUNDARY_ROUNDING_M && d <= last + BOUNDARY_ROUNDING_M;

  if (
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    end <= start ||
    (!line.start && !covered(start)) ||
    (!line.finish && !covered(end))
  ) {
    return null;
  }

  // A boundary inside the rounding margin is read at the recorded end.
  const read = (d: number) =>
    timeAtDist(series, Math.min(Math.max(d, first), last)) * 1000;
  const startMs = line.start ? 0 : read(start);
  const endMs = line.finish ? lap.timeMs : read(end);

  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    return null;
  }

  return Math.round(endMs - startMs);
}

/** Compare the same sector on aligned reference and focused laps. */
export function compareSectorTimes(
  sector: Sector,
  reference: TimedLap | null | undefined,
  selected: TimedLap | null | undefined,
  aligned = true,
  line: LineEnds = INSIDE_LAP,
): SectorTiming {
  const referenceMs = sectorDurationMs(sector, reference, line);

  const selectedMs =
    selected == null
      ? referenceMs
      : aligned
        ? sectorDurationMs(sector, selected, line)
        : null;

  const deltaMs =
    selected != null &&
    aligned &&
    referenceMs != null &&
    selectedMs != null
      ? selectedMs - referenceMs
      : null;

  return {
    referenceMs,
    selectedMs,
    deltaMs,
    approximate: !sector.authored,
  };
}

/** Every sector of the lap compared, keyed by sector number. The sectors
 *  run in order from the line to the line: the first starts on it and the
 *  last ends on it. */
export function timeSectors(
  sectors: Sector[],
  reference: TimedLap | null | undefined,
  selected: TimedLap | null | undefined,
  aligned = true,
): Record<number, SectorTiming> {
  return Object.fromEntries(
    sectors.map((sector, i) => [
      sector.n,
      compareSectorTimes(sector, reference, selected, aligned, {
        start: i === 0,
        finish: i === sectors.length - 1,
      }),
    ]),
  );
}
