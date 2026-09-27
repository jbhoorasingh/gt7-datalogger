// 1c · 500 — an unhandled server error behind a view. Saved laps are never
// at risk from one, and the page says so before anything else.

import { useState } from "react";
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
  // When it failed, fixed at first render: the line is for matching a log.
  const [at] = useState(() => new Date().toISOString().replace(/\.\d{3}Z$/, "Z"));
  return (
    <ErrorPage
      flag={FLAGS.red}
      kicker="Red flag · session stopped"
      code={String(status)}
      title="Red flag. Something broke on the server."
      body="Every lap already saved is safe in the database. If recording was on, the lap in progress may not have been saved. The server log has the details."
      primary={{ label: "Try again", onClick: onRetry ?? (() => window.location.reload()) }}
      secondary={{ label: "Open logs", onClick: () => openSettings("logs") }}
      diag={[request, status, at].filter((part) => part != null).join(" · ")}
      art={<RedFlagArt />}
      wide="text"
    />
  );
}
