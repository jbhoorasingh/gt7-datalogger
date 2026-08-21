// Transport for lap playback on Analysis (#59): play / pause / scrub / speed,
// owning a rAF clock that feeds the SAME onCursorDist the chart hover uses —
// so the stacked charts, the race-line dot and the Corner Detail panel all
// animate without knowing playback exists. Alongside it, a strip of live
// widgets (steering wheel, pedals, gear, speed) rendered from a LiveFrame
// synthesized at the playhead.
//
// The clock advances in the REFERENCE lap's own time (see lib/playback.ts),
// pauses itself when the tab is hidden, and under prefers-reduced-motion
// steps the cursor a few times a second instead of every frame.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GearWidget } from "@/components/widgets/GearWidget";
import { InputsWidget } from "@/components/widgets/InputsWidget";
import { SpeedWidget } from "@/components/widgets/SpeedWidget";
import { SteeringWidget } from "@/components/widgets/SteeringWidget";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Tip } from "@/components/ui/Tooltip";
import { formatLapTime } from "@/lib/format";
import {
  advancePlayhead,
  distAtTime,
  frameAtTime,
  PLAYBACK_SPEEDS,
  playbackEnd,
  type PlaybackSeries,
} from "@/lib/playback";
import type { LapSummary } from "@/lib/types";

// The playhead is sampled every animation frame; React state (time readout,
// scrub position, widget strip) commits at this rate, same trick as
// useLiveFrame (#32). The cursor itself goes out per frame — the consumers
// are already rAF-throttled — except under reduced motion.
const UI_UPDATE_HZ = 12;
const REDUCED_MOTION_HZ = 4;

export function PlaybackBar({
  series,
  lap,
  onCursorDist,
  onPlayingChange,
}: {
  series: PlaybackSeries;
  /** Summary of the reference lap, for the widget strip's car/lap fields. */
  lap?: LapSummary;
  onCursorDist: (dist: number | null) => void;
  /** Reported up so the view can let the playhead own the cursor over hover. */
  onPlayingChange?: (playing: boolean) => void;
}) {
  const endS = playbackEnd(series);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [tUI, setTUI] = useState(0); // throttled mirror of tRef for the UI
  const tRef = useRef(0);
  const playingRef = useRef(false);
  playingRef.current = playing;
  const speedRef = useRef(speed);
  speedRef.current = speed;

  const reducedMotion = useMemo(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  useEffect(() => onPlayingChange?.(playing), [playing, onPlayingChange]);

  // A different lap (or a refetch) is a different clock: rewind and stop.
  useEffect(() => {
    tRef.current = 0;
    setTUI(0);
    setPlaying(false);
  }, [series]);

  const pushCursor = useCallback(
    (tS: number) => onCursorDist(distAtTime(series, tS)),
    [series, onCursorDist],
  );

  // The clock. Wall-time deltas × the speed multiplier, so a dropped frame
  // never slows the lap down; stops (and stays) at the lap's end.
  useEffect(() => {
    if (!playing || endS <= 0) return;
    let raf = 0;
    let last = performance.now();
    let lastCommit = -Infinity;
    let lastCursor = -Infinity;
    const commitGap = 1000 / UI_UPDATE_HZ;
    const cursorGap = reducedMotion ? 1000 / REDUCED_MOTION_HZ : 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const { t, ended } = advancePlayhead(
        tRef.current, now - last, speedRef.current, endS,
      );
      last = now;
      tRef.current = t;
      if (now - lastCursor >= cursorGap) {
        lastCursor = now;
        pushCursor(t);
      }
      if (now - lastCommit >= commitGap || ended) {
        lastCommit = now;
        setTUI(t);
      }
      if (ended) setPlaying(false);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, endS, pushCursor, reducedMotion]);

  // A hidden tab gets no animation frames, so the lap would "jump" on return
  // — and playing to nobody is noise. Pause instead; the user resumes.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden && playingRef.current) setPlaying(false);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const seek = (tS: number) => {
    tRef.current = tS;
    setTUI(tS);
    pushCursor(tS);
  };

  const toggle = () => {
    if (!playing && tRef.current >= endS) seek(0); // replay from the top
    setPlaying((p) => !p);
  };

  const frame = useMemo(() => frameAtTime(series, tUI, lap), [series, tUI, lap]);
  const widgetProps = { frame, laps: [], w: 1, h: 1, options: {} };

  if (endS <= 0) return null;

  return (
    <div className="mb-2 flex flex-wrap items-center gap-3 rounded-lg bg-panel-2/40 px-3 py-2">
      <Tip content={playing ? "Pause" : "Play the reference lap (drives every cursor-synced panel)"}>
        <button
          onClick={toggle}
          aria-label={playing ? "Pause playback" : "Play lap"}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent hover:bg-accent/25"
        >
          {playing ? "❚❚" : "▶"}
        </button>
      </Tip>
      <input
        type="range"
        min={0}
        max={endS}
        step={endS / 2000}
        value={Math.min(tUI, endS)}
        onChange={(e) => seek(Number(e.target.value))}
        aria-label="Lap position"
        className="min-w-32 flex-1 accent-[#38bdf8]"
      />
      <span className="shrink-0 font-tabular text-xs text-ink-dim">
        {formatLapTime(Math.round(tUI * 1000))}
        <span className="opacity-60"> / {formatLapTime(Math.round(endS * 1000))}</span>
      </span>
      <SegmentedControl
        ariaLabel="Playback speed"
        value={String(speed)}
        onValueChange={(v) => setSpeed(Number(v))}
        options={PLAYBACK_SPEEDS.map((s) => ({ value: String(s), label: `${s}×` }))}
      />
      {/* The driver's hands at the playhead — live-dashboard widgets fed the
          synthesized frame, unchanged. */}
      <div className="hidden shrink-0 items-center gap-4 pl-1 sm:flex">
        <SteeringWidget {...widgetProps} variant="wheel" />
        <InputsWidget {...widgetProps} variant="bars-v" />
        <GearWidget {...widgetProps} variant="plain" />
        <SpeedWidget {...widgetProps} variant="digits" />
      </div>
    </div>
  );
}
