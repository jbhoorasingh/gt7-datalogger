// Editable twin of GridRenderer for the layout builder: the layout at true
// canvas size, CSS-scaled to fit, with hand-rolled pointer-event dragging.
// Drag a widget to move it (snapped ghost, green = valid / red = collision),
// drag the corner handle to step through its allowed footprints. The selected
// cell gets an accent ring, the resize handle and a "{Widget} · w×h" tag,
// all sized in screen pixels so they read the same at any zoom.

import { memo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { CARD_PAD, widgetZoom } from "@/components/GridRenderer";
import type { LayoutCell, LayoutConfig } from "@/lib/layout";
import type { LapSummary, LiveFrame } from "@/lib/types";
import { WIDGET_META, widgetBaseW } from "@/lib/widgetMeta";
import { WIDGET_COMPONENTS } from "@/lib/widgetRegistry";

// Canvas dimensions used when the layout fills the viewport (size: null).
const FILL_PREVIEW = { width: 1280, height: 720 };
const MAX_CANVAS_HEIGHT = 520;

interface DragState {
  mode: "move" | "resize";
  cellId: string;
  offX: number; // pointer offset inside the cell, true-canvas px (move)
  offY: number;
  x: number; // proposed footprint
  y: number;
  w: number;
  h: number;
  valid: boolean;
  moved: boolean;
}

function overlapsAny(
  cells: LayoutCell[],
  skipId: string,
  x: number,
  y: number,
  w: number,
  h: number,
): boolean {
  return cells.some(
    (c) =>
      c.id !== skipId && c.x < x + w && x < c.x + c.w && c.y < y + h && y < c.y + c.h,
  );
}

export function GridCanvas({
  layout,
  frame,
  laps,
  selected,
  onSelect,
  onCellsChange,
  footer,
}: {
  layout: LayoutConfig;
  frame: LiveFrame | null;
  laps: LapSummary[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  onCellsChange: (cells: LayoutCell[]) => void;
  // Rendered under the canvas once its scale is known (the caption).
  footer?: (scale: number) => ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  // The ref is authoritative: pointermove + pointerup can fire faster than a
  // React re-render, so handlers must not read drag state from the closure.
  const dragRef = useRef<DragState | null>(null);
  const [drag, setDragState] = useState<DragState | null>(null);
  const setDrag = (d: DragState | null) => {
    dragRef.current = d;
    setDragState(d);
  };

  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setContainerWidth(el.clientWidth));
    observer.observe(el);
    setContainerWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  const trueSize = layout.size ?? FILL_PREVIEW;
  const scale =
    containerWidth > 0
      ? Math.min(containerWidth / trueSize.width, MAX_CANVAS_HEIGHT / trueSize.height, 1)
      : 0;

  const { grid } = layout;
  const innerW = trueSize.width - 2 * layout.padX;
  const innerH = trueSize.height - 2 * layout.padY;
  const cellW = (innerW - grid.gap * (grid.cols - 1)) / grid.cols;
  const cellH = (innerH - grid.gap * (grid.rows - 1)) / grid.rows;
  const left = (x: number) => layout.padX + x * (cellW + grid.gap);
  const top = (y: number) => layout.padY + y * (cellH + grid.gap);
  const spanW = (w: number) => w * cellW + (w - 1) * grid.gap;
  const spanH = (h: number) => h * cellH + (h - 1) * grid.gap;

  // Pointer position in true-canvas px.
  function truePoint(e: React.PointerEvent): { px: number; py: number } {
    const rect = container.current!.getBoundingClientRect();
    return { px: (e.clientX - rect.left) / scale, py: (e.clientY - rect.top) / scale };
  }

  function startMove(e: React.PointerEvent, cell: LayoutCell) {
    e.currentTarget.setPointerCapture(e.pointerId);
    const { px, py } = truePoint(e);
    setDrag({
      mode: "move",
      cellId: cell.id,
      offX: px - left(cell.x),
      offY: py - top(cell.y),
      x: cell.x,
      y: cell.y,
      w: cell.w,
      h: cell.h,
      valid: true,
      moved: false,
    });
    onSelect(cell.id);
  }

  function startResize(e: React.PointerEvent, cell: LayoutCell) {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({
      mode: "resize",
      cellId: cell.id,
      offX: 0,
      offY: 0,
      x: cell.x,
      y: cell.y,
      w: cell.w,
      h: cell.h,
      valid: true,
      moved: false,
    });
    onSelect(cell.id);
  }

  function onPointerMove(e: React.PointerEvent) {
    const drag = dragRef.current;
    if (!drag) return;
    const cell = layout.cells.find((c) => c.id === drag.cellId);
    if (!cell) return;
    const { px, py } = truePoint(e);

    if (drag.mode === "move") {
      const x = Math.min(
        grid.cols - cell.w,
        Math.max(0, Math.round((px - drag.offX - layout.padX) / (cellW + grid.gap))),
      );
      const y = Math.min(
        grid.rows - cell.h,
        Math.max(0, Math.round((py - drag.offY - layout.padY) / (cellH + grid.gap))),
      );
      if (x === drag.x && y === drag.y && drag.moved) return;
      setDrag({
        ...drag,
        x,
        y,
        valid: !overlapsAny(layout.cells, cell.id, x, y, cell.w, cell.h),
        moved: drag.moved || x !== cell.x || y !== cell.y,
      });
      return;
    }

    // Resize: raw target span from the pointer, snapped to the nearest
    // allowed footprint that stays inside the grid.
    const rawW = Math.max(1, Math.round((px - left(cell.x) + grid.gap / 2) / (cellW + grid.gap)));
    const rawH = Math.max(1, Math.round((py - top(cell.y) + grid.gap / 2) / (cellH + grid.gap)));
    const candidates = WIDGET_META[cell.widget].sizes.filter(
      ([w, h]) => cell.x + w <= grid.cols && cell.y + h <= grid.rows,
    );
    if (candidates.length === 0) return;
    const [w, h] = candidates.reduce((best, s) => {
      const d = (s[0] - rawW) ** 2 + (s[1] - rawH) ** 2;
      const bd = (best[0] - rawW) ** 2 + (best[1] - rawH) ** 2;
      return d < bd ? s : best;
    });
    if (w === drag.w && h === drag.h && drag.moved) return;
    setDrag({
      ...drag,
      w,
      h,
      valid: !overlapsAny(layout.cells, cell.id, cell.x, cell.y, w, h),
      moved: drag.moved || w !== cell.w || h !== cell.h,
    });
  }

  function onPointerUp() {
    const drag = dragRef.current;
    if (!drag) return;
    if (drag.moved && drag.valid) {
      onCellsChange(
        layout.cells.map((c) =>
          c.id === drag.cellId
            ? drag.mode === "move"
              ? { ...c, x: drag.x, y: drag.y }
              : { ...c, w: drag.w, h: drag.h }
            : c,
        ),
      );
    }
    setDrag(null);
  }

  const bare = layout.bg === 0;
  const cardAlpha = layout.page === "green" ? 1 : layout.bg / 100;
  const card: CSSProperties = bare
    ? {}
    : { backgroundColor: `rgba(8, 10, 14, ${cardAlpha})` };
  // Painted on the unscaled frame so the transparency checkerboard keeps a
  // 20px screen size at any zoom.
  const pageBg =
    layout.page === "green"
      ? "#00ff00"
      : layout.page === "dark"
        ? "var(--color-surface)"
        : "repeating-conic-gradient(#101317 0% 25%, #0c0f12 0% 50%) 0 0 / 20px 20px";
  // One screen pixel in true-canvas px, for chrome drawn inside the scaled layer.
  const px = scale > 0 ? 1 / scale : 1;
  const selectedCell = layout.cells.find((c) => c.id === selected) ?? null;
  const dragMoved = drag?.moved === true;

  return (
    <div ref={container} className="w-full">
      {scale > 0 && (
        <div
          className="relative mx-auto overflow-hidden rounded-md shadow-[0_0_0_1px_var(--color-edge)]"
          style={{
            width: trueSize.width * scale,
            height: trueSize.height * scale,
            background: pageBg,
          }}
          onPointerDown={(e) => {
            if (e.target === e.currentTarget) onSelect(null);
          }}
        >
          <div
            className="absolute left-0 top-0 font-tabular"
            style={{
              width: trueSize.width,
              height: trueSize.height,
              transform: `scale(${scale})`,
              transformOrigin: "0 0",
              ["--px" as string]: `${px}px`,
            }}
            onPointerDown={(e) => {
              if (e.target === e.currentTarget) onSelect(null);
            }}
          >
            <GridLines
              cols={grid.cols}
              rows={grid.rows}
              cellW={cellW}
              cellH={cellH}
              gap={grid.gap}
              padX={layout.padX}
              padY={layout.padY}
            />

            {layout.cells.map((cell) => {
              const isSelected = cell.id === selected;
              const dragging = drag?.cellId === cell.id && drag.moved;
              const frameless = bare || WIDGET_META[cell.widget].frameless === true;
              const pad = frameless ? 0 : CARD_PAD;
              const cScale =
                typeof cell.options?.scale === "number" && isFinite(cell.options.scale)
                  ? cell.options.scale
                  : 1;
              const w = spanW(cell.w);
              const h = spanH(cell.h);
              const zoom = widgetZoom(w, h, pad, cScale, widgetBaseW(cell.widget, cell.variant));
              const Comp = WIDGET_COMPONENTS[cell.widget];
              return (
                <div
                  key={cell.id}
                  className={`absolute flex cursor-grab touch-none items-center justify-center overflow-hidden active:cursor-grabbing ${
                    frameless ? "" : "rounded-xl border border-edge p-3"
                  } ${
                    isSelected ? "" : "hover:shadow-[0_0_0_calc(2*var(--px))_var(--color-edge-bright)]"
                  } ${dragging ? "opacity-40" : ""}`}
                  style={{
                    left: left(cell.x),
                    top: top(cell.y),
                    width: w,
                    height: h,
                    ...(frameless ? {} : card),
                  }}
                  onPointerDown={(e) => startMove(e, cell)}
                  onPointerMove={onPointerMove}
                  onPointerUp={onPointerUp}
                >
                  <div
                    className="pointer-events-none flex items-center justify-center"
                    style={{ zoom, width: Math.max(24, w - pad * 2) / zoom }}
                  >
                    {frame ? (
                      <Comp
                        frame={frame}
                        laps={laps}
                        variant={cell.variant}
                        w={cell.w}
                        h={cell.h}
                        options={cell.options ?? {}}
                      />
                    ) : (
                      <span className="text-xs text-ink-dim">{WIDGET_META[cell.widget].label}</span>
                    )}
                  </div>
                  {/* empty frameless widgets (e.g. alerts with nothing firing)
                      would be invisible and undraggable — give them a label */}
                  {frameless && (
                    <span className="pointer-events-none absolute left-1 top-1 rounded bg-black/50 px-1 text-[8px] uppercase tracking-wider text-ink-dim">
                      {WIDGET_META[cell.widget].label}
                    </span>
                  )}
                </div>
              );
            })}

            {/* selection: ring, tag and resize handle. The handle stays mounted
                through a resize drag — it holds the pointer capture. */}
            {selectedCell && (
              <div
                className="pointer-events-none absolute rounded-xl"
                style={{
                  left: left(selectedCell.x),
                  top: top(selectedCell.y),
                  width: spanW(selectedCell.w),
                  height: spanH(selectedCell.h),
                  boxShadow: dragMoved ? "none" : `0 0 0 ${3 * px}px var(--color-accent)`,
                }}
              >
                {!dragMoved && (
                  <span
                    className="absolute left-0 whitespace-nowrap rounded bg-accent-900 font-tabular text-accent-200"
                    style={{
                      // Above the cell, or inside it on the top row where
                      // "above" is off the canvas.
                      top: selectedCell.y === 0 ? 6 * px : -26 * px,
                      fontSize: 11 * px,
                      padding: `${2 * px}px ${8 * px}px`,
                    }}
                  >
                    {WIDGET_META[selectedCell.widget].label} · {selectedCell.w}×{selectedCell.h}
                  </span>
                )}
                <div
                  className="pointer-events-auto absolute cursor-nwse-resize touch-none border-accent"
                  title="Drag to resize"
                  style={{
                    right: -3 * px,
                    bottom: -3 * px,
                    width: 16 * px,
                    height: 16 * px,
                    borderRightWidth: 3 * px,
                    borderBottomWidth: 3 * px,
                    borderBottomRightRadius: 12,
                  }}
                  onPointerDown={(e) => startResize(e, selectedCell)}
                  onPointerMove={onPointerMove}
                  onPointerUp={onPointerUp}
                />
              </div>
            )}

            {/* snapped ghost while dragging */}
            {drag && drag.moved && (
              <div
                className={`pointer-events-none absolute rounded-xl border-2 ${
                  drag.valid ? "border-throttle bg-throttle/10" : "border-brake bg-brake/10"
                }`}
                style={{
                  left: left(drag.x),
                  top: top(drag.y),
                  width: spanW(drag.w),
                  height: spanH(drag.h),
                }}
              />
            )}
          </div>
        </div>
      )}
      {scale > 0 && footer?.(scale)}
    </div>
  );
}

// Static cell outlines; memoized so 30 Hz telemetry re-renders skip them.
const GridLines = memo(function GridLines({
  cols,
  rows,
  cellW,
  cellH,
  gap,
  padX,
  padY,
}: {
  cols: number;
  rows: number;
  cellW: number;
  cellH: number;
  gap: number;
  padX: number;
  padY: number;
}) {
  const cells = [];
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      cells.push(
        <div
          key={`${x}-${y}`}
          className="absolute rounded border border-white/5"
          style={{
            left: padX + x * (cellW + gap),
            top: padY + y * (cellH + gap),
            width: cellW,
            height: cellH,
          }}
        />,
      );
    }
  }
  return <>{cells}</>;
});
