// Relative fuel map: what every fuel setting on the MFD would cost in pace and
// buy in range, from the reference lap's measured consumption.
//
// The panel fetches `/api/analysis/fuel?lap_id=…` itself, so the cells answer
// that one request from the committed fixture instead of a backend. Only the
// endpoint is stubbed — the component's own loading, error and empty paths all
// run for real, keyed on which lap id the cell asks for.

import { FuelMapPanel } from "gt7-datalogger-frontend";
import { FUEL, FUEL_LAP } from "../fixtures/app";
import { Panel, Surface } from "../preview-shell";

// Lap 940 is the Mount Panorama 911 GT3 R's best — the only fixture laps that
// burned any fuel, and the lap FUEL was actually computed for.
const MOUNT_PANORAMA = FUEL_LAP;
// Lap 1063 is the Tsukuba AE86's best: that session ran with fuel consumption
// off, so the backend answers with an empty row set.
const NO_CONSUMPTION = 1063;
// A lap whose samples never carried fuel at all — the endpoint 404s.
const MISSING = 1069;

const EMPTY = { fuel_level: 100, base_lap_ms: 58963, base_fuel_per_lap: 0, rows: [] };

const REPLIES: Record<string, { status: number; body: unknown } | "pending"> = {
  [String(MOUNT_PANORAMA)]: { status: 200, body: FUEL },
  [String(NO_CONSUMPTION)]: { status: 200, body: EMPTY },
  [String(MISSING)]: { status: 404, body: { detail: "no fuel samples on this lap" } },
};

const realFetch = globalThis.fetch;
globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
  );
  const match = /lap_id=(\d+)/.exec(url);
  if (!url.startsWith("/api/analysis/fuel") || !match) return realFetch(input, init);
  const reply = REPLIES[match[1]];
  if (reply === undefined || reply === "pending") return new Promise<Response>(() => {});
  return Promise.resolve(
    new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}) as typeof fetch;

export function Session() {
  // Eleven settings around the one that was driven. Row 0 is the measured
  // baseline, highlighted: 12.35 L a lap out of 98 L on board is just under
  // eight laps of Mount Panorama, and leaning off five clicks doubles that at
  // 1.25 s a lap.
  return (
    <Surface width={380}>
      <Panel title="Fuel strategy">
        <FuelMapPanel lapId={MOUNT_PANORAMA} />
      </Panel>
    </Surface>
  );
}

export function NoConsumptionOnTheLap() {
  // The Tsukuba session ran with fuel consumption switched off, so there is
  // nothing to extrapolate from and the panel says which lap is the problem.
  return (
    <Surface width={380}>
      <Panel title="Fuel strategy">
        <FuelMapPanel lapId={NO_CONSUMPTION} />
      </Panel>
    </Surface>
  );
}

export function NoFuelData() {
  // The request failed — a recording from before the fuel columns, or a lap
  // the server has no samples for.
  return (
    <Surface width={380}>
      <Panel title="Fuel strategy">
        <FuelMapPanel lapId={MISSING} />
      </Panel>
    </Surface>
  );
}
