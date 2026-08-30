// Shared API/WebSocket types. Sample columns mirror backend SAMPLE_COLUMNS.

export interface LiveFrame {
  on_track: boolean;
  paused: boolean;
  speed_kmh: number;
  rpm: number;
  rpm_alert: number;
  gear: number;
  suggested_gear: number;
  throttle: number;
  brake: number;
  boost: number;
  fuel_level: number;
  fuel_capacity: number;
  current_lap: number;
  total_laps: number;
  best_lap_ms: number;
  last_lap_ms: number;
  position: number;
  total_positions: number;
  tire_temps: [number, number, number, number];
  tire_slip: number;
  water_temp: number;
  oil_temp: number;
  oil_pressure: number;
  aids: number; // AIDS_* bitmask
  surface: number; // packed per-wheel SURFACE_* codes; 0 = no data (pre-C format)
  car_id: number;
  car_name: string;
  session_best_ms: number;
  prev_best_ms: number; // session best BEFORE the latest lap (-1 if none)
  // Live gap vs the session-best lap; null whenever it's unavailable (no
  // reference lap yet, paused/off-track, or past the reference's last sample)
  delta_ms: number | null;
  lap_elapsed_ms: number; // time into the current lap (-1 before the first sample)
  pos_x: number;
  pos_z: number;
  tod_ms: number;
  track_name: string;
  // Steering wheel angle in radians as broadcast (packet B+); null when the
  // stream doesn't carry it. Positive = turning right in GT7's convention.
  steer_rad: number | null;
}

export interface ConnectionStatus {
  source: string;
  recording: boolean;
  session_id: number | null;
  track_name: string;
  connected: boolean;
  console_ip: string;
  packets_received: number;
  decode_errors: number;
  packet_format?: string;
  frames_dropped?: number; // console frames lost in transit (packet-id gaps)
}

export interface LapSummary {
  id: number;
  session_id: number;
  number: number;
  time_ms: number;
  finished_at?: string;
  car_id?: number;
  car_name?: string;
  car_category?: string; // packet C: "Gr.3", "Gr.4", "N300"…; "" when unknown
  // Read across the join from the lap's session, which is where the car's own
  // details live (#57) — they cannot vary between laps of one session.
  car_manufacturer?: string;
  car_year?: number;
  car_drivetrain?: string;
  car_aspiration?: string;
  track_name?: string; // from the session row; "" when the circuit is unnamed
  fuel_consumed: number;
  full_throttle_pct: number;
  full_brake_pct: number;
  coasting_pct: number;
  tire_spin_pct: number;
  max_speed: number;
  min_body_height: number;
  total_ticks?: number;
  tod_ms?: number;
  tcs_active_pct?: number;
  asm_active_pct?: number;
  max_water_temp?: number;
  max_oil_temp?: number;
  min_oil_pressure?: number; // -1 = unknown
  counts_for_best?: boolean; // false = partial lap (pit out-lap)
  off_track_count?: number; // excursions past track limits; -1 = unknown
  off_survey_count?: number; // excursions beyond the SURVEYED road edge; -1 = unknown
  clean_lap?: boolean | null; // null = unknown (no surface data recorded)
  // Recovered from a stream that ended without the lap-counter increment (a
  // replay ending); the time is GT7's own, verified against the integrated
  // clock (#26).
  salvaged?: boolean;
  // Race position when the lap completed (#60); -1 = no position reporting.
  race_position?: number;
  event_counts?: Record<string, number>;
}

// Driver-aids bitmask stored per tick in the "aids" sample column and sent in
// live frames. Mirrors backend AidsBits.
export const AIDS_TCS = 1;
export const AIDS_ASM = 2;
export const AIDS_HANDBRAKE = 4;
export const AIDS_REV_LIMITER = 8;

