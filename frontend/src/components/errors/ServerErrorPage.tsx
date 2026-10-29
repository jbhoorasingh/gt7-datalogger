// 1c · 500 — an unhandled server error behind a view. Saved laps are never
// at risk from one, and the page says so before anything else.

import { useState } from "react";
import { useT } from "@/i18n";
import { openSettings } from "@/lib/router";
import { ErrorPage } from "./ErrorPage";
import { FLAGS, RedFlagArt } from "./illustrations";

export interface ServerErrorPageProps {
  /** Re-runs whatever failed; defaults to a page reload. */
  onRetry?: () => void;
  /** The failing request, e.g. "GET /api/sessions/71/laps". */
  request?: string;
  status?: number;
}

export function ServerErrorPage({ onRetry, request, status = 500 }: ServerErrorPageProps) {
  const t = useT();
  // When it failed, fixed at first render: the line is for matching a log.
  const [at] = useState(() => new Date().toISOString().replace(/\.\d{3}Z$/, "Z"));
  return (
    <ErrorPage
      flag={FLAGS.red}
      kicker={t("error.server.kicker")}
      code={String(status)}
      title={t("error.server.title")}
      body={t("error.server.body")}
      primary={{ label: t("error.server.tryAgain"), onClick: onRetry ?? (() => window.location.reload()) }}
      secondary={{ label: t("error.server.openLogs"), onClick: () => openSettings("logs") }}
      diag={[request, status, at].filter((part) => part != null).join(" · ")}
      art={<RedFlagArt />}
      wide="text"
    />
  );
}
