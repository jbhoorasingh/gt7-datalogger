// 1a · 404 — an unknown route, or a deep link to a session that isn't in
// this logger's database.

import { useT } from "@/i18n";
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
  const t = useT();
  return (
    <ErrorPage
      flag={FLAGS.chequered}
      kicker={t("error.notFound.kicker")}
      code="404"
      title={t("error.notFound.title")}
      body={body ?? t("error.notFound.body")}
      primary={{ label: t("error.notFound.backToSessions"), onClick: () => navigate("sessions") }}
      secondary={{ label: t("error.notFound.openLive"), onClick: () => navigate("live") }}
      diag={diag ?? t("error.notFound.diag", { where: window.location.hash || "#/" })}
      art={<OffTrackArt />}
    />
  );
}
