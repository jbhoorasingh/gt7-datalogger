// Right-hand illustrations for the error pages. Decorative only (aria-hidden);
// the text column carries everything the page has to say.

// Flag swatches, per marshal flag.
export const FLAGS = {
  chequered: "repeating-conic-gradient(var(--color-ink-soft) 0 25%, #07080a 0 50%) 0 0 / 10px 10px",
  black: "#07080a",
  red: "oklch(0.55 0.2 25)",
  yellow: "var(--color-warn)",
  white: "var(--color-ink-soft)",
} as const;

const CIRCUIT =
  "M70,210 C45,210 32,188 46,166 L118,62 C130,44 152,40 172,50 L258,92 C278,102 298,96 308,80 " +
  "L326,52 C342,28 376,40 370,68 L348,196 C342,222 320,232 294,229 L96,214 C84,213 76,212 70,210 Z";

const GRAVEL: [number, number][] = [
  [392, 18],
  [400, 6],
  [384, 26],
  [408, 20],
  [396, 30],
];

/** 1a: a circuit bed, and an accent line leaving it at a corner into the gravel. */
export function OffTrackArt() {
  return (
    <div aria-hidden className="relative w-full max-w-[620px]">
      <div className="absolute -inset-[10%] bg-[radial-gradient(closest-side,color-mix(in_srgb,var(--color-accent)_8%,transparent),transparent)]" />
      <svg viewBox="0 0 420 280" className="relative w-full overflow-visible">
        <path d={CIRCUIT} fill="none" stroke="var(--color-edge)" strokeWidth={24} strokeLinejoin="round" />
        <path
          d={CIRCUIT}
          fill="none"
          stroke="var(--color-accent)"
          strokeOpacity={0.5}
          strokeWidth={1.4}
          strokeDasharray="5 7"
        />
        <path
          d="M300,84 C316,62 330,40 350,34 C372,28 392,20 404,10"
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth={2.2}
          strokeLinecap="round"
          strokeDasharray="140 560"
          className="animate-offtrack"
          style={{ filter: "drop-shadow(0 0 4px var(--color-accent))" }}
        />
        {GRAVEL.map(([x, y]) => (
          <circle key={`${x},${y}`} cx={x} cy={y} r={1.8} fill="var(--color-ink-ghost)" />
        ))}
        <text x={396} y={46} fill="var(--color-ink-faint)" fontSize={10} textAnchor="middle">
          gravel
        </text>
        <line x1={88} y1={200} x2={84} y2={226} stroke="var(--color-ink-soft)" strokeWidth={2} />
        <text x={98} y={246} fill="var(--color-ink-faint)" fontSize={10}>
          S/F
        </text>
      </svg>
    </div>
  );
}

/** 1b: a black flag waving on its pole, with a "401" disc. */
export function BlackFlagArt() {
  return (
    <div aria-hidden className="flex items-start">
      <div className="h-[380px] w-1 rounded-sm bg-[linear-gradient(var(--color-ink-dim),var(--color-edge))]" />
      <div
        className="animate-flagwave mt-1.5 flex h-[220px] w-[300px] origin-left items-center justify-center rounded-r bg-[linear-gradient(135deg,#07080a,var(--color-panel-2)_55%,#07080a)]"
        style={{ boxShadow: "0 0 0 1px var(--color-hairline), 0 18px 40px rgb(0 0 0 / 0.6)" }}
      >
        <div className="flex h-[110px] w-[110px] items-center justify-center rounded-full bg-ink-soft font-tabular text-[40px] font-semibold text-[#07080a]">
          401
        </div>
      </div>
    </div>
  );
}

/** 1c: the red race-control field — "Return to pits". */
export function RedFlagArt() {
  return (
    <div
      aria-hidden
      className="relative flex h-[calc(100%-80px)] min-h-[320px] w-full flex-col justify-between overflow-hidden rounded-panel p-7 text-[oklch(0.95_0.02_25)]"
      style={{
        background:
          "linear-gradient(160deg, oklch(0.42 0.17 25), oklch(0.3 0.12 25) 70%, oklch(0.24 0.08 25))",
      }}
    >
      <div className="animate-sweep absolute inset-0 bg-[radial-gradient(circle_at_70%_30%,oklch(0.6_0.2_25),transparent_60%)]" />
      <div className="relative flex justify-between text-[11px] uppercase tracking-[0.14em]">
        <span>Race control</span>
        <span className="animate-blink">● Live</span>
      </div>
      <div className="relative flex flex-col gap-1.5">
        <span className="text-[11px] uppercase tracking-[0.14em]">All cars</span>
        <span className="text-[40px] font-medium tracking-[-0.01em]">Return to pits</span>
        <span className="text-[13px]">Session suspended · saved laps are safe</span>
      </div>
    </div>
  );
}
