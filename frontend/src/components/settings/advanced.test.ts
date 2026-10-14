import { describe, expect, it } from "vitest";
import type { ConfigSetting } from "@/lib/types";
import {
  NO_EDITS,
  parseNumber,
  pendingCount,
  plainDoc,
  resetKey,
  restartPending,
  setValue,
  shownValue,
} from "./advanced";

function row(over: Partial<ConfigSetting>): ConfigSetting {
  return {
    key: "ws_rate",
    env: "GT7_WS_RATE",
    group: "Live stream",
    label: "Live stream rate",
    doc: "",
    kind: "int",
    apply: "live",
    editable: true,
    secret: false,
    source: "env",
    restart_pending: false,
    value: 30,
    default: 30,
    ...over,
  };
}

describe("plainDoc", () => {
  it("keeps link text and drops code and bold marks", () => {
    expect(plainDoc("See [the docs](../x.md#y): `udp` or **most**")).toBe("See the docs: udp or most");
  });
});

describe("edits", () => {
  it("drops a value that matches the server again", () => {
    const r = row({});
    const e = setValue(NO_EDITS, r, 45);
    expect(pendingCount(e)).toBe(1);
    expect(pendingCount(setValue(e, r, 30))).toBe(0);
  });

  it("compares lists as sets", () => {
    const r = row({ key: "webhook_events", kind: "list", value: ["a", "b"] });
    expect(pendingCount(setValue(NO_EDITS, r, ["b", "a"]))).toBe(0);
  });

  it("always counts a typed secret, and never shows the stored one", () => {
    const r = row({ key: "sync_token", secret: true, value: null, kind: "str" });
    expect(shownValue(r, NO_EDITS)).toBe("");
    expect(pendingCount(setValue(NO_EDITS, r, ""))).toBe(1);
  });

  it("reset replaces a typed value and shows what the server falls back to", () => {
    const r = row({ source: "saved", value: 12, reset_value: 25 });
    const e = resetKey(setValue(NO_EDITS, r, 50), "ws_rate");
    expect(e).toEqual({ values: {}, reset: ["ws_rate"] });
    // The environment's 25, not the shipped default of 30.
    expect(shownValue(r, e)).toBe(25);
    // Typing again un-resets it.
    expect(setValue(e, r, 40).reset).toEqual([]);
  });
});

describe("parseNumber", () => {
  it("reads ints and floats by kind, null while incomplete", () => {
    expect(parseNumber(row({}), "45")).toBe(45);
    expect(parseNumber(row({ kind: "float" }), "2.5")).toBe(2.5);
    expect(parseNumber(row({}), "")).toBeNull();
    expect(parseNumber(row({}), "abc")).toBeNull();
    expect(parseNumber(row({}), "4e1")).toBe(40);
    expect(parseNumber(row({}), "4.5")).toBeNull();
    expect(parseNumber(row({}), "12abc")).toBeNull();
  });
});

describe("restartPending", () => {
  it("lists only settings waiting for a restart", () => {
    const rows = [row({}), row({ key: "cars_json", restart_pending: true })];
    expect(restartPending(rows).map((r) => r.key)).toEqual(["cars_json"]);
  });
});
