import { describe, expect, it } from "vitest";
import { dashSearch, parseDashParams, withoutAlertsRow } from "./dash";
import { DASH_PRESETS } from "./dashPresets";
import type { LayoutConfig } from "./layout";

describe("withoutAlertsRow", () => {
  it("drops a preset's alerts banner and shifts the rest up", () => {
    const preset = DASH_PRESETS["race-engineer"].layout;
    const out = withoutAlertsRow(preset);
    expect(out.grid.rows).toBe(preset.grid.rows - 1);
    expect(out.cells.some((c) => c.widget === "alerts")).toBe(false);
    expect(out.cells).toHaveLength(preset.cells.length - 1);
    expect(out.cells.find((c) => c.id === "re-fuel")?.y).toBe(0);
    expect(Math.max(...out.cells.map((c) => c.y + c.h))).toBe(out.grid.rows);
  });

  it("leaves alerts placed elsewhere alone", () => {
    const layout: LayoutConfig = {
      ...DASH_PRESETS.endurance.layout,
      cells: [
        { id: "a", widget: "alerts", variant: "banner", x: 0, y: 2, w: 8, h: 1 },
        { id: "f", widget: "fuel", variant: "laps", x: 0, y: 0, w: 4, h: 2 },
      ],
    };
    expect(withoutAlertsRow(layout)).toBe(layout);
  });
});

describe("dashSearch", () => {
  it("round-trips through parseDashParams", () => {
    const search = dashSearch({ layout: "7" }, true);
    expect(parseDashParams({ search, hash: "" })).toEqual({
      layout: "7",
      preset: null,
      demo: true,
    });
    expect(dashSearch({ preset: "endurance" }, false)).toBe("?preset=endurance");
    expect(dashSearch({}, false)).toBe("");
  });
});
