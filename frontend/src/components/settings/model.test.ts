import { describe, expect, it } from "vitest";
import type { AdminSettings } from "@/lib/types";
import { filterSections, mergeEdits, pendingKeys, pendingPatch, resolveSection } from "./model";

const saved = {
  ps_ip: "10.0.0.2",
  source: "udp",
  packet_format: "C",
  webhook_url: "",
  webhook_events: ["personal_best", "session_summary"],
  race_engineer_categories: ["fuel", "lap"],
} as unknown as AdminSettings;

describe("pending edits", () => {
  it("drops an edit once it is back to the saved value", () => {
    let e = mergeEdits(saved, {}, { ps_ip: "10.0.0.9" });
    expect(pendingKeys(saved, e)).toEqual(["ps_ip"]);
    e = mergeEdits(saved, e, { ps_ip: "10.0.0.2" });
    expect(e).toEqual({});
  });

  it("compares lists as sets", () => {
    const e = mergeEdits(saved, {}, { race_engineer_categories: ["lap", "fuel"] });
    expect(pendingKeys(saved, e)).toEqual([]);
  });

  it("sends only the diff", () => {
    const e = mergeEdits(saved, {}, { source: "sim", packet_format: "C", webhook_url: "https://x" });
    expect(pendingPatch(saved, e)).toEqual({ source: "sim", webhook_url: "https://x" });
  });
});

describe("sector voice settings", () => {
  const sectorDefaults: AdminSettings = {
    ...saved,
    race_engineer_sector_voice_enabled: false,
    race_engineer_sector_voice_decimals: 3,
    race_engineer_sector_voice_rounding: "round",
    race_engineer_sector_voice_content: "delta",
    race_engineer_sector_voice_comparison: "individual",
  };

  it("tracks and sends changes to all five sector voice settings", () => {
    const changes = {
      race_engineer_sector_voice_enabled: true,
      race_engineer_sector_voice_decimals: 2,
      race_engineer_sector_voice_rounding: "truncate",
      race_engineer_sector_voice_content: "both",
      race_engineer_sector_voice_comparison: "cumulative",
    } as const;

    const edits = mergeEdits(sectorDefaults, {}, changes);

    expect(pendingKeys(sectorDefaults, edits)).toHaveLength(5);
    expect(pendingKeys(sectorDefaults, edits)).toEqual(
      expect.arrayContaining(Object.keys(changes)),
    );
    expect(pendingPatch(sectorDefaults, edits)).toEqual(changes);
  });

  it("excludes unchanged settings and removes reverted edits", () => {
    const edits = mergeEdits(sectorDefaults, {}, {
      race_engineer_sector_voice_enabled: true,
      race_engineer_sector_voice_decimals: 3,
    });

    expect(pendingPatch(sectorDefaults, edits)).toEqual({
      race_engineer_sector_voice_enabled: true,
    });

    const reverted = mergeEdits(sectorDefaults, edits, {
      race_engineer_sector_voice_enabled: false,
    });

    expect(pendingKeys(sectorDefaults, reverted)).toEqual([]);
    expect(pendingPatch(sectorDefaults, reverted)).toEqual({});
  });
});

describe("rail", () => {
  it("filters by label and keywords", () => {
    expect(filterSections("token").flatMap((g) => g.items.map((i) => i.id))).toEqual(["sync", "access"]);
    expect(filterSections("webhook").flatMap((g) => g.items.map((i) => i.id))).toEqual(["notifications"]);
    expect(filterSections("zzz")).toEqual([]);
  });

  it("resolves aliases and falls back to connection", () => {
    expect(resolveSection(undefined)).toBe("connection");
    expect(resolveSection("engineer")).toBe("race-engineer");
    expect(resolveSection("data")).toBe("data");
    expect(resolveSection("nope")).toBe("connection");
  });
});
