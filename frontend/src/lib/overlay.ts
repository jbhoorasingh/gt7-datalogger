// Overlay configuration: which widgets, which layout, and appearance —
// all encoded in the URL hash so OBS, a phone, and a second monitor can each
// load their own setup, e.g. /#overlay?w=gear,speed&layout=grid&scale=1.25

export const WIDGET_IDS = [
  "gear",
  "speed",
  "rpm",
  "inputs",
  "steering",
  "times",
  "delta",
  "position",
  "tires",
  "fuel",
  "strategy",
  "clock",
  "engine",
  "aids",
  "boost",
  "alerts",
] as const;

export type WidgetId = (typeof WIDGET_IDS)[number];

export const WIDGET_LABELS: Record<WidgetId, string> = {
  gear: "Gear",
  speed: "Speed",
  rpm: "RPM",
  inputs: "Throttle / brake",
  steering: "Steering wheel",
  times: "Lap times",
  delta: "Delta (big)",
  position: "Race position",
  tires: "Tire temps",
  fuel: "Fuel",
  strategy: "Fuel strategy",
  clock: "In-game clock",
  engine: "Engine temps",
  aids: "Driver aids",
  boost: "Boost",
  alerts: "Race alerts",
};

export type OverlayLayout = "strip" | "stack" | "grid";
export type OverlayAlign = "bottom" | "center" | "top";
// Page behind the widgets: transparent (OBS browser sources), green for
// chroma keying (webviews without alpha support), or solid dark (phones).
export type OverlayPage = "transparent" | "green" | "dark";

export interface OverlaySize {
  width: number;
  height: number;
}

export interface OverlayConfig {
  widgets: WidgetId[];
  layout: OverlayLayout;
  scale: number; // 0.5 .. 2, global
  widgetScales: Partial<Record<WidgetId, number>>; // per-widget, default 1
  bg: number; // 0 .. 100, card background opacity
  align: OverlayAlign;
  demo: boolean; // animated placeholder data while no telemetry is flowing
  page: OverlayPage;
  // Explicit canvas size; null fills the viewport (legacy behavior). With a
  // size set, the overlay renders at exactly width x height so it matches the
  // OBS browser-source dimensions and the builder preview.
  size: OverlaySize | null;
  padX: number; // px inset from the canvas edges
  padY: number;
}

export const DEFAULT_PAD = 16;

// Common stream canvas sizes; "custom" comes from free W x H inputs.
export const SIZE_PRESETS: { label: string; size: OverlaySize }[] = [
  { label: "full HD", size: { width: 1920, height: 1080 } },
  { label: "bottom strip", size: { width: 1920, height: 260 } },
  { label: "TikTok / Shorts", size: { width: 1080, height: 1920 } },
  { label: "vertical 720p", size: { width: 720, height: 1280 } },
];

export const DEFAULT_CONFIG: OverlayConfig = {
  widgets: ["gear", "speed", "rpm", "inputs", "times", "tires", "fuel"],
  layout: "strip",
  scale: 1,
  widgetScales: {},
  bg: 70,
  align: "bottom",
  demo: false,
  page: "transparent",
  size: { width: 1920, height: 260 },
  padX: DEFAULT_PAD,
  padY: DEFAULT_PAD,
};

export const PHONE_PRESET: OverlayConfig = {
  widgets: ["speed", "gear", "times", "delta", "position", "tires", "fuel", "strategy", "clock"],
  layout: "grid",
  scale: 1,
  widgetScales: {},
  bg: 100,
  align: "top",
  demo: false,
  page: "dark",
  size: null, // phones vary — fill the screen
  padX: DEFAULT_PAD,
  padY: DEFAULT_PAD,
};

// Preferred URL form is the plain path /overlay?w=… — hash-fragment URLs
// (/#overlay?…) still work but are rejected by some apps' URL validators
// (e.g. TikTok LIVE Studio web sources). #/overlay is accepted too so the
// hash form matches its siblings (#/dash, #/engineer).
export function isOverlayLocation(loc: {
  pathname: string;
  hash: string;
}): boolean {
  return (
    loc.pathname === "/overlay" ||
    /^#\/?overlay(\?|$)/.test(loc.hash)
  );
}

// "1920x260" -> {width: 1920, height: 260}; null on anything malformed.
function parseSize(raw: string | null): OverlaySize | null {
  if (!raw) return null;
  const m = /^(\d{2,5})x(\d{2,5})$/.exec(raw);
  if (!m) return null;
  return { width: Number(m[1]), height: Number(m[2]) };
}

