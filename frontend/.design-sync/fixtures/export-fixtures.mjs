// Exports the preview fixtures in .design-sync/fixtures/*.ts from a running
// backend, so the cards render real laps instead of invented numbers.
//
// To regenerate (scratch-DB recipe in .design-sync/NOTES.md):
//   GT7_DB_PATH=<scratch.db> GT7_SOURCE=sim GT7_CARS_CSV= \
//     backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8009
//   node .design-sync/fixtures/export-fixtures.mjs --port 8009 \
//     --analysis-session 228 --fuel-session 194 --sessions 194,228,232,239
//
// Three modules, split by weight: whatever a preview imports is inlined into
// its compiled bundle, so the 140 KB comparison payload must not ride along
// with a status bar. Numbers are rounded and position tracks decimated for the
// same reason. Host details (LAN address, database path) are replaced with
// placeholders — these files are committed to a public repository.

import { writeFileSync } from "node:fs";

const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i < 0 ? dflt : process.argv[i + 1];
};
const PORT = arg("port", "8009");
const ANALYSIS_SESSION = Number(arg("analysis-session", "228"));
const FUEL_SESSION = Number(arg("fuel-session", "194"));
// Pinned rather than "whatever the server has": a sim-mode backend records its
// own sessions while the exporter runs, and those land as empty rows.
const SESSION_IDS = arg("sessions", "194,228,232,239,280").split(",").map(Number);
const TRACK_POINTS = 300; // decimated from the 20 Hz position track
// A circuit whose survey bundle exists locally — the fuel/stint session's own.
const OUTLINE_TRACK = arg("outline-track", "Mount Panorama Motor Racing Circuit");
const GEARING_LAP = Number(arg("gearing-lap", "0")); // 0 = the analysis reference
// Everything the recording carries. The classic set is what the Analysis view
// opens with; the rest — per-wheel temperature, suspension travel and slip,
// the accelerometers, steering and the aid bits — is what the per-corner, g-g,
// playback and race-line components read. Requested at the SAME step as the
// classic module so a preview that switches between them sees one lap, sampled
// once.
const FULL_CHANNELS = [
  "speed", "throttle", "brake", "coast", "gear", "rpm", "boost", "tire_slip",
  "yaw_rate", "body_height", "acc_lat", "acc_long", "steer", "aids",
  "slip_fl", "slip_fr", "slip_rl", "slip_rr",
  "tt_fl", "tt_fr", "tt_rl", "tt_rr",
  "sus_fl", "sus_fr", "sus_rl", "sus_rr",
].join(",");

const api = async (path) => {
  const res = await fetch(`http://localhost:${PORT}${path}`);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
};

const round = (v, dp) => (typeof v === "number" ? Math.round(v * 10 ** dp) / 10 ** dp : v);
const DP = { dist: 1, t: 3, speed: 2, throttle: 1, brake: 1, coast: 1, gear: 0, rpm: 0, boost: 3,
  tire_slip: 3, yaw_rate: 3, pos_x: 2, pos_z: 2, delta_ms: 1, median: 2, deviation: 3 };
const roundSeries = (series) =>
  Object.fromEntries(Object.entries(series).map(([k, xs]) =>
    [k, Array.isArray(xs) ? xs.map((v) => round(v, DP[k] ?? 2)) : xs]));

function decimate(xs, n) {
  if (xs.length <= n) return xs;
  const stride = xs.length / n;
  return Array.from({ length: n }, (_, i) => xs[Math.round(i * stride)]);
}

const sessions = (await api("/api/sessions")).filter((s) => SESSION_IDS.includes(s.id));
const laps = await api(`/api/sessions/${ANALYSIS_SESSION}/laps`);
const fuelLaps = await api(`/api/sessions/${FUEL_SESSION}/laps`);
// `counts_for_best` matters: the quickest lap of a session can be an excluded
// one (an out-lap, a cut), and the app would never use it as a reference.
const counting = laps.filter((l) => l.time_ms > 0 && l.counts_for_best !== false);
const ref = counting.reduce((a, b) => (b.time_ms < a.time_ms ? b : a));
// Reference plus the next two quickest: the comparison the Analysis view opens.
const others = counting.filter((l) => l.id !== ref.id).sort((a, b) => a.time_ms - b.time_ms).slice(0, 2);
const selected = [ref.id, ...others.map((l) => l.id)];

const compare = await api(`/api/analysis/compare?laps=${selected.join(",")}&ref=${ref.id}`);
for (const entry of Object.values(compare.laps)) {
  entry.series = roundSeries(entry.series);
  if (entry.delta) entry.delta = roundSeries(entry.delta);
  if (entry.track) {
    entry.track = Object.fromEntries(
      Object.entries(entry.track).map(([k, xs]) => [k, decimate(xs, TRACK_POINTS).map((v) => round(v, DP[k] ?? 2))]),
    );
  }
}

const full = await api(
  `/api/analysis/compare?laps=${selected.join(",")}&ref=${ref.id}&channels=${FULL_CHANNELS}`,
);
for (const entry of Object.values(full.laps)) {
  entry.series = roundSeries(entry.series);
  if (entry.delta) entry.delta = roundSeries(entry.delta);
  if (entry.track) {
    entry.track = Object.fromEntries(
      Object.entries(entry.track).map(([k, xs]) => [k, decimate(xs, TRACK_POINTS).map((v) => round(v, DP[k] ?? 2))]),
    );
  }
}
// `gearing` lives on the lap detail, not on compare — GearingPanel reads it
// from there, so a preview cannot build one without this.
const gearingLap = GEARING_LAP || ref.id;
const gearing = (await api(`/api/laps/${gearingLap}?with_samples=false`)).gearing;

