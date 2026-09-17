// The playback clock (#59) is the only piece of Analysis that moves on its
// own; these pin the properties the transport relies on: monotone advance,
// hard stop at the lap end, speed scaling, and a scrub that hands the clock a
// position instead of fighting it.

import { describe, expect, it } from "vitest";
import {
  advancePlayhead,
  distAtTime,
  frameAtTime,
  nearestIndex,
  playbackEnd,
  positionAtDist,
  positionAtTime,
  timeAtDist,
  type PlaybackSeries,
} from "./playback";

// A 100 m lap resampled every 10 m, driven at 20 m/s until halfway and
// 10 m/s after — so time is NOT linear in distance, which is the whole point
// of advancing the playhead on the lap's own clock.
function series(): PlaybackSeries {
  const dist = Array.from({ length: 11 }, (_, i) => i * 10);
  const t = dist.map((d) => (d <= 50 ? d / 20 : 2.5 + (d - 50) / 10));
  return {
    dist,
    t,
    speed: dist.map((d) => (d <= 50 ? 72 : 36)),
    gear: dist.map((d) => (d <= 50 ? 4 : 3)),
    throttle: dist.map(() => 100),
    brake: dist.map(() => 0),
    steer: dist.map((d) => d / 100),
  };
}

describe("advancePlayhead", () => {
  it("advances monotonically and scales with the speed multiplier", () => {
    let t = 0;
    const seen: number[] = [];
    for (let i = 0; i < 10; i++) {
      t = advancePlayhead(t, 100, 1, 60).t;
      seen.push(t);
    }
    expect(seen).toEqual(seen.slice().sort((a, b) => a - b));
    expect(t).toBeCloseTo(1.0);

    expect(advancePlayhead(0, 1000, 0.25, 60).t).toBeCloseTo(0.25);
    expect(advancePlayhead(0, 1000, 4, 60).t).toBeCloseTo(4);
  });

  it("stops exactly at the lap end and reports it", () => {
    const end = playbackEnd(series()); // 7.5 s
    expect(end).toBeCloseTo(7.5);
    const r = advancePlayhead(7.4, 1000, 2, end);
    expect(r.ended).toBe(true);
    expect(r.t).toBe(end);
    // Once at the end it stays there — no creep past the last sample.
    expect(advancePlayhead(end, 100, 1, end)).toEqual({ t: end, ended: true });
  });

  it("never runs backwards, even on a negative frame delta", () => {
    expect(advancePlayhead(3, -50, 1, 60).t).toBe(3);
  });
});

describe("distance/time mapping", () => {
  it("inverts: scrubbing to a distance resumes from that lap time", () => {
    const s = series();
    for (const dist of [0, 15, 50, 85, 100]) {
      expect(distAtTime(s, timeAtDist(s, dist))).toBeCloseTo(dist);
    }
  });

  it("advances on the lap's own clock — slow sectors play slowly", () => {
    const s = series();
    // 1 s of playback in the fast half covers 20 m; in the slow half, 10 m.
    expect(distAtTime(s, 1) - distAtTime(s, 0)).toBeCloseTo(20);
    expect(distAtTime(s, 4) - distAtTime(s, 3)).toBeCloseTo(10);
  });

  it("clamps outside the lap instead of extrapolating", () => {
    const s = series();
    expect(distAtTime(s, -1)).toBe(0);
    expect(distAtTime(s, 999)).toBe(100);
  });

  it("a scrub mid-play does not fight the clock", () => {
    const s = series();
    const end = playbackEnd(s);
    advancePlayhead(0, 1000, 1, end); // 1 s in when the user grabs the bar
    const t = timeAtDist(s, 80); // ...and drags the scrubber to 80 m
    const after = advancePlayhead(t, 500, 1, end).t;
    expect(distAtTime(s, after)).toBeGreaterThan(80); // continues from there
    expect(after).toBeCloseTo(t + 0.5);
  });
});

