import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { translator } from "@/i18n";
import en from "@/i18n/locales/en.json";
import { api, ApiError } from "./api";
import type { UpdateInstall, UpdateStatus } from "./types";
import {
  clearUpdating,
  downloadPercent,
  installUpdate,
  markUpdating,
  refusal,
  updateMessage,
  updatePhase,
  updatingTo,
} from "./update";

const t = translator("en", en);

function offered(install: Partial<UpdateInstall> | null = null): UpdateStatus {
  return {
    available: true,
    version: "1.0.1",
    url: "https://github.com/jbhoorasingh/gt7-datalogger/releases/tag/v1.0.1",
    checked_at: null,
    install: install && {
      state: "idle", version: null, progress: null, error: null, on_quit: true, ...install,
    },
  };
}

beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the update's phase", () => {
  it("is none without a newer release", () => {
    expect(updatePhase(null)).toBe("none");
    expect(updatePhase({ available: false, install: null })).toBe("none");
    // A finished download for a release no longer offered is not shown.
    expect(updatePhase({ available: false, install: offered({ state: "ready" }).install })).toBe("none");
  });

  it("is a notice where the install cannot update itself, or has not started", () => {
    expect(updatePhase(offered())).toBe("notify");
    expect(updatePhase({ available: true, version: "1.0.1" })).toBe("notify"); // an older server
    expect(updatePhase(offered({ state: "idle" }))).toBe("notify");
  });

  it("follows the desktop app's download and install", () => {
    for (const state of ["downloading", "ready", "installing", "failed"] as const) {
      expect(updatePhase(offered({ state, version: "1.0.1" }))).toBe(state);
    }
  });
});

describe("what the banner says", () => {
  it("words each phase", () => {
    expect(updateMessage(offered(), t)).toBe("Version 1.0.1 is available");
    expect(updateMessage(offered({ state: "downloading", version: "1.0.1", progress: 0.456 }), t)).toBe(
      "Downloading version 1.0.1… 45%",
    );
    expect(updateMessage(offered({ state: "ready", version: "1.0.1" }), t)).toBe("Version 1.0.1 is ready to install");
    expect(updateMessage(offered({ state: "failed", version: "1.0.1", error: "offline" }), t)).toBe(
      "Version 1.0.1 could not be installed: offline",
    );
  });

  it("keeps the percentage between 0 and 100", () => {
    expect(downloadPercent(offered({ state: "downloading", progress: 1.2 }))).toBe(100);
    expect(downloadPercent(offered({ state: "downloading", progress: null }))).toBe(0);
  });
});

describe("a refused install", () => {
  it("says the server's reason", () => {
    const e = new ApiError(
      '/api/admin/update-install: 409 {"detail":"a session is recording; finish the session, then update"}',
      409,
    );
    expect(refusal(e, "fallback")).toBe("a session is recording; finish the session, then update");
    expect(refusal(new Error("network down"), "fallback")).toBe("fallback");
  });
});

describe("Restart and update", () => {
  it("remembers the version for the page shown while the server is away", async () => {
    vi.spyOn(api.admin, "updateInstall").mockResolvedValue(offered({ state: "installing" }));
    expect(updatingTo()).toBeNull();
    await installUpdate(offered({ state: "ready", version: "1.0.1" }));
    expect(updatingTo()).toBe("1.0.1");
    clearUpdating();
    expect(updatingTo()).toBeNull();
  });

  it("forgets it after a while", () => {
    markUpdating("1.0.1", 0);
    expect(updatingTo(5 * 60_000)).toBe("1.0.1");
    expect(updatingTo(11 * 60_000)).toBeNull();
  });

  it("marks nothing when the server refuses", async () => {
    vi.spyOn(api.admin, "updateInstall").mockRejectedValue(new ApiError("409", 409));
    await expect(installUpdate(offered({ state: "ready", version: "1.0.1" }))).rejects.toThrow();
    expect(updatingTo()).toBeNull();
  });
});
