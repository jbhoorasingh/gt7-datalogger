// 1e · No telemetry — the server is up but no frames are arriving from the
// console. The right side is a live checklist built from /api/status and the
// socket, re-run every 5 s, so the driver can see which link is missing.

import { useEffect, useState } from "react";
import { rich, useT, type Translate } from "@/i18n";
import { ApiError, api } from "@/lib/api";
import { openSettings } from "@/lib/router";
import type { ConnectionStatus } from "@/lib/types";
import { toast } from "@/store/toasts";
import { liveFrameRef, useTelemetry } from "@/store/telemetry";
import { ErrorPage, NO_TELEMETRY_DOCS_URL } from "./ErrorPage";
import { FLAGS } from "./illustrations";

const RECHECK_MS = 5000;
// GT7's telemetry port. /api/status doesn't report it; this is the default
// every console uses.
const TELEMETRY_PORT = 33740;

type CheckState = "ok" | "bad" | "wait";

interface Check {
  state: CheckState;
  label: string;
  detail: string;
}

export function NoTelemetryPage() {
  const t = useT();
  const wsConnected = useTelemetry((s) => s.wsConnected);
  const status = useTelemetry((s) => s.status);
  const setStatus = useTelemetry((s) => s.setStatus);
  const [serverUp, setServerUp] = useState<boolean | null>(null);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    let alive = true;
    const check = () =>
      api
        .status()
        .then((s) => {
          if (!alive) return;
          setServerUp(true);
          setStatus(s);
        })
        .catch(() => alive && setServerUp(false));
    check();
    const id = window.setInterval(check, RECHECK_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [setStatus]);

  const switchToSim = async () => {
    setSwitching(true);
    try {
      await api.admin.updateSettings({ source: "sim" });
      toast(t("error.noTelemetry.switched"), "success");
      api.status().then(setStatus).catch(() => {});
    } catch (e) {
      // Locked server: the switch lives in Settings, behind the token.
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        toast(e.message, "error");
        openSettings("connection");
      } else {
        toast(e instanceof Error ? e.message : String(e), "error");
      }
    } finally {
      setSwitching(false);
    }
  };

  const packets = status?.packets_received ?? 0;
  const target =
    status?.source === "sim" ? t("error.noTelemetry.simulator") : status?.console_ip || "broadcast";
  return (
    <ErrorPage
      flag={FLAGS.white}
      kicker={t("error.noTelemetry.kicker")}
      code="0 Hz"
      title={t("error.noTelemetry.title")}
      body={t("error.noTelemetry.body")}
      primary={{ label: t("error.noTelemetry.setIp"), onClick: () => openSettings("connection") }}
      secondary={{
        label: switching ? t("error.noTelemetry.switching") : t("error.noTelemetry.useSim"),
        onClick: () => void switchToSim(),
        disabled: switching || status?.source === "sim",
      }}
      diag={`udp 0.0.0.0:${TELEMETRY_PORT} · ${t("error.noTelemetry.packets", { count: packets })} · heartbeat → ${target}`}
      art={<ChecksPanel checks={buildChecks(t, serverUp, wsConnected, status)} />}
    />
  );
}

function buildChecks(
  t: Translate,
  serverUp: boolean | null,
  wsConnected: boolean,
  status: ConnectionStatus | null,
): Check[] {
  const sim = status?.source === "sim";
  const flowing = status?.connected ?? false;
  const packets = status?.packets_received ?? 0;
  const ip = status?.console_ip ?? "";
  const lastFrameS =
    liveFrameRef.at > 0 ? Math.round((performance.now() - liveFrameRef.at) / 1000) : null;
  return [
    {
      label: t("error.noTelemetry.check.server"),
      ...(serverUp == null
        ? { state: "wait", detail: t("error.noTelemetry.check.checking") }
        : serverUp
          ? { state: "ok", detail: t("error.noTelemetry.check.responding") }
          : { state: "bad", detail: t("error.noTelemetry.check.notResponding") }),
    },
    {
      label: t("error.noTelemetry.check.socket"),
      ...(wsConnected
        ? { state: "ok", detail: t("error.noTelemetry.check.socketOpen") }
        : { state: "bad", detail: t("error.noTelemetry.check.socketClosed") }),
    },
    {
      label: t("error.noTelemetry.check.discovery"),
      ...(sim
        ? { state: "ok", detail: t("error.noTelemetry.check.simulated") }
        : flowing
          ? { state: "ok", detail: ip }
          : ip
            ? { state: "bad", detail: t("error.noTelemetry.check.noReply", { ip }) }
            : { state: "bad", detail: t("error.noTelemetry.check.noConsole") }),
    },
    {
      label: t("error.noTelemetry.check.telemetry", { port: String(TELEMETRY_PORT) }),
      ...(flowing
        ? { state: "ok", detail: t("error.noTelemetry.packets", { count: packets }) }
        : packets > 0
          ? {
              state: "bad",
              detail: t("error.noTelemetry.check.noneLately", {
                packets: t("error.noTelemetry.packets", { count: packets }),
              }),
            }
          : { state: "bad", detail: t("error.noTelemetry.packets", { count: 0 }) }),
    },
    {
      label: t("error.noTelemetry.check.inSession"),
      state: "wait",
      detail:
        lastFrameS != null
          ? t("error.noTelemetry.check.lastFrame", { s: lastFrameS })
          : t("error.noTelemetry.check.cantTell"),
    },
  ];
}

const DOT: Record<CheckState, string> = {
  ok: "bg-throttle",
  bad: "bg-brake shadow-[0_0_6px_var(--color-brake)]",
  wait: "bg-ink-faint",
};

function ChecksPanel({ checks }: { checks: Check[] }) {
  const t = useT();
  return (
    <div className="panel w-full max-w-[520px]">
      <div className="flex items-baseline gap-2 px-4 py-2.5">
        <span className="section-header">{t("error.noTelemetry.checks")}</span>
        <span className="text-[10.5px] text-ink-faint">{t("error.noTelemetry.rerun")}</span>
      </div>
      <div className="rule" />
      {checks.map((c) => (
        <div
          key={c.label}
          className="rule-row grid grid-cols-[14px_1fr_auto] items-center gap-2.5 px-4 py-[11px] text-[12.5px]"
        >
          <span className={`h-[7px] w-[7px] rounded-full ${DOT[c.state]}`} />
          <span>{c.label}</span>
          <span
            className={`font-tabular text-[11px] ${c.state === "bad" ? "text-brake" : "text-ink-dim"}`}
          >
            {c.detail}
          </span>
        </div>
      ))}
      <div className="px-4 py-3 text-[11px] leading-normal text-ink-dim">
        {rich(t("error.noTelemetry.mostOften"), {
          link: (
            <a
              className="text-accent hover:underline"
              href={NO_TELEMETRY_DOCS_URL}
              target="_blank"
              rel="noreferrer"
            >
              {t("error.noTelemetry.moreInDocs")}
            </a>
          ),
        })}
      </div>
    </div>
  );
}
