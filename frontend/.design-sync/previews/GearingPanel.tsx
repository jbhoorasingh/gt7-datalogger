// Gearing for the reference lap: the ratio per gear with the speed each one
// reaches at the rev limiter, which is how ratio gaps and a too-short top gear
// are read at a glance.
//
// The panel fetches the lap itself (`/api/laps/<id>?samples=0`), so the cells
// answer that one request from the committed fixture rather than a backend —
// the component's loading, absent and populated paths all run for real, keyed
// on which lap id the cell asks for.
//
// `GEARING` is the one gearing payload recorded through the fixed decode path
// (top_speed is the broadcast calculated max speed, not the ratio that used to
// land there), so it is the only set that can back a correct card. There is no
// second post-fix car yet, hence no two-car contrast cell.

import { GearingPanel, type Units } from "gt7-datalogger-frontend";
import { GEARING, GEARING_LAP } from "../fixtures/analysis-full";
import { Panel, Surface } from "../preview-shell";

const LAP_WITHOUT_GEARING = 1069;

const REPLIES: Record<string, unknown> = {
  [String(GEARING_LAP)]: { id: GEARING_LAP, gearing: GEARING },
  [String(LAP_WITHOUT_GEARING)]: { id: LAP_WITHOUT_GEARING, gearing: null },
};

const realFetch = globalThis.fetch;
globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
  );
  const match = /^\/api\/laps\/(\d+)/.exec(url);
  if (!match) return realFetch(input, init);
  const body = REPLIES[match[1]];
  if (body === undefined) return new Promise<Response>(() => {});
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}) as typeof fetch;

const METRIC: Units = "metric";
const IMPERIAL: Units = "imperial";

function Card({ lapId, units }: { lapId: number; units: Units }) {
  return (
    <Surface width={330}>
      <Panel title="Gearing — reference lap">
        <GearingPanel lapId={lapId} units={units} />
      </Panel>
    </Surface>
  );
}

export function Session() {
  // Six ratios from 3.200 to 0.950 against a 9,000 rpm limiter and a 290 km/h
  // top speed. The redline column is the reading: 86 km/h in 1st, then steps
  // of 34, 33, 44 and 43 km/h — so 3rd to 4th is the widest gap in the box,
  // and 6th arrives exactly on the tune's top speed, as it must.
  return <Card lapId={GEARING_LAP} units={METRIC} />;
}

export function Imperial() {
  // Both speed columns follow the unit setting; the ratios are unitless.
  return <Card lapId={GEARING_LAP} units={IMPERIAL} />;
}

// The loading state is deliberately not a cell: `.skeleton` fills with
// `--color-panel`, which is the exact colour of the `.panel` it is placed
// inside, so the placeholder is invisible — in the capture and in the product
// alike. Recorded in the learnings as a component defect rather than faked
// here with a colour the design system does not use.

export function NoGearingData() {
  // Transmission metadata arrived with Tier 1. An older recording has none,
  // and the panel says why rather than drawing an empty table.
  return <Card lapId={LAP_WITHOUT_GEARING} units={METRIC} />;
}
