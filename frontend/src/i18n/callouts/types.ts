// The arguments each Race Engineer callout carries (VoiceCallout.message_args),
// by message key. The backend's detectors and manager are the source; this
// mirrors them so a phrasebook is type-checked against what is actually sent.
//
// The rule both sides keep: the arguments describe the English sentence. A
// clause the server left out (a gap too small to mention, a brake-point
// difference under the noise floor) arrives as null, so a translation leaves
// it out too — no phrasebook re-decides what is worth saying.

export type Units = "metric" | "imperial";
export type Rounding = "round" | "truncate";
export type Places = 1 | 2 | 3;
export type Wheel = "fl" | "fr" | "rl" | "rr";

/** Sector-voice precision, present on lap calls only when it applies. */
interface Precision {
  places?: Places;
  rounding?: Rounding;
}

interface Temperature {
  celsius: number;
}

interface Laps {
  laps: number;
}

interface Braking {
  corner: number;
  /** A hand-labelled name (#48), or "" — the name wins when there is one. */
  corner_name: string;
  meters: number;
  units: Units;
}

interface Repeated {
  wheel: Wheel;
  /** null when no corner is near: "the next braking zone", "corner exit". */
  corner: number | null;
  corner_name: string;
  count: number;
}

interface Position {
  position: number;
  previous: number;
}

export interface CalloutArgs {
  /** Browser-only: the phrase spoken by Enable / Test voice. */
  "system.enabled": Record<string, never>;
  "system.test": { event_type?: string };
  "lap.time": Precision & { time_ms: number; delta_ms: number | null };
  "lap.personal_best": Precision & { time_ms: number; gain_ms: number | null };
  "sector.time": {
    sector: number;
    time_ms: number;
    delta_ms: number;
    content: "time" | "delta" | "both";
    /** individual: this sector against the reference's; cumulative: the lap so far. */
    comparison: "individual" | "cumulative";
    places: Places;
    rounding: Rounding;
    /** The delta rounds to zero at this precision. */
    level: boolean;
  };
  "pace.drop": { gap_ms: number; laps: number };
  "race.final_lap": { lap: number };
  "race.halfway": { lap: number; total_laps: number };
  "position.gained": Position;
  "position.lost": Position;
  "fuel.remaining": Laps;
  "fuel.low": Laps;
  "fuel.critical": Laps;
  "fuel.shortfall": Laps;
  "strategy.pit_window_next": { lap: number };
  "strategy.pit_window_open": { lap: number };
  "engine.water_temp_high": Temperature;
  "engine.water_temp_critical": Temperature;
  "engine.oil_temp_high": Temperature;
  "engine.oil_temp_critical": Temperature;
  "engine.oil_pressure_low": { bar: number };
  "tires.temp_high": Temperature;
  "tires.imbalance": {
    axis: "front_rear" | "left_right";
    hotter: "front" | "rear" | "left" | "right";
    degrees: number;
  };
  "coaching.braking_early": Braking;
  "coaching.braking_late": Braking;
  "coaching.repeated_lockups": Repeated;
  "coaching.repeated_wheelspin": Repeated;
  "coaching.repeated_bottoming": Repeated;
  "coaching.corner_time_loss": {
    corner: number;
    corner_name: string;
    loss_ms: number;
    /** Negative: braked earlier than the reference. null: not mentioned. */
    brake_delta_m: number | null;
    /** Negative: slower at the apex. null: not mentioned. */
    apex_delta_kmh: number | null;
    units: Units;
  };
}

export type CalloutKey = keyof CalloutArgs;

/** One language's wording for every callout. */
export type Phrasebook = { [K in CalloutKey]: (args: CalloutArgs[K]) => string };
