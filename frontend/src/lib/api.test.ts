import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, isTokenError } from "./api";

function respond(status: number, body: unknown) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return vi.fn(async () => new Response(text, { status, headers: { "Content-Type": "application/json" } }));
}

async function failure(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("expected the request to fail");
}

beforeEach(() => {
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a refused request", () => {
  it("says what the request guard said, not that the token was rejected", async () => {
    const detail = "LAN access is off: only the computer running GT7 Datalogger can use it.";
    vi.stubGlobal("fetch", respond(403, { detail, reason: "lan_off" }));
    const e = await failure(api.admin.settings());
    expect(e.status).toBe(403);
    expect(e.reason).toBe("lan_off");
    expect(e.message).toBe(detail);
    expect(isTokenError(e)).toBe(false);
  });

  it("keeps every guard reason", async () => {
    for (const reason of ["untrusted_host", "cross_site"]) {
      vi.stubGlobal("fetch", respond(403, { detail: `refused: ${reason}`, reason }));
      const e = await failure(api.status());
      expect(e.reason).toBe(reason);
      expect(e.message).toBe(`refused: ${reason}`);
    }
  });

  it("still blames the token for a 403 without a reason", async () => {
    vi.stubGlobal("fetch", respond(403, { detail: "invalid admin token" }));
    const e = await failure(api.admin.settings());
    expect(e.reason).toBeUndefined();
    expect(e.message).toMatch(/admin token rejected/);
    expect(isTokenError(e)).toBe(true);
  });

  it("and for a 403 that is not JSON at all", async () => {
    vi.stubGlobal("fetch", respond(403, "<html>Forbidden</html>"));
    const e = await failure(api.admin.settings());
    expect(isTokenError(e)).toBe(true);
  });

  it("asks for the token on a 401", async () => {
    vi.stubGlobal("fetch", respond(401, { detail: "admin token required (X-API-Key header)" }));
    const e = await failure(api.admin.settings());
    expect(e.message).toMatch(/admin token required/);
    expect(isTokenError(e)).toBe(true);
  });
});

describe("finishing setup", () => {
  it("posts the phone answer to /api/admin/setup/complete", async () => {
    const fetch = respond(200, { completed: true, admin_token: "tok" });
    vi.stubGlobal("fetch", fetch);
    const done = await api.admin.completeSetup({ lan_access: true });
    expect(done.admin_token).toBe("tok");
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/admin/setup/complete");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ lan_access: true });
  });

  it("sends no body when there is no question to answer (Skip setup, Docker)", async () => {
    const fetch = respond(200, { completed: true, admin_token: null });
    vi.stubGlobal("fetch", fetch);
    await api.admin.completeSetup(undefined);
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});
