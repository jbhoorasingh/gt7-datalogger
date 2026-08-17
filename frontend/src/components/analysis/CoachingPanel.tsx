// Post-lap coaching notes (#23): the race engineer's findings as text — the
// exact wording voice would have used, replayed server-side from the stored
// session so they exist for people who never enable voice. Grouped per lap,
// newest first; a finding that names a corner zooms the charts and map to it
// on click, using the same corner-window convention as everything else.

import { cornerRange } from "@/components/analysis/RaceLineMap";
import type { CoachingLapNotes, Corner } from "@/lib/types";

// One glyph per finding family, so the list scans without reading every line.
const ICONS: Record<string, string> = {
  repeated_lockups: "🔒",
  repeated_wheelspin: "🌀",
  repeated_bottoming: "⚠️",
  braking_early: "🛑",
  braking_late: "🛑",
  corner_time_loss: "⏱",
};

export function CoachingPanel({
  notes,
  selected,
  lapColors,
  corners,
  onZoom,
}: {
  notes: CoachingLapNotes[];
  selected: number[];
  lapColors: Record<string, string>;
  corners: Corner[];
  onZoom?: (range: [number, number]) => void;
}) {
  if (notes.length === 0) return null;
  // Newest lap first — the engineer's most recent observation is the one a
  // driver stepping out of the car wants on top.
  const ordered = [...notes].sort((a, b) => b.number - a.number);

  return (
    <div className="space-y-2 p-3">
      {ordered.map((lap) => {
        const inView = selected.includes(lap.lap_id);
        return (
          <div key={lap.lap_id} className={inView ? "" : "opacity-50"}>
            <div className="mb-0.5 flex items-center gap-1.5 font-tabular text-[10px] uppercase text-ink-dim">
              {inView && (
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ backgroundColor: lapColors[String(lap.lap_id)] }}
                />
              )}
              Lap {lap.number}
            </div>
            <ul className="space-y-1">
              {lap.findings.map((f, i) => {
                const corner =
                  f.corner != null ? corners.find((c) => c.n === f.corner) : undefined;
                const zoomable = corner && onZoom;
                return (
                  <li
                    key={`${f.type}-${i}`}
                    onClick={zoomable ? () => onZoom(cornerRange(corner)) : undefined}
                    title={zoomable ? "Zoom the charts and map to this corner" : undefined}
                    className={`rounded-md border border-edge/60 px-2 py-1 text-xs leading-snug ${
                      zoomable ? "cursor-pointer hover:border-edge-bright hover:bg-panel-2" : ""
                    }`}
                  >
                    <span className="mr-1.5">{ICONS[f.type] ?? "💬"}</span>
                    {f.text}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <div className="text-[10px] text-ink-dim">
        Replayed from this session's laps — the same findings the voice engineer
        makes, whether or not voice was on.
      </div>
    </div>
  );
}
