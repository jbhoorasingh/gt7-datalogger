import { describe, expect, it } from "vitest";
import type { BackupInventory } from "@/lib/types";
import { chosenParts, DEFAULT_PARTS, partDetail, tokensWarning } from "./backup";

const inv: BackupInventory = {
  data: { available: true, bytes: 886067200, sessions: 212, laps: 1 },
  surveys: {
    available: true,
    bytes: 7_340_032,
    bundles: 10,
    corrections: 1,
    logs: 0,
    car_widths: true,
    source_id: true,
  },
  config: { available: true, settings: 1 },
  layouts: { available: false, layouts: 0 },
  tokens: { available: true, keys: ["sync_token", "webhook_url"] },
  free_bytes: 10 << 30,
};

describe("backup parts", () => {
  it("leaves the credentials out unless asked", () => {
    expect(DEFAULT_PARTS).toEqual(["data", "surveys", "config", "layouts"]);
  });

  it("asks only for ticked parts that hold something", () => {
    expect(chosenParts(new Set(DEFAULT_PARTS), inv)).toEqual(["data", "surveys", "config"]);
    expect(chosenParts(new Set(["tokens", "data"]), inv)).toEqual(["data", "tokens"]);
    // Before the server has said, the ticks stand.
    expect(chosenParts(new Set(DEFAULT_PARTS), null)).toEqual(DEFAULT_PARTS);
  });

  it("says what each part holds", () => {
    expect(partDetail("data", inv)).toBe("212 sessions · 1 lap · 845.0 MB");
    expect(partDetail("surveys", inv)).toBe("10 circuits · 1 correction · 7.0 MB");
    expect(partDetail("config", inv)).toBe("1 saved setting, without credentials");
    expect(partDetail("layouts", inv)).toBe("No layouts saved yet");
    expect(partDetail("tokens", inv)).toBe("The sync token and webhook URL");
    expect(partDetail("layouts", null)).toBe("Overlay and dash layouts");
  });

  it("warns about the credentials that are saved, and only those", () => {
    expect(tokensWarning(inv)).toBe(
      "The file then carries both: anyone holding it can post to your webhook and sign in to your sync account.",
    );
    const hookOnly = { ...inv, tokens: { available: true, keys: ["webhook_url"] } };
    expect(tokensWarning(hookOnly)).toBe("The file then carries it: anyone holding it can post to your webhook.");
  });
});