function clampWidgetScale(n: number): number | null {
  return isFinite(n) && n >= 0.5 && n <= 3 ? n : null;
}

// An overlay URL either carries the whole config in its params (legacy) or
// references a server-saved layout by name/id: /overlay?layout=race-strip.
// The bare word after "layout=" is a server ref unless it's one of the three
// legacy flow-layout keywords, which legacy URLs used for the same param.
export type OverlayRoute =
  | { kind: "legacy"; config: OverlayConfig }
  | { kind: "server"; ref: string; demo: boolean };

const LEGACY_LAYOUTS = ["strip", "stack", "grid"];

function overlayQuery(loc: { search: string; hash: string }): URLSearchParams {
  const query = loc.hash.includes("?")
    ? loc.hash.slice(loc.hash.indexOf("?") + 1)
    : loc.search.replace(/^\?/, "");
  return new URLSearchParams(query);
}

export function parseOverlayRoute(loc: { search: string; hash: string }): OverlayRoute {
  const params = overlayQuery(loc);
  const ref = params.get("layout");
  if (ref && !params.has("w") && !LEGACY_LAYOUTS.includes(ref)) {
    const demo = params.get("demo");
    return { kind: "server", ref, demo: demo === "1" || demo === "true" };
  }
  return { kind: "legacy", config: parseOverlayLocation(loc) };
}

export function parseOverlayLocation(loc: { search: string; hash: string }): OverlayConfig {
  const params = overlayQuery(loc);
  // Widget entries are "id" or "id:scale" (e.g. gear:1.5).
  const ids: WidgetId[] = [];
  const widgetScales: Partial<Record<WidgetId, number>> = {};
  for (const entry of (params.get("w") ?? "").split(",")) {
    const [id, rawScale] = entry.split(":", 2);
    if (!(WIDGET_IDS as readonly string[]).includes(id)) continue;
    ids.push(id as WidgetId);
    const s = rawScale != null ? clampWidgetScale(Number(rawScale)) : null;
    if (s != null && s !== 1) widgetScales[id as WidgetId] = s;
  }
  const layout = params.get("layout");
  const align = params.get("align");
  const scale = Number(params.get("scale"));
  const bg = Number(params.get("bg"));
  const demo = params.get("demo");
  const page = params.get("page");
  const pad = /^(\d{1,3})x(\d{1,3})$/.exec(params.get("pad") ?? "");
  const resolvedLayout = layout === "stack" || layout === "grid" ? layout : "strip";
  return {
    widgets: ids.length > 0 ? ids : DEFAULT_CONFIG.widgets,
    layout: resolvedLayout,
    scale: isFinite(scale) && scale >= 0.5 && scale <= 2 ? scale : 1,
    widgetScales,
    bg: isFinite(bg) && bg >= 0 && bg <= 100 && params.has("bg") ? bg : DEFAULT_CONFIG.bg,
    align: align === "center" || align === "top" ? align : "bottom",
    demo: demo === "1" || demo === "true",
    page:
      page === "green" || page === "dark" || page === "transparent"
        ? page
        : resolvedLayout === "grid"
          ? "dark"
          : "transparent",
    size: parseSize(params.get("size")),
    padX: pad ? Number(pad[1]) : DEFAULT_PAD,
    padY: pad ? Number(pad[2]) : DEFAULT_PAD,
  };
}

export function buildOverlayUrl(
  config: OverlayConfig,
  origin: string = window.location.origin,
): string {
  const params = new URLSearchParams();
  params.set(
    "w",
    config.widgets
      .map((id) => {
        const s = config.widgetScales[id];
        return s != null && s !== 1 ? `${id}:${s}` : id;
      })
      .join(","),
  );
  params.set("layout", config.layout);
  if (config.scale !== 1) params.set("scale", String(config.scale));
  params.set("bg", String(config.bg));
  if (config.align !== "bottom") params.set("align", config.align);
  if (config.demo) params.set("demo", "1");
  const defaultPage = config.layout === "grid" ? "dark" : "transparent";
  if (config.page !== defaultPage) params.set("page", config.page);
  if (config.size) params.set("size", `${config.size.width}x${config.size.height}`);
  if (config.padX !== DEFAULT_PAD || config.padY !== DEFAULT_PAD) {
    params.set("pad", `${config.padX}x${config.padY}`);
  }
  return `${origin}/overlay?${params.toString()}`;
}
