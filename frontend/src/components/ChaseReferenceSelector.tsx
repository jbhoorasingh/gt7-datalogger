
import { useEffect, useState } from "react";
import { useTelemetry } from "@/store/telemetry";
import { api } from "@/lib/api";
import type { LapSummary } from "@/lib/types";
import { useEngineer } from "@/store/engineer";
import { sendWs, subscribeWs } from "@/lib/wsBus";

export function ChaseReferenceSelector() {
const [showLapPicker, setShowLapPicker] = useState(false);
const [savedLaps, setSavedLaps] = useState<LapSummary[]>([]);
const [loadingLaps, setLoadingLaps] = useState(false);
const [lapError, setLapError] = useState<string | null>(null);
  const trackName = useTelemetry((st) => st.status?.track_name ?? "");
  const lapEpoch = useTelemetry((st) => st.lapEpoch);
  const wsConnected = useTelemetry((st) => st.wsConnected);
  const referenceMode = useEngineer(
    (st) => st.serverStatus?.reference_mode ?? "session_best"
  );

const referenceLapId = useEngineer(
  (st) => st.serverStatus?.reference_lap_id ?? null
);

const referenceTimeMs = useEngineer(
  (st) => st.serverStatus?.reference_time_ms ?? null
);

const selectedLap = savedLaps.find((lap) => lap.id === referenceLapId);

useEffect(() => {
  return subscribeWs((msg) => {
    if (msg.type === "engineer_reference_error") {
      setLapError(msg.data.message);
    }
  });
}, []);

useEffect(() => {
  if ((!showLapPicker && referenceMode !== "chase") || !trackName) return;

  let cancelled = false;
  setLoadingLaps(true);
  setLapError(null);

  api.laps(trackName)
    .then((laps) => {
      if (!cancelled) {
        setSavedLaps(
          laps.filter((lap) => lap.counts_for_best && lap.time_ms > 0)
        );
      }
    })
    .catch(() => {
      if (!cancelled) {
        setLapError("Could not load saved laps.");
      }
    })
    .finally(() => {
      if (!cancelled) setLoadingLaps(false);
    });

  return () => {
    cancelled = true;
  };
}, [showLapPicker, referenceMode, trackName, lapEpoch]);

function selectHistoricalLap(lapId: number) {
  setLapError(null);

  const sent = sendWs({
    type: "set_engineer_reference",
    data: { lap_id: lapId },
  });

  if (!sent) {
    setLapError("Not connected to the Race Engineer.");
    return;
  }

  setShowLapPicker(false);
}

  function selectSessionBest() {
    sendWs({
      type: "set_engineer_reference",
      data: { lap_id: null },
    });
  }

  return (
    <section className="rounded-md border border-edge bg-panel-2 p-3">
      <h3 className="mb-2 text-sm font-semibold">
        🎯 Coaching Reference
      </h3>

      <p className="mb-3 text-xs text-ink-dim">
        Circuit: {trackName || "Waiting for circuit identification"}
      </p>

      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          className={`btn rounded-md px-3 py-2 text-xs ${
            referenceMode === "session_best" ? "btn-primary" : ""
          }`}
          disabled={!wsConnected}
          onClick={selectSessionBest}
        >
          Session Best
        </button>

        <button
          type="button"
          className={`btn rounded-md px-3 py-2 text-xs ${
            referenceMode === "chase" ? "btn-primary" : ""
          }`}
          disabled={!wsConnected || !trackName}
		  onClick={() => setShowLapPicker((open) => !open)}
        >
          Chase a Reference
        </button>
      </div>

{showLapPicker && (
  <div className="mt-3 rounded-md border border-edge bg-panel p-2">
    <div className="mb-2 text-xs font-semibold">
      Saved laps at {trackName}
    </div>

    {loadingLaps && (
      <p className="text-xs text-ink-dim">Loading saved laps...</p>
    )}

    {!loadingLaps && savedLaps.length === 0 && !lapError && (
      <p className="text-xs text-ink-dim">
        No valid saved laps found for this circuit.
      </p>
    )}

    {!loadingLaps && savedLaps.length > 0 && (
      <div className="max-h-64 space-y-1 overflow-y-auto">
        {savedLaps.map((lap) => (
          <button
            key={lap.id}
            type="button"
            disabled={!wsConnected}
            onClick={() => selectHistoricalLap(lap.id)}
            className="flex w-full items-center justify-between gap-2 rounded-md border border-edge p-2 text-left text-xs hover:bg-panel-2 disabled:opacity-50"
          >
            <span className="min-w-0 truncate">
              {lap.car_name || `Car ${lap.car_id ?? "unknown"}`}
              {" · Lap "}
              {lap.number}
            </span>

            <span className="shrink-0 font-mono">
              {Math.floor(lap.time_ms / 60000)}:
              {String(Math.floor((lap.time_ms % 60000) / 1000)).padStart(2, "0")}.
              {String(lap.time_ms % 1000).padStart(3, "0")}
            </span>
          </button>
        ))}
      </div>
    )}
  </div>
)}

{lapError && (
  <p role="alert" className="mt-2 text-xs text-red-400">
    {lapError}
  </p>
)}

<div className="mt-3 rounded-md bg-panel p-3">
  <div className="text-[10px] uppercase tracking-widest text-ink-dim">
    Active Coaching Reference
  </div>

  <div className="mt-1 text-sm font-semibold">
    {referenceMode === "chase"
      ? selectedLap?.car_name || (
          referenceLapId === null
            ? "Your new fastest lap"
            : `Historical lap #${referenceLapId}`
        )
      : "Session Best"}
  </div>

  {referenceMode === "chase" && referenceTimeMs !== null && (
    <div className="mt-1 font-mono text-lg">
      {Math.floor(referenceTimeMs / 60000)}:
      {String(Math.floor((referenceTimeMs % 60000) / 1000)).padStart(2, "0")}.
      {String(referenceTimeMs % 1000).padStart(3, "0")}
    </div>
  )}

  <p className="mt-2 text-xs text-ink-dim">
    {referenceMode === "chase"
      ? referenceLapId === null
        ? "You have beaten your historical reference. Coaching now uses your faster lap."
        : "Coaching against your selected historical lap."
      : "Automatically coaching against your fastest valid session lap."}
  </p>
</div>
    </section>
  );
}