// Per-tick packed surface codes stored in the "surface" sample column:
// 4 bits per wheel, FL in the lowest nibble (FL | FR<<4 | RL<<8 | RR<<12).
// Mirrors backend app/processing/surface.py.
export const SURFACE_NONE = 0; // recorded without packet-C surface data
export const SURFACE_TARMAC = 1;
export const SURFACE_KERB = 2;
export const SURFACE_DIRT = 3;
export const SURFACE_GRASS = 4;
export const SURFACE_SAND = 5;
export const SURFACE_SNOW = 6;
export const SURFACE_OTHER = 7;

export function surfaceWheelCodes(v: number): [number, number, number, number] {
  return [v & 0xf, (v >> 4) & 0xf, (v >> 8) & 0xf, (v >> 12) & 0xf];
}

export function looseWheelCount(v: number): number {
  return surfaceWheelCodes(v).filter((c) => c >= SURFACE_DIRT && c <= SURFACE_SNOW).length;
}

export function kerbWheelCount(v: number): number {
  return surfaceWheelCodes(v).filter((c) => c === SURFACE_KERB).length;
}

// --- Surface survey (mirrors backend app/processing/survey.py) ---------------

export const SURVEY_WHEELS = ["FL", "FR", "RL", "RR"] as const;

// One per-wheel surface change, pushed over the WebSocket as it happens and
// kept in the survey status' `recent` ring.
export interface SurveyTransition {
  n: number;
  pid: number;
  session_id: number | null;
  lap: number;
  from: string; // 4 surface chars, FL FR RL RR
  to: string;
  changed: string[]; // wheels whose char flipped
  // Track border this contact belongs to, relative to travel direction:
  // set when one side touched kerb/loose while the other stayed on tarmac.
  border: "L" | "R" | null;
  // Manual marking kind active when this transition happened, if any.
  mark: string | null;
  pos: [number, number, number]; // x, y, z
  vel: [number, number, number];
  speed_mps: number;
  heading_rad: number | null; // null below ~3 m/s (velocity heading is noise)
  rotation: [number, number, number];
  rel_north: number;
  wheelbase_m: number | null;
  flags: number; // raw packet flags (undocumented-bit correlation)
  tw_m: number | null; // track width used for this record's contacts
  // Derived wheel-contact points [x, z]; null when heading was unavailable
  contacts: Record<string, [number, number]> | null;
}

// One border-edge point — the unit of "the track taking shape". kind
// "auto" comes from surface transitions; "straddle" is sampled continuously
// while one side's wheels are held off the tarmac (the border traces itself
// as you drive along it); "edge"/"runoff"/"wall" come from the driver
// holding a marking button while driving the boundary.
export type SurveyKind = "auto" | "straddle" | "edge" | "runoff" | "wall";

export interface SurveyEdge {
  x: number;
  z: number;
  hx: number; // travel-direction unit at the moment of evidence
  hz: number;
  side: "L" | "R";
  // One metre of border is one record; `kind` is what its votes settled on
  // (hand-marked kinds beat inferred ones — the surface chars cannot see a
  // wall or paved run-off). Resolved server-side, so consumers can just read
  // it; `votes` is the evidence behind it, as [count, last run] per kind PER
  // SOURCE — the installation id is what stops two people's run ordinals
  // being read as one fact when their bundles merge (#47).
  kind: SurveyKind;
  votes?: Partial<Record<SurveyKind, Record<string, [number, number]>>>;
  run?: number; // run ordinal that first evidenced this metre
  tw?: number | null; // axle track width in use when it was laid
}

// Start/finish line located from lap rollovers (GT7 increments current_lap
// exactly on the line). Provisional after one crossing; confident once
// repeat crossings land within meters of each other.
export interface SurveyFinish {
  x: number;
  z: number;
  hx: number; // travel direction across the line
  hz: number;
  crossings: number;
  spread_m: number;
  confident: boolean;
}

