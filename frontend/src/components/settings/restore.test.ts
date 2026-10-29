import { afterEach, describe, expect, it } from "vitest";
import { useI18n } from "@/i18n";
import en from "@/i18n/locales/en.json";
import es from "@/i18n/locales/es.json";
import ja from "@/i18n/locales/ja.json";
import type { RestoreReport, RestoreUpload } from "@/lib/types";
import {
  defaultItems,
  heldParts,
  machineSettings,
  restoreDetail,
  restoreNotes,
  restoreRequest,
  restoreSummary,
  restoreWarning,
  settingValue,
} from "./restore";

// A whole backup, uploaded into an install that has nothing yet.
const fresh: RestoreUpload = {
  id: "abc",
  name: "gt7-backup-2026-10-21.zip",
  bytes: 230_686_720,
  received_at: "2026-10-22T10:00:00+00:00",
  expires_in_s: 1800,
  app_version: "0.8.1",
  created_at: "2026-10-21T20:00:00+00:00",
  alembic_revision: "0013_lap_wheelbase",
  free_bytes: 10 << 30,
  here: { sessions: 0, laps: 0, source_id: true, tokens: [] },
  parts: {
    data: { available: true, refused: "", bytes: 886067200, sessions: 212, laps: 1840, needs_bytes: 950_000_000 },
    surveys: { available: true, refused: "", bytes: 7_340_032, bundles: 10, corrections: 1, logs: 3, car_widths: true },
    source_id: { available: true, refused: "" },
    config: {
      available: true,
      refused: "",
      settings: [
        { key: "ps_ip", label: "Console IP", group: "Connection", value: "192.168.1.20", machine: true },
        { key: "mdns", label: "Announce on the LAN", group: "Live stream", value: false, machine: true },
        { key: "race_engineer_units", label: "Spoken units", group: "Race Engineer", value: "imperial", machine: false },
        { key: "webhook_events", label: "Webhook events", group: "Notifications", value: ["personal_best"], machine: false },
      ],
      skipped: [],
    },
    layouts: {
      available: true,
      refused: "",
      layouts: [
        { name: "Pit wall", kind: "dash", replaces: false },
        { name: "Stream", kind: "overlay", replaces: false },
      ],
      skipped: [],
    },
    tokens: { available: true, refused: "", keys: ["webhook_url", "sync_token"] },
  },
};

// The same backup, into an install with recordings, layouts and a token of its own.
const lived: RestoreUpload = {
  ...fresh,
  here: { sessions: 30, laps: 410, source_id: true, tokens: ["sync_token"] },
  parts: {
    ...fresh.parts,
    layouts: { ...fresh.parts.layouts, layouts: [{ name: "Pit wall", kind: "dash", replaces: true }] },
  },
};

describe("which parts are ticked to begin with", () => {
  it("ticks everything for a fresh install, the identity and the tokens included", () => {
    expect(defaultItems(fresh)).toEqual(["data", "surveys", "source_id", "config", "layouts", "tokens"]);
  });

  it("leaves the identity and tokens alone on an install that has its own", () => {
    expect(defaultItems(lived)).toEqual(["data", "surveys", "config", "layouts"]);
  });

  it("counts an install whose only session has no lap yet as having no recordings", () => {
    const opened: RestoreUpload = { ...fresh, here: { ...fresh.here, sessions: 1, laps: 0 } };
    expect(defaultItems(opened)).toContain("source_id");
    expect(restoreWarning("data", opened)).toBe("");
  });

  it("never ticks a part the install cannot take", () => {
    const newer: RestoreUpload = {
      ...fresh,
      parts: { ...fresh.parts, data: { ...fresh.parts.data, refused: "the recordings were saved by a newer version" } },
    };
    expect(defaultItems(newer)).not.toContain("data");
  });

  it("lists only what the backup holds", () => {
    const configOnly: RestoreUpload = {
      ...fresh,
      parts: {
        data: { available: false },
        surveys: { available: false },
        source_id: { available: false },
        config: fresh.parts.config,
        layouts: { available: false },
        tokens: { available: false },
      },
    };
    expect(heldParts(configOnly).map((p) => p.id)).toEqual(["config"]);
    expect(defaultItems(configOnly)).toEqual(["config"]);
  });
});

describe("what a restore asks for", () => {
  it("lists the machine settings, none of them ticked to begin with", () => {
    expect(machineSettings(fresh).map((s) => s.key)).toEqual(["ps_ip", "mdns"]);
    expect(restoreRequest(new Set(defaultItems(fresh)), new Set(), fresh).machineSettings).toEqual([]);
  });

  it("sends the machine settings ticked, but only with Config", () => {
    const machine = new Set(["ps_ip"]);
    expect(restoreRequest(new Set(["config"]), machine, fresh)).toEqual({ items: ["config"], machineSettings: ["ps_ip"] });
    expect(restoreRequest(new Set(["layouts"]), machine, fresh)).toEqual({ items: ["layouts"], machineSettings: [] });
  });

  it("asks in the server's order, for parts it can take", () => {
    const refused: RestoreUpload = { ...fresh, parts: { ...fresh.parts, data: { ...fresh.parts.data, refused: "no room" } } };
    expect(restoreRequest(new Set(["tokens", "data", "surveys"]), new Set(), refused).items).toEqual(["surveys", "tokens"]);
  });
});

