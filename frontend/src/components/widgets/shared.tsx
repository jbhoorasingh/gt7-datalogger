// Small building blocks shared by the overlay/dashboard widgets.

import type { WidgetRenderProps } from "@/lib/widgetMeta";
import type { LiveFrame } from "@/lib/types";

export function Caption({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
      {children}
    </div>
  );
}

// Legacy URLs pass an explicit `big` option (strip widgets are compact); grid
// cells infer it from their footprint.
export function isBig(props: WidgetRenderProps): boolean {
  if (props.options.big != null) return props.options.big === true;
  return props.h >= 2;
}

export function lastVsPrevBest(frame: LiveFrame): number | null {
  return frame.last_lap_ms > 0 && frame.prev_best_ms > 0
    ? frame.last_lap_ms - frame.prev_best_ms
    : null;
}

// Live gap to the session-best lap while driving; before a reference lap
// exists (or past its end) it falls back to the end-of-lap comparison.
export function liveDelta(frame: LiveFrame): { ms: number; live: boolean } | null {
  if (frame.delta_ms != null) return { ms: frame.delta_ms, live: true };
  const last = lastVsPrevBest(frame);
  return last != null ? { ms: last, live: false } : null;
}

export function lapLabel(frame: LiveFrame): string {
  if (frame.total_laps > 0 && frame.current_lap > frame.total_laps) return "FIN";
  return `${frame.current_lap}${frame.total_laps > 0 ? `/${frame.total_laps}` : ""}`;
}

// 240° arc gauge (SVG, no chart library). value is clamped to 0..1.
const GAUGE_R = 40;
const GAUGE_LEN = (Math.PI * GAUGE_R * 240) / 180;

function polar(angleDeg: number): [number, number] {
  const a = (angleDeg * Math.PI) / 180;
  return [50 + GAUGE_R * Math.cos(a), 50 + GAUGE_R * Math.sin(a)];
}

const [GX0, GY0] = polar(150);
const [GX1, GY1] = polar(30);
const GAUGE_PATH = `M ${GX0} ${GY0} A ${GAUGE_R} ${GAUGE_R} 0 1 1 ${GX1} ${GY1}`;

export function Gauge({
  value,
  text,
  caption,
  color = "var(--color-accent)",
}: {
  value: number;
  text: string;
  caption: string;
  color?: string;
}) {
  const frac = Math.min(1, Math.max(0, value));
  return (
    <svg viewBox="0 0 100 82" className="w-24">
      <path d={GAUGE_PATH} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth={7} strokeLinecap="round" />
      <path
        d={GAUGE_PATH}
        fill="none"
        stroke={color}
        strokeWidth={7}
        strokeLinecap="round"
        strokeDasharray={`${frac * GAUGE_LEN} ${GAUGE_LEN}`}
      />
      <text x={50} y={52} textAnchor="middle" fill="currentColor" fontSize={18} fontWeight={700}>
        {text}
      </text>
      <text
        x={50}
        y={76}
        textAnchor="middle"
        fill="var(--color-ink-dim)"
        fontSize={7}
        letterSpacing={2}
        style={{ textTransform: "uppercase" }}
      >
        {caption}
      </text>
    </svg>
  );
}