// Which official GT7 configuration a bundle is, once a human has confirmed
// it. Never inferred silently: GT7 broadcasts no track id and the catalog
// carries no world coordinates, so the match is a suggestion, not a lookup.
export interface OfficialMatch {
  track: string;
  layout: string;
  official_id: string;
  official_name: string;
  turns: number;
  length_m: number;
  reverse: boolean;
}

// The same shape as the server suggests it, with its reasoning attached.
export interface OfficialSuggestion extends OfficialMatch {
  confidence: number;
  why: string;
}

// A circuit's persisted survey bundle, as listed by /api/track-bundles.
export interface TrackBundleInfo {
  track: string;
  slug: string;
  runs: number;
  updated_at: string;
  points: number;
  finish_crossings: number;
  // Elevation only fills in by RE-DRIVING: bundles built before v3 sit near
  // 0 % until their metres are driven again.
  elevation_points: number;
  elevation_pct: number;
  manual_points: number;
  corners: number;
  sections: number;
  sources: number; // installations whose evidence is in this bundle
  official: OfficialMatch | null;
  source_runs: Record<string, number>;
  // Compiled fidelity score (#40): how much of the boundary the evidence
  // actually establishes. Absent when the bundle has not compiled.
  coverage?: TrackCoverage;
  compiled_at?: string;
}

// One hand-labelled corner. Anchored to a POSITION, not a lap distance —
// distance depends on the racing line taken (#48).
export interface AuthoredCorner {
  n: number;
  name: string;
  direction: "L" | "R" | null;
  apex: { x: number; z: number };
  entry: { x: number; z: number } | null;
  exit: { x: number; z: number } | null;
  note: string;
}

export interface AuthoredSection {
  n: number;
  name: string;
  start: { x: number; z: number };
  end: { x: number; z: number };
}

// A survey run's JSONL. `orphaned` = it gathered evidence and never reached a
// circuit, so it saved no bundle at all and exists only as this file (#45).
export interface SurveyLog {
  name: string;
  track: string;
  started_at: string;
  session_id: number | null;
  track_width_m: number | null;
  marks: number;
  transitions: number;
  finish_crossings: number;
  bytes: number;
  orphaned: boolean;
}

// One circuit as the management view sees it: the three sources of track
// knowledge joined, so the rows where they disagree are visible (#46).
export interface TrackOverviewRow {
  slug: string;
  name: string;
  named: boolean; // in the DB tracks table -> auto-identification works
  // Which kind of signature names it: "user" if somebody typed it here,
  // "seed" if the build shipped it (#58). Both auto-identify; only one is
  // this installation's own knowledge, and a seeded row is the weaker claim
  // — it is a bounding box computed from somebody else's lap.
  provenance: "user" | "seed" | null;
  track_id: number | null;
  length_m: number | null;
  bundle: TrackBundleInfo | null;
  sessions: number;
  official: OfficialMatch | null;
  suggestion: OfficialSuggestion | null;
}

export interface TrackOverview {
  source: string; // this installation's id
  tracks: TrackOverviewRow[];
  logs: SurveyLog[];
  catalog_configs: number;
  // Shipped signatures held but not listed in `tracks` — circuits that will
  // name themselves the first time they are driven (#58). They are kept out
  // of the table so 77 undriven rows cannot bury the ones that mean
  // something, and reported here so their absence is not read as a gap.
  seeded_signatures: number;
}