function bestFuelLapId() {
  return fuelLaps
    .filter((l) => l.time_ms > 0 && l.counts_for_best !== false)
    .reduce((a, b) => (b.time_ms < a.time_ms ? b : a)).id;
}
const bestFuelLap = fuelLaps.find((l) => l.id === bestFuelLapId());
const stats = await api("/api/admin/stats");
stats.lan_ip = "192.168.0.10";
stats.db.path = "/data/gt7.db";

const MODULES = {
  "analysis.ts": {
    blurb: `${laps[0]?.track_name} · ${laps[0]?.car_name} · the session's three quickest laps,\n// resampled by the backend against the best one.`,
    values: {
      COMPARE: ["CompareResult", compare],
      DEVIATION: ["DeviationResult", roundSeries(await api(`/api/analysis/deviation?session_id=${ANALYSIS_SESSION}&count=5`))],
      COACHING: ["CoachingNotes", await api(`/api/analysis/coaching?session_id=${ANALYSIS_SESSION}`)],
    },
    tail: [
      `/** The reference lap of COMPARE — the session's best. */`,
      `export const REF_LAP = ${ref.id};`,
      `/** Reference first, as the Analysis view selects them. */`,
      `export const SELECTED = ${JSON.stringify(selected)};`,
    ],
  },
  "app.ts": {
    blurb: `Sessions, laps, fuel and stints, plus the connection and admin state\n// the chrome reads.`,
    values: {
      SESSIONS: ["SessionSummary[]", sessions],
      LAPS: ["LapSummary[]", laps],
      FUEL_LAPS: ["LapSummary[]", fuelLaps],
      STINT: ["StintTrend", await api(`/api/analysis/stint?session_id=${FUEL_SESSION}`)],
      FUEL: ["FuelMapResult", await api(`/api/analysis/fuel?lap_id=${bestFuelLap.id}`)],
      STATUS: ["ConnectionStatus", await api("/api/status")],
      ADMIN_SETTINGS: ["AdminSettings", await api("/api/admin/settings")],
      ADMIN_SYNC: ["SyncStatus", await api("/api/admin/sync")],
      ADMIN_STATS: ["AdminStats", stats],
      RACE_ENGINEER: ["RaceEngineerDiagnostics", await api("/api/admin/race-engineer")],
      SURVEY_STATUS: ["SurveyStatus", await api("/api/survey/status")],
      // Fastest counting lap per circuit and car — what the bests board reads.
      PERSONAL_BESTS: ["PersonalBest[]", (await api("/api/laps/bests")).bests],
    },
    tail: [
      `/** The fuel session's best lap — the one FUEL was computed for. */`,
      `export const FUEL_LAP = ${bestFuelLap.id};`,
    ],
  },
  "analysis-full.ts": {
    blurb: `The same three ${laps[0]?.track_name} laps as analysis.ts, sampled the same way,\n// but carrying every channel the recording holds: per-wheel temperature,\n// suspension travel and slip, the accelerometers, steering and the aid bits.\n// Import this one for a per-corner, g-g, playback or race-line component; the\n// lighter analysis.ts is enough for everything else.`,
    values: {
      COMPARE_FULL: ["CompareResult", full],
      GEARING: ["LapGearing", gearing],
    },
    tail: [
      `/** GEARING's lap. Laps recorded before the calculated_max_speed fix`,
      `    store a ratio in top_speed, so this must come from a lap recorded`,
      `    after it — pass --gearing-lap. */`,
      `export const GEARING_LAP = ${gearingLap};`,
    ],
  },
  "tracks.ts": {
    blurb: `The track catalogue and survey coverage, for the Tracks view.`,
    values: {
      TRACK_CATALOG: ["TrackCatalog", await api("/api/track-catalog")],
      TRACK_OVERVIEW: ["TrackOverview", await api("/api/track-overview")],
      // The surveyed road for one circuit that has a bundle. Without it the
      // race line draws laps over empty space, and nothing exercises the
      // road-quad, kerb and off-track rendering at all.
      TRACK_OUTLINE: ["TrackOutline", await api(`/api/track-outline?track=${encodeURIComponent(OUTLINE_TRACK)}`)],
    },
    tail: [],
  },
};

// A partial re-export: `--modules analysis-full` rewrites only that file.
// Fixtures carry captured values (uptime, packet counts, session ids) that
// drift every run, and a preview's grade vouches for the numbers on its card —
// so a module nobody asked for must not be rewritten underneath one.
const ONLY = (arg("modules", "") || "").split(",").filter(Boolean);
for (const [file, mod] of Object.entries(MODULES)) {
  if (ONLY.length && !ONLY.includes(file.replace(/\.ts$/, ""))) continue;
  const types = [...new Set(Object.values(mod.values).map(([t]) => t.replace("[]", "")))].sort();
  const body = [
    `// Preview fixtures: real laps out of the datalogger's own database, captured`,
    `// through its own API. Regenerate with .design-sync/fixtures/export-fixtures.mjs.`,
    `//`,
    `// ${mod.blurb}`,
    ``,
    `import type {`,
    ...types.map((t) => `  ${t},`),
    `} from "gt7-datalogger-frontend";`,
    ``,
    ...Object.entries(mod.values).map(([k, [t, v]]) => `export const ${k}: ${t} = ${JSON.stringify(v)};\n`),
    ...mod.tail,
    ``,
  ].join("\n");
  writeFileSync(`.design-sync/fixtures/${file}`, body);
  console.error(`  ${file}: ${(body.length / 1024).toFixed(0)} KB`);
}
console.error(`fixtures: ${counting.length} laps, ${Object.keys(compare.laps).length} compared, ${sessions.length} sessions`);
