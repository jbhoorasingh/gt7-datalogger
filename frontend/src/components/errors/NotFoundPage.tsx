// 1a · 404 — an unknown route, or a deep link to a session that isn't in
// this logger's database.

import { navigate } from "@/lib/router";
import { ErrorPage } from "./ErrorPage";
import { FLAGS, OffTrackArt } from "./illustrations";

export interface NotFoundPageProps {
  /** Replaces the generic paragraph, e.g. to name the missing session. */
  body?: React.ReactNode;
  /** Monospace line under the actions; defaults to the current hash. */
  diag?: string;
}

export function NotFoundPage({ body, diag }: NotFoundPageProps) {
  return (
    <ErrorPage
      flag={FLAGS.chequered}
      kicker="Chequered · off the circuit"
      code="404"
      title="You’ve gone wide. That lap isn’t on record."
      body={
        body ??
        "There’s no page at this address in the datalogger. The link may be mistyped, or it came from another installation — lap and session links only work on the logger that recorded them."
      }
      primary={{ label: "Back to Sessions", onClick: () => navigate("sessions") }}
      secondary={{ label: "Open Live", onClick: () => navigate("live") }}
      diag={diag ?? `${window.location.hash || "#/"} · no such page`}
      art={<OffTrackArt />}
    />
  );
}