export interface SurveyStatus {
  active: boolean;
  started_at: string | null;
  track: string; // circuit label; picked at start or auto-identified mid-run
  session_id: number | null; // session the transitions belong to
  track_width_m: number; // the assumption entered at start
  width_estimate_m: number | null; // median of measured edge crossings
  width_samples: number; // accepted crossing measurements so far
  width_in_use_m: number; // what contact derivation actually uses
  // Axle track measured from cornering — every corner is a sample, so this
  // normally settles long before a deliberate edge ride produces anything.
  width_source: "cornering" | "car-memory" | "edge-ride" | "assumed";
  car_id: number | null;
  remembered_width_m: number | null; // measured for this car on an earlier run
  yaw_width_m: number | null;
  yaw_samples: number;
  yaw_needed: number;
  yaw_rejects: Partial<Record<
    "slow" | "straight" | "on_pedals" | "slip" | "implausible", number
  >>;
  trail_points: number;
  trail_epoch: number;
  edge_points: number;
  edges_epoch: number;
  finish: SurveyFinish | null;
  // Persistent per-circuit knowledge this run resumed from / saves into
  // (data/track-bundles/<slug>.json). null = fresh circuit.
  bundle: { track: string; runs: number; updated_at: string; points: number } | null;
  mark_side: "L" | "R" | null; // manual boundary marking armed on this side
  mark_kind: "edge" | "runoff" | "wall";
  packets: number;
  no_surface_packets: number;
  transitions: number;
  histogram: Record<string, Record<string, number>>; // wheel -> char -> count
  chars_seen: string[];
  known_chars: string[];
  unknown_chars: Record<string, number>;
  // Undocumented packet-flag bits (12–15) seen active: bit -> tick count.
  // GT7 has no known track-limits field; one ever activating is a finding.
  unknown_flag_bits: Record<string, number>;
  recent: SurveyTransition[];
  log_path: string | null;
}

export type EventType = "lockup" | "wheelspin" | "bottoming" | "kerb";

export interface LapEvent {
  type: EventType;
  start_dist: number;
  end_dist: number;
  wheels: string[]; // "fl" | "fr" | "rl" | "rr"
  severity: number;
}

// Static per-lap gearing metadata (present when the recording backend saw it)
export interface LapGearing {
  ratios: number[];
  top_speed: number;
  rpm_alert: number;
}

export interface SessionSummary {
  id: number;
  started_at: string;
  car_id: number;
  car_name: string;
  car_category: string; // packet C: "Gr.3", "Gr.4", "N300"...; "" when unknown
  // From the bundled car inventory, denormalised onto the session row (#57).
  // Empty (or 0 for the year) wherever GT7's own car list has no answer —
  // race cars and concepts routinely carry no model year.
  car_manufacturer: string;
  car_year: number;
  car_drivetrain: string; // "FR", "FF", "MR", "RR", "4WD"
  car_aspiration: string; // "NA", "TC", "SC", "TC+SC", "EV"
  car_full_name: string; // "Nissan Skyline GTS-R (R31) '87"; car_name is the short form
  // The published figures, in the source's own units. 0 = not published, which
  // is meaningful rather than missing: an EV has no displacement.
  car_displacement_cc: number;
  car_power_bhp: number;
  car_torque_kgfm: number;
  car_weight_kg: number;
  car_length_mm: number;
  car_width_mm: number;
  car_height_mm: number;
  car_performance_points: number;
  note: string;
  // User-set labels ("wet", "race sim") for telling sessions apart and
  // filtering the list (#25). Never inferred from telemetry.
  tags: string[];
  track_name: string;
  lap_count: number;
  best_lap_time_ms: number | null;
  // Manually kept off the Bests board (#26): a replay recording or another
  // driver's stint is indistinguishable from own driving in telemetry, so
  // only a human can rule its laps out as personal bests.
  bests_excluded: boolean;
  // The race result (#60), written at the checkered-flag edge. -1 = no
  // result: a time trial, or a stream that ended mid-race — deliberately
  // distinct from finishing last (>= 2).
  final_position: number;
  final_total_positions: number;
  race_laps: number; // race distance in laps; 0 = not a lapped race
  // Sum of the stored lap times; null unless every lap 1..race_laps is
  // accounted for (a sum across a missing lap would be confidently wrong).
  race_time_ms: number | null;
}

export interface Track {
  id: number;
  name: string;
  length_m: number;
  created_at: string;
}

