import { describe, expect, it } from "vitest";
import { analysisParams, parseAnalysisParams, parseHash } from "./router";

describe("parseHash", () => {
  it("lands the bare origin on Live", () => {
    for (const hash of ["", "#", "#/", "#/?x=1"]) {
      expect(parseHash(hash).view).toBe("live");
    }
  });

  it("parses every named view, with or without a trailing slash", () => {
    expect(parseHash("#/analysis?session=3").view).toBe("analysis");
    expect(parseHash("#/analysis?session=3").params.get("session")).toBe("3");
    expect(parseHash("#/sessions/").view).toBe("sessions");
    expect(parseHash("#/overlays").view).toBe("overlays");
  });

  it("keeps the old aliases working", () => {
    const bests = parseHash("#/bests");
    expect(bests.view).toBe("sessions");
    expect(bests.params.get("sub")).toBe("bests");
    const admin = parseHash("#/admin/sync");
    expect(admin.view).toBe("settings");
    expect(admin.params.get("section")).toBe("sync");
  });

  it("marks an unknown path as not found instead of falling back to Live", () => {
    expect(parseHash("#/nowhere").view).toBe("notfound");
    expect(parseHash("#/analysis/412").view).toBe("notfound");
  });
});

describe("Analysis links", () => {
  const parse = (query: string) => parseAnalysisParams(new URLSearchParams(query));

  it("read the race line's lens", () => {
    expect(parse("session=3&laps=12,15&ref=15&lens=tire_temp").lens).toBe("tire_temp");
    expect(parse("session=3&lens=tire_balance").lens).toBe("tire_balance");
  });

  it("ignore a lens this build does not know, or none", () => {
    expect(parse("session=3&lens=tyres").lens).toBeUndefined();
    expect(parse("session=3&lens=").lens).toBeUndefined();
    expect(parse("session=3").lens).toBeUndefined();
  });

  it("leave Inputs unsaid, like the default channel set", () => {
    expect(analysisParams({ session: 3, lens: "inputs" })).toEqual({ session: "3" });
    expect(analysisParams({ session: 3 })).toEqual({ session: "3" });
    expect(analysisParams({ session: 3, lens: "speed" })).toEqual({ session: "3", lens: "speed" });
  });

  it("read back what they write", () => {
    const req = {
      session: 3,
      laps: [12, 208],
      ref: 15,
      channels: ["speed", "brake"],
      lens: "tire_balance" as const,
    };
    expect(parse(new URLSearchParams(analysisParams(req)).toString())).toEqual(req);
  });
});
