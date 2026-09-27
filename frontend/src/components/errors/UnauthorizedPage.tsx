// 1b · 401 — the server sets GT7_ADMIN_TOKEN and this browser has none (or
// the wrong one). The token goes into the same per-browser store Settings ›
// Access uses, is checked against the admin API, and the caller retries.

import { useState } from "react";
import { ApiError, api, getAdminToken, setAdminToken } from "@/lib/api";
import { navigate } from "@/lib/router";
import { ADMIN_TOKEN_DOCS_URL, ErrorPage } from "./ErrorPage";
import { BlackFlagArt, FLAGS } from "./illustrations";

export interface UnauthorizedPageProps {
  /** Called once a token has been accepted; defaults to a page reload. */
  onUnlock?: () => void;
  /** The request that was refused, e.g. "GET /api/admin/settings". */
  request?: string;
  /** 401 (no token sent) or 403 (token rejected). */
  status?: number;
}

export function UnauthorizedPage({
  onUnlock,
  request = "GET /api/admin/settings",
  status = 401,
}: UnauthorizedPageProps) {
  const [token, setToken] = useState(getAdminToken);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const unlock = async () => {
    const value = token.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    setAdminToken(value);
    try {
      // Any admin read proves the token; settings is the cheapest.
      await api.admin.settings();
      if (onUnlock) onUnlock();
      else window.location.reload();
    } catch (e) {
      setError(
        e instanceof ApiError && (e.status === 401 || e.status === 403)
          ? "The server rejected that token. Check it and try again."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setBusy(false);
    }
  };

  const sent = getAdminToken() !== "";
  return (
    <ErrorPage
      flag={FLAGS.black}
      kicker="Black flag · report to the stewards"
      code={String(status)}
      title="Settings are locked on this server."
      body="Whoever runs this logger set an admin token. Enter it once and this browser remembers it. Live, overlays and /dash never need it."
      primary={{ label: "Back to Live", onClick: () => navigate("live") }}
      secondary={{ label: "Where do I find it?", href: ADMIN_TOKEN_DOCS_URL }}
      diag={`${request} → ${status} · X-API-Key ${sent ? "rejected" : "missing"}`}
      art={<BlackFlagArt />}
    >
      <form
        className="flex w-full max-w-[420px] flex-col gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          void unlock();
        }}
      >
        <div className="flex gap-2">
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="GT7_ADMIN_TOKEN"
            autoComplete="off"
            aria-label="Admin token"
            className="min-w-0 flex-1 rounded-md border border-edge bg-panel-2 px-3 py-[7px] font-tabular text-[13px] text-ink placeholder:text-ink-ghost focus:border-edge-bright focus:outline-none"
          />
          <button
            type="submit"
            className="btn btn-primary px-3.5 py-1.5 text-xs"
            disabled={busy || token.trim() === ""}
          >
            {busy ? "Checking…" : "Unlock settings"}
          </button>
        </div>
        {error && <span className="text-[11.5px] text-brake">{error}</span>}
      </form>
    </ErrorPage>
  );
}