// Fastest full lap at one circuit in one car category — the reference a lap
// is worth being compared against, since a Gr.3 time and an N100 time around
// the same corners are not the same achievement (#19).
export interface CategoryBest {
  lap_id: number;
  session_id: number;
  number: number;
  time_ms: number;
  car_id: number;
  car_name: string;
  car_category: string;
  track_name: string;
  clean_lap: boolean | null;
  off_survey_count?: number; // excursions beyond the SURVEYED road edge; -1 = unknown
  finished_at: string;
}

// One row of the personal-bests board (#26): the fastest counting lap ever
// recorded for one (circuit, car) pair. Partial laps, unnamed circuits and
// bests_excluded sessions never appear — the board only states times that
// were really driven start to finish on a known track.
export interface PersonalBest {
  track_name: string;
  car_id: number;
  car_name: string;
  car_category: string; // "" when unknown (pre-packet-C recording)
  lap_id: number;
  session_id: number;
  number: number;
  time_ms: number;
  finished_at: string;
  clean_lap: boolean | null; // null = unknown (no surface data recorded)
  off_survey_count: number; // excursions beyond the SURVEYED road edge; -1 = unknown
  salvaged: boolean; // recovered from a replay-style stream ending (#26)
  lap_count: number; // counting laps recorded for this (circuit, car) pair
}

// Official GT7 track/layout metadata (bundled data/tracks.json — only the
// fields the UI reads; the endpoint returns more).
export interface TrackCatalog {
  tracks: {
    name: string;
    layouts: {
      name: string;
      official_name: string;
      length_m: number;
      reverse: { official_id: string; turns: number } | null;
    }[];
  }[];
}

export type Samples = Record<string, number[]>;

export interface PeakValley {
  dist: number;
  speed: number;
  x: number;
  z: number;
}

// A corner on the reference lap, numbered from the start line. Detected from
// this lap's curvature, unless the circuit has authored corners in its
// bundle — those outrank detection and keep their numbering across laps and
// sessions (`authored`, and they may carry a name).
export interface Corner {
  n: number;
  apex_dist: number;
  apex_x: number;
  apex_z: number;
  entry_dist: number;
  exit_dist: number;
  direction: "L" | "R";
  min_speed: number;
  angle_deg: number;
  name?: string;
  authored?: boolean;
}

// One accelerometer axis, checked against physics the recording already
// carries (lateral vs v·ω from the driven path, longitudinal vs dv/dt).
// `slope` is broadcast units per m/s²; `g_per_unit` is what the UI multiplies
// the raw channel by to get g — signed, so a channel that counts the other way
// comes out upright. Mirrors backend analysis.accel_calibration (#16).
export interface AccelAxis {
  slope: number;
  r2: number; // share of the channel the scaled reference explains (about zero)
  samples: number;
  fitted: boolean; // false = too weak to trust; g_per_unit assumes m/s²
  g_per_unit: number;
}

export interface AccelCalibration {
  available: boolean; // false on recordings without packet-B accelerometer
  lateral?: AccelAxis;
  longitudinal?: AccelAxis;
  unit?: string; // "m/s^2" | "g" | "unverified" | "1 unit = … m/s^2"
}

// Corners of the traction circle a lap actually reached, in g.
export interface GGExtremes {
  lat_right: number;
  lat_left: number;
  accel: number;
  braking: number;
}

// One corner of the report card (#21): this lap measured through the
// REFERENCE lap's corner window, so time-through is comparable across laps.
// A corner the lap never fully drove is absent from its report.
export interface CornerReportRow {
  n: number;
  entry_speed: number;
  min_speed: number;
  exit_speed: number;
  time_ms: number;
}

export interface CompareLapEntry {
  series: Samples & { dist: number[] };
  peaks_valleys: { peaks: PeakValley[]; valleys: PeakValley[] };
  events?: LapEvent[];
  delta?: { dist: number[]; delta_ms: number[] };
  corners?: Corner[]; // reference lap only
  corner_report?: CornerReportRow[]; // every lap, on the reference's corners
  gg?: GGExtremes; // peaks from the raw ticks, not the resampled series
}