describe("time-synced map position (#75)", () => {
  // A straight along x, so a car's x IS its distance.
  const onStraight = (s: PlaybackSeries): PlaybackSeries => ({
    ...s,
    pos_x: [...s.dist],
    pos_z: s.dist.map(() => 0),
  });

  it("places a slower lap behind the reference at the same lap time", () => {
    const ref = onStraight(series());
    // Same lap, 10 % slower everywhere.
    const slow = onStraight({ ...series(), t: series().t!.map((t) => t * 1.1) });
    const tRef = timeAtDist(ref, 50); // reference car at 50 m, 2.5 s in
    expect(positionAtTime(ref, tRef)![0]).toBeCloseTo(50);
    const [x] = positionAtTime(slow, tRef)!;
    expect(x).toBeLessThan(50);
    // 2.5 s into the slow lap is 2.5 / 1.1 s of the reference's clock.
    expect(x).toBeCloseTo(distAtTime(ref, 2.5 / 1.1));
  });

  it("interpolates between steps rather than snapping to them", () => {
    const s = onStraight(series());
    expect(positionAtTime(s, 0.25)![0]).toBeCloseTo(5); // 20 m/s × 0.25 s
  });

  it("waits on the line before the start and parks at the finish", () => {
    const s = onStraight(series());
    expect(positionAtTime(s, -1)).toEqual([0, 0]);
    expect(positionAtTime(s, 999)![0]).toBe(100);
  });

  it("is null for a series without positions", () => {
    expect(positionAtTime(series(), 1)).toBeNull();
    expect(positionAtDist(series(), 10)).toBeNull();
  });

  it("puts the reference dot exactly at the cursor, not on the nearest step", () => {
    const s = onStraight(series());
    expect(positionAtDist(s, 12.5)![0]).toBeCloseTo(12.5);
    // ...which is what makes the time-synced gap exact: a lap identical to
    // the reference sits right on top of it, between steps too.
    for (const d of [12.5, 37.3, 81.9]) {
      expect(positionAtTime(s, timeAtDist(s, d))![0]).toBeCloseTo(positionAtDist(s, d)![0]);
    }
  });

  // A lap where GT7 moved the car 500 m inside one 5 m step (10 → 15 m)
  // that took 10.25 s. Speeds in km/h at the step's two ends say which side
  // of the reset the car spent that time standing on.
  function withReset(speedBefore?: number, speedAfter?: number): PlaybackSeries {
    return {
      dist: [0, 5, 10, 15, 20],
      t: [0, 0.25, 0.5, 10.75, 11.0],
      pos_x: [0, 5, 10, 510, 515],
      pos_z: [0, 0, 0, 0, 0],
      ...(speedBefore != null && speedAfter != null
        ? { speed: [72, 72, speedBefore, speedAfter, 72] }
        : {}),
    };
  }

  it("never slides a dot across a reset", () => {
    // Stopped, then reset: at 20 m/s the step is half a second of driving,
    // so the car waits where it stopped for the rest (reset at t ≈ 10.5 s).
    const stoppedFirst = withReset(0, 72);
    for (const t of [0.5, 3, 7, 10.4]) expect(positionAtTime(stoppedFirst, t)).toEqual([10, 0]);
    expect(positionAtTime(stoppedFirst, 10.6)).toEqual([510, 0]);
    // Reset, then stopped: it jumps almost at once and waits on the far side.
    const resetFirst = withReset(72, 0);
    expect(positionAtTime(resetFirst, 0.6)).toEqual([10, 0]);
    for (const t of [1, 5, 10.7]) expect(positionAtTime(resetFirst, t)).toEqual([510, 0]);
    // No speeds to go on: halfway through the step.
    const blind = withReset();
    expect(positionAtTime(blind, 5.5)).toEqual([10, 0]);
    expect(positionAtTime(blind, 5.7)).toEqual([510, 0]);
    // Past the step it drives on normally.
    expect(positionAtTime(blind, 10.875)![0]).toBeCloseTo(512.5);
    // On the distance axis it jumps where the nearest step would.
    expect(positionAtDist(blind, 12)).toEqual([10, 0]);
    expect(positionAtDist(blind, 13)).toEqual([510, 0]);
  });

  it("still glides where a car covered more ground than its distance says", () => {
    // 7× — as far as driven steps went on real recordings (a slide, or a run
    // of dropped frames): continuous motion, so it interpolates.
    const s: PlaybackSeries = {
      dist: [0, 5],
      t: [0, 1],
      pos_x: [0, 35],
      pos_z: [0, 0],
    };
    expect(positionAtTime(s, 0.5)![0]).toBeCloseTo(17.5);
    expect(positionAtDist(s, 2.5)![0]).toBeCloseTo(17.5);
  });
});

describe("nearestIndex", () => {
  it("reads a series at the point nearest a distance, including a short last step", () => {
    // Whole 5 m steps, then the lap's exact end.
    const dist = [0, 5, 10, 15, 17.5];
    expect(nearestIndex(dist, 7.4)).toBe(1);
    expect(nearestIndex(dist, 7.6)).toBe(2);
    expect(nearestIndex(dist, 16.8)).toBe(4); // index × step would say 3
    expect(nearestIndex(dist, 99)).toBe(4);
    expect(nearestIndex(dist, -3)).toBe(0);
    expect(nearestIndex([], 3)).toBe(0);
  });
});

describe("frameAtTime", () => {
  it("synthesizes a LiveFrame the widgets can render", () => {
    const f = frameAtTime(series(), 1.25);
    expect(f.speed_kmh).toBeCloseTo(72);
    expect(f.throttle).toBe(100);
    expect(f.gear).toBe(4);
    expect(f.lap_elapsed_ms).toBe(1250);
    expect(f.steer_rad).toBeCloseTo(0.25);
  });

  it("degrades to 'no data' when a channel is absent", () => {
    const s = series();
    delete s.steer;
    const f = frameAtTime(s, 1);
    expect(f.steer_rad).toBeNull();
    expect(f.position).toBe(-1); // no race_pos channel -> no position
  });
});
