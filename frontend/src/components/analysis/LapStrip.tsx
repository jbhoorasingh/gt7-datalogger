// The lap strip (#102): the reference line unrolled into one bar under the
// map, coloured by the same lens, with the track's sectors above it and the
// corner numbers below. The zoom window every panel shares is drawn on it
// and driven from it — a drag picks a stretch, the window's edges resize
// it, its middle slides it, a click goes to the corner under the pointer —
// so with the map off screen the zoom is still in reach. A compact copy
// sits over the charts for the same reason.
//
// Drags report the window live through `onLiveZoom` (the map follows frame
// by frame) and commit through `onZoomChange` on release, which is when the
// charts, heavier to re-zoom, get it.

import { useRef } from "react";
import { cornerAt, cornerRange, selectedCorner, stepCorner } from "@/lib/cornerZoom";
import { speedUnit, speedValue, type Units } from "@/lib/format";
import {
  dragRange,
  type Grab,
  grabAt,
  hasDragged,
  isFullLap,
  metresAt,
  MIN_SPAN_M,
  type Range,
} from "@/lib/lapStrip";
import type { LensRun } from "@/lib/mapLens";
import type { Corner, Sector } from "@/lib/types";
import type { SectorTiming } from "@/lib/sectorTiming";

function formatSectorDuration(ms: number): string {
  const rounded = Math.round(ms);
  if (rounded < 60_000) return `${(rounded / 1000).toFixed(3)}s`;
  const minutes = Math.floor(rounded / 60_000);
  const seconds = ((rounded % 60_000) / 1000).toFixed(3).padStart(6, "0");
  return `${minutes}:${seconds}`;
}

