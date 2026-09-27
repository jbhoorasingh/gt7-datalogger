import { describe, expect, it } from "vitest";
import { parseHash } from "./router";

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
