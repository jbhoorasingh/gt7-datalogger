// 1b · 401 — the server sets GT7_ADMIN_TOKEN and this browser has none (or
// the wrong one). The token goes into the same per-browser store Settings ›
// Access uses, is checked against the admin API, and the caller retries.

import { useState } from "react";
import { useT } from "@/i18n";
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
  const t = useT();
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
          ? t("error.auth.rejected")
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
      kicker={t("error.auth.kicker")}
      code={String(status)}
      title={t("error.auth.title")}
      body={t("error.auth.body")}
      primary={{ label: t("error.auth.backToLive"), onClick: () => navigate("live") }}
      secondary={{ label: t("error.auth.whereToFind"), href: ADMIN_TOKEN_DOCS_URL }}
      diag={`${request} → ${status} · X-API-Key ${sent ? t("error.auth.keyRejected") : t("error.auth.keyMissing")}`}
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
            aria-label={t("settings.access.token")}
            className="min-w-0 flex-1 rounded-md border border-edge bg-panel-2 px-3 py-[7px] font-tabular text-[13px] text-ink placeholder:text-ink-ghost focus:border-edge-bright focus:outline-none"
          />
          <button
            type="submit"
            className="btn btn-primary px-3.5 py-1.5 text-xs"
            disabled={busy || token.trim() === ""}
          >
            {busy ? t("error.auth.checking") : t("error.auth.unlock")}
          </button>
        </div>
        {error && <span className="text-[11.5px] text-brake">{error}</span>}
      </form>
    </ErrorPage>
  );
}
