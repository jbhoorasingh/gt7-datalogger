// Driver dashboard route: /dash (or #/dash) renders full-screen without the
// app chrome. ?layout=<name-or-id> loads a server layout, ?preset=<key> one of
// the built-ins, ?demo=1 forces placeholder data.

import type { LayoutConfig } from "./layout";

export interface DashParams {
  layout: string | null;
  preset: string | null;
  demo: boolean;
}

export function isDashLocation(loc: { pathname: string; hash: string }): boolean {
  return (
    loc.pathname === "/dash" ||
    loc.hash === "#/dash" ||
    loc.hash.startsWith("#/dash?")
  );
}

export function parseDashParams(loc: { search: string; hash: string }): DashParams {
  const query = loc.hash.includes("?")
    ? loc.hash.slice(loc.hash.indexOf("?") + 1)
    : loc.search.replace(/^\?/, "");
  const params = new URLSearchParams(query);
  const demo = params.get("demo");
  return {
    layout: params.get("layout"),
    preset: params.get("preset"),
    demo: demo === "1" || demo === "true",
  };
}

/**
 * The dash shows alerts in its own full-width banner, so a layout's alerts
 * row would say the same thing twice. Drop an `alerts` cell that spans the
 * whole top row and move everything else up into the freed row. Layouts that
 * place alerts anywhere else are left alone — that was a deliberate choice.
 */
export function withoutAlertsRow(layout: LayoutConfig): LayoutConfig {
  const { cols, rows } = layout.grid;
  const banner = layout.cells.find(
    (c) => c.widget === "alerts" && c.y === 0 && c.h === 1 && c.x === 0 && c.w === cols,
  );
  if (!banner || rows < 2) return layout;
  return {
    ...layout,
    grid: { ...layout.grid, rows: rows - 1 },
    cells: layout.cells.filter((c) => c !== banner).map((c) => ({ ...c, y: c.y - 1 })),
  };
}

/** The /dash query string for a layout choice, for history.replaceState. */
export function dashSearch(choice: { layout?: string; preset?: string }, demo: boolean): string {
  const params = new URLSearchParams();
  if (choice.layout) params.set("layout", choice.layout);
  else if (choice.preset) params.set("preset", choice.preset);
  if (demo) params.set("demo", "1");
  const q = params.toString();
  return q ? `?${q}` : "";
}
