// Renders a v2 LayoutConfig: a CSS grid with widgets spanning their cells.
// Used by the server-layout overlay path, the driver dashboard, and (in a
// scaled-down form) the builder canvas. Empty cells render nothing so the
// page stays transparent for OBS.

import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { LayoutCell, LayoutConfig } from "@/lib/layout";
import type { LapSummary, LiveFrame } from "@/lib/types";
import { WIDGET_META, widgetBaseW } from "@/lib/widgetMeta";
import { WIDGET_COMPONENTS } from "@/lib/widgetRegistry";

// Content height (px) a widget was designed for; a cell span this tall shows
// the widget at 1:1, taller/shorter spans zoom it proportionally. The zoom is
// also capped so the variant's design width (baseW) fits the cell — otherwise
// text-heavy widgets wrap and clip in tall narrow cells.
const BASE_CONTENT_H = 84;
export const CARD_PAD = 12;

export function widgetZoom(
  spanW: number,
  spanH: number,
  pad: number,
  scale: number,
  baseW: number,
): number {
  const fit = Math.min((spanH - pad * 2) / BASE_CONTENT_H, (spanW - pad * 2) / baseW);
  return Math.min(8, Math.max(0.35, fit * scale));
}

export function GridRenderer({
  layout,
  frame,
  laps,
}: {
  layout: LayoutConfig;
  frame: LiveFrame;
  laps: LapSummary[];
}) {
  const container = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(() =>
      setBox({ w: el.clientWidth, h: el.clientHeight }),
    );
    observer.observe(el);
    setBox({ w: el.clientWidth, h: el.clientHeight });
    return () => observer.disconnect();
  }, []);

  const { grid } = layout;
  const cellW = box.w > 0 ? (box.w - grid.gap * (grid.cols - 1)) / grid.cols : 0;
  const cellH = box.h > 0 ? (box.h - grid.gap * (grid.rows - 1)) / grid.rows : 0;

  const bare = layout.bg === 0;
  // Translucent cards bleed the chroma-key green through and key out badly.
  const cardAlpha = layout.page === "green" ? 1 : layout.bg / 100;
  const card: CSSProperties = bare
    ? {}
    : {
        backgroundColor: `rgba(8, 10, 14, ${cardAlpha})`,
        // Hairline ring rather than a border: on a transparent OBS page the
        // ring rides the alpha with the fill instead of drawing a hard line.
        boxShadow: `0 0 0 1px rgba(38, 43, 51, ${cardAlpha})`,
      };

  const outerStyle: CSSProperties = {
    padding: `${layout.padY}px ${layout.padX}px`,
    ...(layout.size
      ? { width: layout.size.width, height: layout.size.height, overflow: "hidden" }
      : {}),
  };

  return (
    <div className={layout.size ? "" : "h-full w-full"} style={outerStyle}>
      <div
        ref={container}
        className="grid h-full w-full font-tabular"
        style={{
          gridTemplateColumns: `repeat(${grid.cols}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${grid.rows}, minmax(0, 1fr))`,
          gap: grid.gap,
        }}
      >
        {cellW > 0 &&
          cellH > 0 &&
          layout.cells.map((cell) => (
            <GridCell
              key={cell.id}
              cell={cell}
              frame={frame}
              laps={laps}
              cellW={cellW}
              cellH={cellH}
              gap={grid.gap}
              bare={bare}
              card={card}
            />
          ))}
      </div>
    </div>
  );
}

function GridCell({
  cell,
  frame,
  laps,
  cellW,
  cellH,
  gap,
  bare,
  card,
}: {
  cell: LayoutCell;
  frame: LiveFrame;
  laps: LapSummary[];
  cellW: number;
  cellH: number;
  gap: number;
  bare: boolean;
  card: CSSProperties;
}) {
  const Comp = WIDGET_COMPONENTS[cell.widget];
  const frameless = bare || WIDGET_META[cell.widget].frameless === true;
  const pad = frameless ? 0 : CARD_PAD;
  const spanW = cell.w * cellW + (cell.w - 1) * gap;
  const spanH = cell.h * cellH + (cell.h - 1) * gap;
  const scale =
    typeof cell.options?.scale === "number" && isFinite(cell.options.scale)
      ? cell.options.scale
      : 1;
  const zoom = widgetZoom(spanW, spanH, pad, scale, widgetBaseW(cell.widget, cell.variant));
  const innerW = Math.max(24, spanW - pad * 2);

  return (
    <div
      className={`flex items-center justify-center overflow-hidden ${
        frameless ? "" : "rounded-panel px-4 py-4"
      }`}
      style={{
        gridColumn: `${cell.x + 1} / span ${cell.w}`,
        gridRow: `${cell.y + 1} / span ${cell.h}`,
        ...(frameless ? {} : card),
      }}
    >
      <div
        className="flex items-center justify-center"
        style={{ zoom, width: innerW / zoom }}
      >
        <Comp
          frame={frame}
          laps={laps}
          variant={cell.variant}
          w={cell.w}
          h={cell.h}
          options={cell.options ?? {}}
        />
      </div>
    </div>
  );
}