export interface CompareResult {
  ref: number;
  step: number;
  accel: AccelCalibration;
  laps: Record<string, CompareLapEntry>;
}

// One post-lap coaching note (#23) — the race engineer's finding, in the
// exact wording voice would have used. `corner` is set when it names one.
export interface CoachingFinding {
  type: string;
  text: string;
  corner: number | null;
}

export interface CoachingLapNotes {
  lap_id: number;
  number: number;
  findings: CoachingFinding[];
}

export interface CoachingNotes {
  session_id: number;
  laps: CoachingLapNotes[];
}

// One border's coverage, measured against the compiled boundary itself (#38):
// surveyed metres over total boundary metres, gaps in the denominator.
export interface SideCoverage {
  surveyed_m: number;
  gap_m: number;
  pct: number;
  closed: boolean; // the border forms a complete loop
}

export interface TrackCoverage {
  L: SideCoverage;
  R: SideCoverage;
  road_pct: number; // share of surveyed border with the road resolved across
}

// The surveyed road under a lap, compiled server-side from the circuit's
// track bundle (#51). Empty when the circuit has never been surveyed.
export interface TrackOutline {
  track: string;
  slug: string | null;
  // Road surface as quads [x1,z1,x2,z2,x3,z3,x4,z4], one per paired metre of
  // left/right border — order-free, so no centerline has to be reconstructed.
  road: number[][];
  // Border evidence as short segments [x, z, hx, hz] along travel direction,
  // split by what the votes settled on.
  edges: number[][];
  walls: number[][];
  // Start/finish line as [x1, z1, x2, z2]; null until a survey located it.
  finish: number[] | null;
  runs: number;
  updated_at: string;
  // Survey holes in the compiled borders as [x1,z1,x2,z2] spans (#44) —
  // stretches of boundary the ordering knows about but nobody has driven.
  // Absent/empty on the legacy fallback pathway.
  gaps?: number[][];
  coverage?: TrackCoverage | null;
}

export interface DeviationResult {
  dist: number[];
  median: number[];
  deviation: number[];
  lap_ids: number[];
}

export interface FuelMapRow {
  setting: number;
  fuel_per_lap: number;
  laps_remaining: number;
  time_remaining_ms: number;
  lap_time_delta_ms: number;
}

export interface FuelMapResult {
  fuel_level: number;
  base_lap_ms: number;
  base_fuel_per_lap: number;
  rows: FuelMapRow[];
}

export interface AdminSettings {
  ps_ip: string;
  source: "udp" | "sim";
  log_level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  ws_rate: number;
  heartbeat_port: number;
  telemetry_port: number;
  webhook_url: string;
  webhook_events: WebhookEvent[];
  packet_format: "A" | "B" | "~" | "C";
  race_engineer: boolean;
  race_engineer_verbosity: Verbosity;
  race_engineer_categories: CalloutCategory[];
  race_engineer_units: SpokenUnits;
}

// Units spoken inside callout text ("eighteen meters" / "fifty-nine feet").
// Server-side because the text is worded before it reaches a browser — the
// dashboard's own km/h-vs-mph toggle only affects what is displayed.
export type SpokenUnits = "metric" | "imperial";

// --- Race Engineer (mirrors backend app/race_engineer/models.py) ------------

export const CALLOUT_CATEGORIES = [
  "system",
  "lap",
  "pace",
  "race",
  "position",
  "fuel",
  "strategy",
  "engine",
  "tires",
  "chassis",
  "coaching",
] as const;

export type CalloutCategory = (typeof CALLOUT_CATEGORIES)[number];
export type Verbosity = "minimal" | "race" | "coach";

const MINIMAL_CATEGORIES: CalloutCategory[] = ["system", "engine", "strategy", "race"];

