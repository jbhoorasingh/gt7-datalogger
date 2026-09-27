// 1e · No telemetry — the server is up but no frames are arriving from the
// console. The right side is a live checklist built from /api/status and the
// socket, re-run every 5 s, so the driver can see which link is missing.

import { useEffect, useState } from "react";
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
      toast("Switched to the simulated source", "success");
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
  const target = status?.source === "sim" ? "simulator" : status?.console_ip || "broadcast";
  return (
    <ErrorPage
      flag={FLAGS.white}
      kicker="White flag · slow car on track"
      code="0 Hz"
      title="The server is up, but the console is silent."
      body="Live, Dash and the Race Engineer need telemetry from GT7. Point the logger at your PlayStation, or switch to the simulated source to try things out."
      primary={{ label: "Set console IP", onClick: () => openSettings("connection") }}
      secondary={{
        label: switching ? "Switching…" : "Use simulated source",
        onClick: () => void switchToSim(),
        disabled: switching || status?.source === "sim",
      }}
      diag={`udp 0.0.0.0:${TELEMETRY_PORT} · ${packets.toLocaleString()} packets · heartbeat → ${target}`}
      art={<ChecksPanel checks={buildChecks(serverUp, wsConnected, status)} />}
    />
  );
}

function buildChecks(
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
      label: "Datalogger server",
      ...(serverUp == null
        ? { state: "wait", detail: "checking…" }
        : serverUp
          ? { state: "ok", detail: "responding" }
          : { state: "bad", detail: "not responding" }),
    },
    {
      label: "Browser ↔ server",
      ...(wsConnected
        ? { state: "ok", detail: "websocket open" }
        : { state: "bad", detail: "websocket closed" }),
    },
    {
      label: "Console discovery",
      ...(sim
        ? { state: "ok", detail: "simulated source" }
        : flowing
          ? { state: "ok", detail: ip }
          : ip
            ? { state: "bad", detail: `no reply from ${ip}` }
            : { state: "bad", detail: "no console found on the LAN" }),
    },
    {
      label: `Telemetry on ${TELEMETRY_PORT}/udp`,
      ...(flowing
        ? { state: "ok", detail: `${packets.toLocaleString()} packets` }
        : packets > 0
          ? { state: "bad", detail: `${packets.toLocaleString()} packets · none lately` }
          : { state: "bad", detail: "0 packets" }),
    },
    {
      label: "GT7 in a session",
      state: "wait",
      detail:
        lastFrameS != null
          ? `last frame ${lastFrameS} s ago`
          : "can’t tell until packets arrive",
    },
  ];
}

const DOT: Record<CheckState, string> = {
  ok: "bg-throttle",
  bad: "bg-brake shadow-[0_0_6px_var(--color-brake)]",
  wait: "bg-ink-faint",
};

function ChecksPanel({ checks }: { checks: Check[] }) {
  return (
    <div className="panel w-full max-w-[520px]">
      <div className="flex items-baseline gap-2 px-4 py-2.5">
        <span className="section-header">Checks</span>
        <span className="text-[10.5px] text-ink-faint">re-run every 5 s</span>
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
        Most often: the console’s IP changed after a router restart, or GT7 is still on the
        title screen — telemetry only flows once you are on track or in a replay.{" "}
        <a
          className="text-accent hover:underline"
          href={NO_TELEMETRY_DOCS_URL}
          target="_blank"
          rel="noreferrer"
        >
          More in the docs
        </a>
      </div>
    </div>
  );
}
