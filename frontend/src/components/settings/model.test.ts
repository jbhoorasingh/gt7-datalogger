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