// Verbosity is a preset over the same categories (mirrors the backend). The
// server applies its own setting as a ceiling — a browser can only narrow it.
export const VERBOSITY_CATEGORIES: Record<Verbosity, CalloutCategory[]> = {
  minimal: MINIMAL_CATEGORIES,
  race: [...MINIMAL_CATEGORIES, "lap", "pace", "position", "fuel"],
  coach: [...CALLOUT_CATEGORIES],
};

export interface VoiceCallout {
  id: string;
  event_type: string;
  text: string;
  category: CalloutCategory;
  priority: number; // 0..100; 90+ may interrupt
  created_at_ms: number; // server clock — display/debug only
  expires_at_ms: number; // server clock — display/debug only
  // Lifetime measured from the moment the browser receives it. Server and
  // browser clocks disagree (phones, Pi without NTP), so expiry uses this.
  ttl_ms: number;
  interrupt: boolean;
  dedupe_key?: string | null;
  message_key?: string;
  message_args?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface VoiceClientInfo {
  client_id: string;
  page: string;
  voice_supported: boolean;
  voice_enabled: boolean;
  is_active_speaker: boolean;
}

export interface RaceEngineerStatus {
  enabled: boolean; // feature switched on server-side
  active: boolean; // ...and at least one browser has voice enabled
  verbosity: Verbosity;
  categories: CalloutCategory[]; // categories the server will emit
  // Whether enough laps agree on the track distance for lap-vs-lap coaching
  // to mean anything. Explains a quiet coaching category early in a session.
  coaching_ready: boolean;
  active_client_id: string;
  clients: VoiceClientInfo[];
}

export interface RaceEngineerDiagnostics extends RaceEngineerStatus {
  units: SpokenUnits;
  session_id: number | null;
  lap_history: number;
  best_lap_ms: number | null;
  corners: number;
  effective_categories: CalloutCategory[];
  stats: Record<string, number>;
  acks: Record<string, number>;
  /** Why the browser last failed to speak, in the engine's own words. */
  last_ack_reason: string;
  last_callout: VoiceCallout | null;
}

export type CalloutAckStatus =
  | "spoken"
  | "expired"
  // Stopped on purpose — by a critical callout, a disconnect, or the user
  // pressing Test voice. Not a failure, and never treated as one.
  | "interrupted"
  | "duplicate"
  | "disabled"
  | "category_disabled"
  | "not_active_speaker"
  | "speech_error";

// Browser -> server. The only client-to-server protocol on /ws/live; older
// pages send nothing and the server ignores anything it can't parse.
export type ClientMessage =
  | {
      type: "client_capabilities";
      data: {
        client_id: string;
        page: string;
        voice_supported: boolean;
        voice_enabled: boolean;
      };
    }
  | { type: "claim_voice_output"; data: { client_id: string } }
  | { type: "release_voice_output"; data: { client_id: string } }
  | {
      type: "voice_callout_ack";
      data: {
        callout_id: string;
        client_id: string;
        status: CalloutAckStatus;
        spoken_at_ms: number;
        /** Engine reason when status is speech_error — the only clue a
         *  silent setup leaves on the server. */
        reason?: string;
      };
    };

export type WebhookEvent =
  | "personal_best"
  | "session_summary"
  | "overtake"
  | "position_lost"
  | "off_road";

export interface LogRecord {
  ts: string;
  level: string;
  logger: string;
  message: string;
}

export interface AdminStats {
  uptime_s: number;
  db: { sessions: number; laps: number; size_bytes: number; path: string };
  cars_loaded: number;
  source: ConnectionStatus;
  clients: number;
  lan_ip: string;
  http_port: number;
}

export type WsMessage =
  | { type: "telemetry"; data: LiveFrame }
  | { type: "lap"; data: LapSummary }
  | { type: "status"; data: ConnectionStatus }
  | { type: "session"; data: ConnectionStatus }
  | { type: "survey"; data: SurveyTransition }
  | { type: "voice_callout"; data: VoiceCallout }
  | { type: "voice_output_status"; data: { active_client_id: string } }
  | { type: "race_engineer_status"; data: RaceEngineerStatus };
