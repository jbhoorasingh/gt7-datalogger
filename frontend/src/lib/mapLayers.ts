// What the race-line map draws on top of the laps themselves: detected
// chassis events (#103) and the stretches a driver aid was intervening (#104).
// Pure geometry, kept out of RaceLineMap so it can be tested without a chart.
//
// Both layers exist for the same reason. A band on a chart says an event
// happened at 1,840 m; a mark on the track says it happened on the way into
// turn 3, and three laps' marks in one braking zone say it happens there
// every time — which is the reading that changes how a corner is driven.

import { positionAtDist } from "@/lib/playback";
import type { CompareLapEntry, EventType, LapEvent, Samples } from "@/lib/types";

export type MapLayerKey = "events" | "tcs" | "asm";
export type MapLayers = Record<MapLayerKey, boolean>;

export const EVENT_LABELS: Record<EventType, string> = {
  lockup: "Lockup",
  wheelspin: "Wheelspin",
  bottoming: "Bottoming",
  kerb: "Kerb strike",
};

const WHEEL_LABELS: Record<string, string> = {
  fl: "front left",
  fr: "front right",
  rl: "rear left",
  rr: "rear right",
};

export interface EventMarker {
  x: number;
  z: number;
  event: LapEvent;
}

function inRange(dist: number, range: [number, number] | null | undefined): boolean {
  return !range || (dist >= range[0] && dist <= range[1]);
}

// Suspension events are detected per wheel, so a car that bottoms out on all
// four corners at once arrives as four events a metre apart. Drawn as four
// markers they are one blob with one reachable tooltip; merged they are one
// marker that names the wheels.
const MERGE_WITHIN_M = 6;

const WHEEL_ORDER = ["fl", "fr", "rl", "rr"];

function mergeNearby(events: LapEvent[]): LapEvent[] {
  const out: LapEvent[] = [];
  for (const event of [...events].sort((a, b) => a.start_dist - b.start_dist)) {
    let into: LapEvent | undefined;
    for (let i = out.length - 1; i >= 0 && !into; i--) {
      if (event.start_dist - out[i].start_dist > MERGE_WITHIN_M) break;
      if (out[i].type === event.type) into = out[i];
    }
    if (!into) {
      out.push({ ...event, wheels: [...event.wheels] });
      continue;
    }
    into.end_dist = Math.max(into.end_dist, event.end_dist);
    into.wheels = WHEEL_ORDER.filter((w) => into.wheels.includes(w) || event.wheels.includes(w));
    // "Worst" runs the other way for a lockup: the lower the slip ratio, the
    // closer the wheel was to stopped.
    into.severity =
      event.type === "lockup"
        ? Math.min(into.severity, event.severity)
        : Math.max(into.severity, event.severity);
  }
  return out;
}

/**
 * Where each of a lap's events sits on the track: the position the lap was
 * at when the event began. The start, not the middle — a lockup is
 * interesting for where the wheel stopped turning, and the rest of the band
 * is the driver dealing with it.
 *
 * Event distances arrive on the same axis as the entry's series (the compare
 * endpoint moves both onto the reference lap's), so they can be looked up
 * directly. An event past the end of the series — a lap that could not be
 * aligned all the way — has no position and is dropped rather than pinned
 * to the last sample.
 */
export function eventMarkers(
  entry: CompareLapEntry,
  range?: [number, number] | null,
): EventMarker[] {
  const dist = entry.series.dist;
  if (!dist || dist.length === 0) return [];
  const last = dist[dist.length - 1];
  const out: EventMarker[] = [];
  for (const event of mergeNearby(entry.events ?? [])) {
    if (event.start_dist < dist[0] || event.start_dist > last) continue;
    if (!inRange(event.start_dist, range)) continue;
    const at = positionAtDist(entry.series, event.start_dist);
    if (at) out.push({ x: at[0], z: at[1], event });
  }
  return out;
}

/** Whether any sample of the lap has this aid bit set. */
export function hasAid(series: Samples, bit: number): boolean {
  return !!series.aids?.some((v) => (v & bit) !== 0);
}

/**
 * Track positions of every sample where the aid was active. One point per
 * sample, not one per activation: the run of dots IS the reading, since how
 * far down the exit the aid kept working matters as much as where it began.
 */
export function aidPoints(
  series: Samples,
  bit: number,
  range?: [number, number] | null,
): [number, number][] {
  const { aids, dist, pos_x: xs, pos_z: zs } = series;
  if (!aids || !dist || !xs || !zs) return [];
  const n = Math.min(aids.length, dist.length, xs.length, zs.length);
  const out: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    if ((aids[i] & bit) === 0 || !inRange(dist[i], range)) continue;
    if (xs[i] == null || zs[i] == null) continue;
    out.push([xs[i], zs[i]]);
  }
  return out;
}

/**
 * Severity in words. It is a different quantity per event type (events.py):
 * the worst wheel-slip ratio for slip events, and how far into the lap's
 * range of suspension travel the wheel was pushed for the others.
 */
export function severityText(event: LapEvent): string {
  switch (event.type) {
    case "lockup":
      return `slowest wheel at ${Math.round(event.severity * 100)}% of road speed`;
    case "wheelspin":
      return `fastest wheel at ${Math.round(event.severity * 100)}% of road speed`;
    case "bottoming":
    case "kerb":
      return `compressed to ${Math.round(event.severity * 100)}% of the lap's travel`;
  }
}

export function wheelsText(wheels: string[]): string {
  if (wheels.length === 4) return "all four wheels";
  return wheels.map((w) => WHEEL_LABELS[w] ?? w).join(", ");
}