function formatSectorDelta(ms: number): string {
  const sign = ms > 0 ? "+" : ms < 0 ? "-" : "±";
  return `${sign}${(Math.abs(ms) / 1000).toFixed(3)}s`;
}
export function LapStrip({
  length,
  runs,
  sectors,
  sectorTimings = {},
  corners,
  zoomRange,
  onZoomChange,
  onLiveZoom,
  cursorDist,
  onCursorDist,
  units = "metric",
  compact = false,
}: {
  /** The reference lap's length in metres. */
  length: number;
  /** The bar's colouring, as contiguous runs along the lap. */
  runs: LensRun[];
  sectors: Sector[];
  sectorTimings?: Record<number, SectorTiming>;
  corners: Corner[];
  zoomRange: Range | null;
  onZoomChange: (range: Range | null) => void;
  /** The window while a drag is in progress; null once it ends. */
  onLiveZoom?: (range: Range | null) => void;
  cursorDist: number | null;
  onCursorDist?: (dist: number | null) => void;
  units?: Units;
  /** The 8 px copy over the charts: no footer, tighter rows. */
  compact?: boolean;
}) {
  const grab = useRef<{ grab: Grab; range: Range | null; moved: boolean; live: Range | null } | null>(
    null,
  );
  const current = selectedCorner(corners, zoomRange);
  const currentSector = sectors.find(
    (sector) =>
      zoomRange != null &&
      Math.abs(zoomRange[0] - sector.start_dist) < 1 &&
      Math.abs(zoomRange[1] - sector.end_dist) < 1,
  );

  const zoomed = !!zoomRange;
  const pct = (m: number) => `${(m / Math.max(length, 1)) * 100}%`;

  const at = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, width: rect.width };
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || length <= 0) return;
    const { x, width } = at(e);
    grab.current = { grab: grabAt(x, width, length, zoomRange), range: zoomRange, moved: false, live: null };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (length <= 0) return;
    const { x, width } = at(e);
    const d = metresAt(x, width, length);
    onCursorDist?.(d);
    const g = grab.current;
    if (!g) return;
    if (!g.moved && !hasDragged(g.grab, d)) return;
    g.moved = true;
    g.live = dragRange(g.grab, d, g.range, length);
    onLiveZoom?.(g.live);
  };
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const g = grab.current;
    grab.current = null;
    if (!g) return;
    onLiveZoom?.(null);
    if (g.moved) {
      const picked = g.live;
      if (!picked) return;
      // A new stretch too short to mean one, or one that is really the
      // whole lap, is the whole lap.
      if ((g.grab.mode === "new" && picked[1] - picked[0] < MIN_SPAN_M) || isFullLap(picked, length)) {
        onZoomChange(null);
      } else {
        onZoomChange(picked);
      }
      return;
    }
    const { x, width } = at(e);
    const corner = cornerAt(corners, metresAt(x, width, length));
    if (corner) onZoomChange(cornerRange(corner));
  };
  const onPointerLeave = () => {
    if (!grab.current) onCursorDist?.(null);
  };

  const barTop = compact ? 17 : 18;
  const barHeight = compact ? 8 : 12;
  const tickTop = compact ? 28 : 33;
  const stripHeight = compact ? 46 : 50;
  const navLabel = currentSector
    ? `S${currentSector.n}`
    : current
    ? `T${current.n} · ${current.direction === "L" ? "left" : "right"} · ${Math.round(
        speedValue(current.min_speed, units),
      )} ${speedUnit(units)} min`
    : zoomed
      ? "Custom stretch"
      : "Full lap";
  const [a, b] = zoomRange ?? [0, length];
  const rangeText = `${Math.round(a).toLocaleString()}–${Math.round(b).toLocaleString()} m`;

  return (
    <div>
      <div
        className="relative select-none"
        style={{
          height: stripHeight,
          touchAction: "none",
          cursor: grab.current?.grab.mode === "move" ? "grabbing" : "col-resize",
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onPointerLeave}
        onDoubleClick={() => onZoomChange(null)}
      >
        {/* Track sectors: S1…Sn above the bar; a click zooms to one. */}
        {sectors.map((s) => {
          const timing = sectorTimings[s.n];
          const duration = timing?.selectedMs != null
            ? `${timing.approximate ? "~" : ""}${formatSectorDuration(timing.selectedMs)}`
            : null;
          const delta = timing?.deltaMs != null
            ? formatSectorDelta(timing.deltaMs)
            : null;
          const on =
            zoomRange != null &&
            Math.abs(zoomRange[0] - s.start_dist) < 1 &&
            Math.abs(zoomRange[1] - s.end_dist) < 1;
          return (
            <button
              key={s.n}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onZoomChange([s.start_dist, s.end_dist])}
              title={s.name ? `${s.name} · ${s.authored ? "the track's sector" : "a third of the lap"}` : s.authored ? "The track's sector — click to zoom to it" : "A third of the lap — click to zoom to it"}
              className={`absolute top-0 h-3.5 overflow-hidden whitespace-nowrap border-l border-divider pl-1.5 text-left font-tabular text-[9.5px] font-semibold uppercase tracking-[.12em] transition-colors hover:text-ink ${
                on ? "text-accent-300" : "text-ink-faint"
              }`}
              style={{ left: pct(s.start_dist), width: pct(s.end_dist - s.start_dist) }}
            >
              S{s.n}
              {!compact && duration && (
                <span className="ml-1 font-normal normal-case tracking-normal">
                  {duration}
                </span>
              )}
              {!compact && delta && (
                <span
                  className={`ml-1 font-normal normal-case tracking-normal ${
                    timing.deltaMs! < 0
                      ? "text-emerald-400"
                      : timing.deltaMs! > 0
                        ? "text-rose-400"
                        : "text-ink-dim"
                  }`}
                >
                  {delta}
                </span>
              )}
            </button>
          );
        })}
        <svg
          width="100%"
          height={barHeight}
          className="absolute left-0 block overflow-hidden rounded-[3px]"
          style={{ top: barTop }}
          preserveAspectRatio="none"
          viewBox={`0 0 ${Math.max(length, 1)} ${barHeight}`}
        >
          {runs.map((r, i) => (
            <rect
              key={i}
              x={r.start}
              y={0}
              width={Math.max(r.end - r.start, 0) + length * 0.0008}
              height={barHeight}
              fill={r.color}
              opacity={0.85}
            />
          ))}
        </svg>
        {/* Corner numbers under the bar, at their apex. */}
        {corners.map((c) => (
          <span
            key={c.n}
            className={`pointer-events-none absolute -translate-x-1/2 font-tabular text-[10px] font-semibold ${
              current?.n === c.n
                ? "text-accent-300"
                : zoomRange && c.apex_dist >= zoomRange[0] && c.apex_dist <= zoomRange[1]
                  ? "text-ink-soft"
                  : "text-ink-faint"
            }`}
            style={{ left: pct(c.apex_dist), top: tickTop }}
          >
            {c.n}
          </span>
        ))}
        {zoomRange && (
          <>
            <div
              className="pointer-events-none absolute bottom-0 left-0"
              style={{ top: barTop - 2, width: pct(zoomRange[0]), background: "rgba(11,13,16,.6)" }}
            />
            <div
              className="pointer-events-none absolute bottom-0 right-0"
              style={{ top: barTop - 2, width: pct(length - zoomRange[1]), background: "rgba(11,13,16,.6)" }}
            />
            <div
              className="pointer-events-none absolute bottom-0 rounded-[3px] border border-accent bg-accent/8"
              style={{ top: barTop - 3, left: pct(zoomRange[0]), width: pct(zoomRange[1] - zoomRange[0]) }}
            >
              {!compact && (
                <>
                  <span className="absolute -left-[3px] top-1 h-3 w-[5px] rounded-[2px] bg-accent-300" />
                  <span className="absolute -right-[3px] top-1 h-3 w-[5px] rounded-[2px] bg-accent-300" />
                </>
              )}
            </div>
          </>
        )}
        {cursorDist != null && cursorDist >= 0 && cursorDist <= length && (
          <span
            className="pointer-events-none absolute w-px bg-white"
            style={{ left: pct(cursorDist), top: barTop - 3, height: barHeight + 6 }}
          />
        )}
      </div>
      {!compact && (
        <div className="flex flex-wrap items-center gap-2 pb-1 pt-1.5 text-[11px]">
          <button
            onClick={() => {
              const next = stepCorner(corners, current, -1);
              if (next) onZoomChange(cornerRange(next));
            }}
            aria-label="Previous corner"
            disabled={corners.length === 0}
            className="rounded border border-edge px-[7px] text-ink-dim transition-colors hover:border-edge-bright hover:text-ink disabled:opacity-40"
          >
            ‹
          </button>
          <button
            onClick={() => {
              const next = stepCorner(corners, current, 1);
              if (next) onZoomChange(cornerRange(next));
            }}
            aria-label="Next corner"
            disabled={corners.length === 0}
            className="rounded border border-edge px-[7px] text-ink-dim transition-colors hover:border-edge-bright hover:text-ink disabled:opacity-40"
          >
            ›
          </button>
          <span className="text-ink-soft">{navLabel}</span>

          <span className="font-tabular text-ink-faint">{rangeText}</span>
          {zoomed && (
            <button
              onClick={() => onZoomChange(null)}
              className="rounded border border-edge px-[9px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
            >
              Full lap
            </button>
          )}
          <span className="ml-auto text-[10px] text-ink-faint">
            drag the strip to pick a stretch · drag its edges to resize · [ ] steps corners
          </span>
        </div>
      )}
    </div>
  );
}