describe("what each part says", () => {
  it("says what it holds", () => {
    expect(restoreDetail("data", fresh)).toBe("212 sessions · 1,840 laps · 845.0 MB");
    expect(restoreDetail("surveys", fresh)).toBe(
      "10 circuits · 1 correction · 3 survey logs · car widths · 7.0 MB. Merged with what is here",
    );
    expect(restoreDetail("config", fresh)).toBe("2 saved settings · 2 for the machine, below");
    expect(restoreDetail("layouts", fresh)).toBe("2 layouts");
    expect(restoreDetail("tokens", fresh)).toBe("The webhook URL and sync token");
  });

  it("warns about what it would replace, and only that", () => {
    for (const id of ["data", "source_id", "layouts", "tokens"] as const) expect(restoreWarning(id, fresh)).toBe("");
    expect(restoreWarning("data", lived)).toBe(
      "Replaces the 30 sessions and 410 laps here, which are kept beside the database as gt7.db.before-restore-<time>.",
    );
    expect(restoreWarning("source_id", lived)).toMatch(/would count as one source/);
    expect(restoreWarning("layouts", lived)).toBe("Replaces Pit wall here.");
    expect(restoreWarning("tokens", lived)).toBe("Replaces the sync token saved here.");
  });

  it("shows a machine setting's value as a person reads it", () => {
    const [ip, mdns] = machineSettings(fresh);
    expect(settingValue(ip)).toBe("192.168.1.20");
    expect(settingValue(mdns)).toBe("off");
    expect(settingValue({ ...ip, value: "" })).toBe("empty");
    expect(settingValue({ ...ip, value: [] })).toBe("none");
  });
});

const report: RestoreReport = {
  data: { sessions: 212, laps: 1840, revision: "0013_lap_wheelbase", upgraded_from: "0012_x", kept: "gt7.db.before-restore-20261022-101500" },
  surveys: {
    bundles: [{ track: "Ring", slug: "ring", added_points: 4 }],
    corrections: [],
    logs: [],
    car_widths: 0,
    failed: [{ file: "track-bundles/broken.json", error: "invalid bundle" }],
  },
  config: { applied: ["race_engineer_units"], skipped: [{ key: "ws_rate", reason: "must be at most 60" }], left: ["ps_ip"] },
  layouts: { created: ["Stream"], replaced: ["Pit wall"], skipped: [] },
  tokens: { applied: ["sync_token"], skipped: [] },
  rejudging: ["ring"],
};

describe("after a restore", () => {
  it("sums it up in one line", () => {
    expect(restoreSummary(report)).toBe("Restored 212 sessions, 1 circuit, 1 setting, 2 layouts, sync token");
  });

  it("keeps what did not go back, and where the old recordings went", () => {
    expect(restoreNotes(report)).toEqual([
      "The recordings it replaced are kept as gt7.db.before-restore-20261022-101500; delete that file once you are happy.",
      "Its database was upgraded from 0012_x.",
      "track-bundles/broken.json: invalid bundle",
      "ws_rate: must be at most 60",
      "Left as they are here: ps_ip.",
      "Re-judging the laps on 1 circuit in the background.",
    ]);
  });
});

describe("in another language", () => {
  afterEach(() => useI18n.setState({ language: "en", messages: en }));

  it("words what a part holds and does in it", () => {
    useI18n.setState({ language: "ja", messages: ja });
    expect(restoreDetail("data", fresh)).toBe("212セッション · 1,840ラップ · 845.0 MB");
    expect(restoreWarning("tokens", lived)).toBe("ここに保存されている同期トークンを置き換えます。");
    expect(restoreSummary(report)).toBe("復元しました: 212セッション、1コース、1件の設定、2件のレイアウト、同期トークン");
  });

  it("lists the way the language does", () => {
    useI18n.setState({ language: "es", messages: es });
    const two: RestoreUpload = {
      ...lived,
      parts: {
        ...lived.parts,
        layouts: {
          ...lived.parts.layouts,
          layouts: [
            { name: "Pit wall", kind: "dash", replaces: true },
            { name: "Stream", kind: "overlay", replaces: true },
          ],
        },
      },
    };
    expect(restoreWarning("layouts", two)).toBe("Sustituye Pit wall y Stream que hay aquí.");
    expect(restoreNotes(report).at(-1)).toBe("Revaluando en segundo plano las vueltas de 1 circuito.");
  });
});
