// The Analysis playback transport: play / scrub / speed over the reference
// lap, with the driver's hands at the playhead beside it. The clock it owns
// feeds the same `onCursorDist` the chart hover does, so the stacked charts,
// the race-line dot and the corner panels all follow it.
//
// Every readout is the recording's: `COMPARE_FULL` carries the broadcast
// `steer` column, so the wheel turns by the rotation GT7 actually reported.
//
// The playhead is the component's own state with no prop to set it, so a cell
// that wants the lap somewhere other than the start scrubs the real range
// input — React's own value setter plus an `input` event, one frame after
// mount so it lands after the transport's own rewind-on-new-lap effect.

import { PlaybackBar, timeAtDist } from "gt7-datalogger-frontend";
import type { LapSummary, PlaybackSeries } from "gt7-datalogger-frontend";
import { useEffect, useRef } from "react";
import { COMPARE_FULL } from "../fixtures/analysis-full";
import { REF_LAP } from "../fixtures/analysis";
import { Surface } from "../preview-shell";

const SERIES: PlaybackSeries = COMPARE_FULL.laps[String(REF_LAP)].series;

// The reference lap's own summary row, for the car and lap fields.
const LAP: LapSummary = {
  id: REF_LAP,
  session_id: 228,
  number: 2,
  time_ms: 58963,
  car_id: 63,
  car_name: "Corolla Levin 1600GT APEX (AE86) '83",
  track_name: "Tsukuba Circuit",
} as LapSummary;

function indexOfMax(score: (i: number) => number): number {
  let best = -Infinity;
  let at = 0;
  for (let i = 0; i < SERIES.t.length; i++) {
    const v = score(i);
    if (v > best) {
      best = v;
      at = i;
    }
  }
  return at;
}

const s = SERIES;
// Hardest braking of the lap: the pedal against the speed it is scrubbing off.
const BRAKING = timeAtDist(SERIES, s.dist[indexOfMax((i) => (s.brake[i] ?? 0) * s.speed[i])]);
// The slowest point of the lap — the hairpin apex.
const APEX = timeAtDist(SERIES, s.dist[indexOfMax((i) => -s.speed[i])]);

function Transport({
  seekTo = null,
  play = false,
}: {
  /** Seconds into the reference lap to scrub to on mount. */
  seekTo?: number | null;
  play?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (seekTo != null) {
        const input = root.current?.querySelector<HTMLInputElement>('input[type="range"]');
        const setter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )?.set;
        if (input && setter) {
          setter.call(input, String(seekTo));
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      }
      if (play) {
        root.current
          ?.querySelector<HTMLButtonElement>('button[aria-label="Play lap"]')
          ?.click();
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [seekTo, play]);

  return (
    <Surface width={900}>
      <div ref={root}>
        <PlaybackBar series={SERIES} lap={LAP} onCursorDist={() => {}} />
      </div>
    </Surface>
  );
}

export function AtTheStart() {
  // The transport as the Analysis view mounts it: playhead at zero against the
  // reference lap's 0:58.942, 1× selected, and the strip showing the car as it
  // crossed the line — on the throttle in 4th, wheel close to straight.
  return <Transport />;
}

export function HardOnTheBrakes() {
  // Scrubbed to the lap's hardest stop, two seconds in at the end of the
  // straight: brake bar full red, throttle shut, still in 4th and still
  // carrying 185 km/h.
  return <Transport seekTo={BRAKING} />;
}

export function AtTheApex() {
  // The slowest point of the lap, 61 km/h in 1st at the hairpin: throttle
  // just cracked open, no brake, and the wheel at the lock GT7 reported.
  return <Transport seekTo={APEX} />;
}

export function Playing() {
  // The play button pressed: it becomes a pause, and the lap runs on its own
  // clock from wherever the playhead sits — the state in which this transport
  // is driving every cursor-synced panel on the page.
  return <Transport play />;
}
