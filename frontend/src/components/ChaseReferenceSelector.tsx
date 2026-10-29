// Coaching reference card on /engineer: coach against the session's fastest
// lap (the default) or chase a stored lap of the circuit being driven. The
// choice lives on the server, which reports it in race_engineer_status, so
// every page shows the same one.

import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import { formatLapTime, formatTime } from "@/lib/format";
import type { LapSummary } from "@/lib/types";
import { sendWs, subscribeWs } from "@/lib/wsBus";
import { useEngineer } from "@/store/engineer";
import { useTelemetry } from "@/store/telemetry";

export function ChaseReferenceSelector() {
  const t = useT();
  const trackName = useTelemetry((st) => st.status?.track_name ?? "");
  const wsConnected = useTelemetry((st) => st.wsConnected);
  const lapEpoch = useTelemetry((st) => st.lapEpoch);
  const mode = useEngineer((s) => s.serverStatus?.reference_mode ?? "session_best");
  const chasedId = useEngineer((s) => s.serverStatus?.reference_lap_id ?? null);
  const chasedMs = useEngineer((s) => s.serverStatus?.reference_time_ms ?? null);
  const [picking, setPicking] = useState(false);
  // Keyed by circuit, so a list fetched for the last one is never offered.
  const [list, setList] = useState<{ track: string; laps: LapSummary[] } | null>(null);
  const [chased, setChased] = useState<LapSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const laps = list?.track === trackName ? list.laps : null;

  // A lap the server can't chase comes back as an error naming the request.
  useEffect(
    () =>
      subscribeWs((msg) => {
        if (msg.type === "error" && msg.data.request === "set_engineer_reference") {
          setError(msg.data.message);
        }
      }),
    [],
  );

  // Only while the list is open; a new lap or session refreshes it.
  useEffect(() => {
    if (!picking || !trackName) return;
    let live = true;
    api
      .laps(trackName)
      .then((all) => {
        if (!live) return;
        const counting = all.filter((lap) => lap.counts_for_best && lap.time_ms > 0);
        setList({ track: trackName, laps: counting.sort((a, b) => a.time_ms - b.time_ms) });
      })
      .catch(() => live && setError(t("chase.loadFailed")));
    return () => {
      live = false;
    };
  }, [picking, trackName, lapEpoch, t]);

  // Name the lap being chased, wherever the choice was made.
  useEffect(() => {
    if (chasedId == null) {
      setChased(null);
      return;
    }
    let live = true;
    api
      .lapDetail(chasedId, false)
      .then((lap) => live && setChased(lap))
      .catch(() => live && setChased(null));
    return () => {
      live = false;
    };
  }, [chasedId]);

  const choose = (lapId: number | null) => {
    setError(null);
    if (!sendWs({ type: "set_engineer_reference", data: { lap_id: lapId } })) {
      setError(t("chase.notConnected"));
      return;
    }
    setPicking(false);
  };

  const chasing = mode === "chase";
  const chasedLabel =
    chasedId == null
      ? t("chase.yourLap")
      : chased
        ? `${chased.car_name ?? "–"} · ${t("chase.lapRef", { session: String(chased.session_id), lap: chased.number })}`
        : t("chase.lapId", { id: String(chasedId) });

  return (
    <div className="panel">
      <div className="flex items-baseline gap-2 px-4 py-2.5">
        <span className="section-header">{t("chase.title")}</span>
        <span className="min-w-0 truncate text-[10.5px] text-ink-faint">
          {trackName || t("chase.noCircuit")}
        </span>
      </div>
      <div className="rule" />
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label={t("chase.title")}>
          <Choice
            checked={!chasing}
            disabled={!wsConnected}
            label={t("chase.sessionBest")}
            hint={t("chase.sessionBestHint")}
            onClick={() => choose(null)}
          />
          <Choice
            checked={chasing}
            disabled={!wsConnected || !trackName}
            label={t("chase.chaseLap")}
            hint={t("chase.chaseLapHint")}
            onClick={() => setPicking((open) => !open)}
          />
        </div>

        {chasing && chasedMs != null && (
          <div className="flex min-w-0 items-baseline gap-2 text-[11.5px]">
            <span className="whitespace-nowrap text-ink-faint">{t("chase.chasing")}</span>
            <span className="min-w-0 truncate text-ink-soft">{chasedLabel}</span>
            <span className="ml-auto font-tabular text-accent-300">{formatLapTime(chasedMs)}</span>
          </div>
        )}

        {picking && (
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {laps == null &&
              [0, 1, 2].map((i) => <div key={i} className="skeleton h-9" />)}
            {laps != null && laps.length === 0 && (
              <p className="text-[11px] text-ink-faint">{t("chase.noLaps")}</p>
            )}
            {laps?.map((lap) => {
              const current = chasing && lap.id === chasedId;
              return (
                <button
                  key={lap.id}
                  disabled={!wsConnected}
                  aria-current={current || undefined}
                  onClick={() => choose(lap.id)}
                  className={`flex w-full items-baseline gap-2 rounded-md border px-3 py-2 text-left text-xs transition-colors disabled:opacity-45 ${
                    current
                      ? "border-accent/60 bg-accent/10 text-accent-300"
                      : "border-edge text-ink enabled:hover:border-accent enabled:hover:bg-panel-2"
                  }`}
                >
                  <span className="shrink-0 font-tabular">{formatLapTime(lap.time_ms)}</span>
                  <span className="min-w-0 truncate text-ink-dim">
                    · {lap.car_name ?? "–"} ·{" "}
                    {t("chase.lapRef", { session: String(lap.session_id), lap: lap.number })}
                    {lap.finished_at ? ` · ${formatTime(lap.finished_at)}` : ""}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {error && (
          <p role="alert" className="text-[11px] text-brake">
            {error}
          </p>
        )}

        <span className="text-[11px] text-ink-faint">
          {!chasing
            ? t("chase.explainBest")
            : chasedId == null
              ? t("chase.explainBeaten")
              : t("chase.explainChasing")}
        </span>
      </div>
    </div>
  );
}

function Choice({
  checked,
  disabled,
  label,
  hint,
  onClick,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      role="radio"
      aria-checked={checked}
      disabled={disabled}
      className={`flex min-h-11 flex-col gap-[3px] rounded-md border px-3 py-2.5 text-left transition-colors disabled:cursor-default disabled:opacity-45 ${
        checked ? "border-accent/60 bg-accent/10" : "border-edge enabled:hover:border-edge-bright"
      }`}
      onClick={onClick}
    >
      <span className={`text-[13px] ${checked ? "text-accent-300" : "text-ink"}`}>{label}</span>
      <span className="text-[10.5px] leading-snug text-ink-dim">{hint}</span>
    </button>
  );
}
