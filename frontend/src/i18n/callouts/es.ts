// Spanish callouts. Neutral Spanish: understood in Spain and Latin America,
// and avoiding words that differ between them (coche/carro/auto).

import { decimal, feet, fixed, mph, pluralOf, quantize, spokenGap, splitTime } from "./numbers";
import type { CalloutArgs, Phrasebook, Places, Rounding, Units, Wheel } from "./types";

const L = "es";

/** "1" before a noun reads as "uno" on some voices; say "un"/"una". */
function count(n: number, one: string, many: string, feminine = false): string {
  if (n === 1) return `${feminine ? "una" : "un"} ${one}`;
  return `${decimal(L, n)} ${pluralOf(L, n) === "one" ? one : many}`;
}

function cap(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function lapTime(ms: number, places: Places = 1, rounding: Rounding = "round"): string {
  const t = splitTime(ms, places, rounding);
  const secs = `${t.seconds},${t.fraction}`;
  if (t.minutes === 0) return `${secs} segundos`;
  return `${count(t.minutes, "minuto", "minutos")} ${secs}`;
}

/** A gap as a driver hears it: "tres décimas", "1,2 segundos". */
function gap(ms: number, places?: Places, rounding: Rounding = "round"): string {
  if (places) return `${fixed(L, quantize(ms, places, rounding), places)} segundos`;
  const g = spokenGap(ms);
  return g.unit === "tenths"
    ? count(g.value, "décima", "décimas", true)
    : count(g.value, "segundo", "segundos");
}

function distance(meters: number, units: Units): string {
  return units === "imperial"
    ? count(feet(meters), "pie", "pies")
    : count(Math.round(meters), "metro", "metros");
}

function speed(kmh: number, units: Units): string {
  return units === "imperial"
    ? `${count(mph(kmh), "milla", "millas", true)} por hora`
    : `${count(Math.round(kmh), "kilómetro", "kilómetros")} por hora`;
}

function laps(n: number): string {
  return count(Math.round(n * 10) / 10, "vuelta", "vueltas", true);
}

/** "la curva 4" / "Parabolica" / fallback when no corner is known. */
function corner(n: number | null, name: string, unknown = "la próxima zona de frenada"): string {
  if (name) return name;
  return n ? `la curva ${n}` : unknown;
}

const WHEEL: Record<Wheel, string> = {
  fl: "delantera izquierda",
  fr: "delantera derecha",
  rl: "trasera izquierda",
  rr: "trasera derecha",
};

function braking(a: CalloutArgs["coaching.braking_early"], when: string): string {
  return `Estás frenando unos ${distance(a.meters, a.units)} ${when} en ${corner(a.corner, a.corner_name)}.`;
}

export const es: Phrasebook = {
  "system.enabled": () => "Ingeniero de carrera activado.",
  "system.test": () => "Mensaje de prueba del ingeniero de carrera.",

  "lap.time": (a) => {
    let text = `Tiempo de vuelta, ${lapTime(a.time_ms, a.places, a.rounding)}.`;
    if (a.delta_ms != null) {
      text += ` ${cap(gap(a.delta_ms, a.places, a.rounding))} más ${a.delta_ms > 0 ? "lento" : "rápido"}.`;
    }
    return text;
  },
  "lap.personal_best": (a) => {
    let text = `Nuevo mejor tiempo personal, ${lapTime(a.time_ms, a.places, a.rounding)}.`;
    if (a.gain_ms != null) text += ` ${cap(gap(a.gain_ms, a.places, a.rounding))} más rápido.`;
    return text;
  },
  "sector.time": (a) => {
    const phrases: string[] = [];
    if (a.content !== "delta") {
      const t = splitTime(a.time_ms, a.places, a.rounding);
      const secs = `${t.seconds},${t.fraction} segundos`;
      phrases.push(t.minutes ? `${count(t.minutes, "minuto", "minutos")} ${secs}` : secs);
    }
    if (a.content !== "time") {
      if (a.level) phrases.push("igualado con la referencia");
      else {
        const ahead = a.delta_ms < 0;
        const direction =
          a.comparison === "cumulative"
            ? ahead ? "por delante" : "por detrás"
            : ahead ? "más rápido" : "más lento";
        phrases.push(`${gap(a.delta_ms, a.places, a.rounding)} ${direction}`);
      }
    }
    return `Sector ${a.sector}, ${phrases.join(", ")}.`;
  },

  "pace.drop": (a) => `Tu ritmo está bajando, a ${gap(a.gap_ms)} de tu mejor vuelta.`,

  "race.final_lap": () => "Última vuelta.",
  "race.halfway": () => "Mitad de carrera.",

  "position.gained": (a) => `Posición ganada. Ahora vas en la posición ${a.position}.`,
  "position.lost": (a) => `Posición perdida. Ahora vas en la posición ${a.position}.`,

  "fuel.remaining": (a) => `Combustible restante, ${laps(a.laps)}.`,
  "fuel.low": (a) => `Combustible bajo, quedan ${laps(a.laps)}.`,
  "fuel.critical": (a) => `Combustible crítico, quedan ${laps(a.laps)}.`,
  "fuel.shortfall": (a) => `El combustible no alcanza, faltan ${laps(a.laps)}.`,
  "strategy.pit_window_next": () => "La ventana de boxes se abre en la próxima vuelta.",
  "strategy.pit_window_open": () => "La ventana de boxes está abierta.",

  "engine.water_temp_high": () => "Temperatura del agua alta.",
  "engine.water_temp_critical": () => "Temperatura del agua crítica.",
  "engine.oil_temp_high": () => "Temperatura del aceite alta.",
  "engine.oil_temp_critical": () => "Temperatura del aceite crítica.",
  "engine.oil_pressure_low": () => "Presión de aceite baja.",

  "tires.temp_high": () => "Temperatura de neumáticos alta.",
  "tires.imbalance": (a) => {
    const pair = {
      front: ["delanteros", "traseros"],
      rear: ["traseros", "delanteros"],
      left: ["izquierdos", "derechos"],
      right: ["derechos", "izquierdos"],
    }[a.hotter];
    return (
      `Los neumáticos ${pair[0]} están ${count(a.degrees, "grado", "grados")} ` +
      `más calientes que los ${pair[1]}.`
    );
  },

  "coaching.braking_early": (a) => braking(a, "antes"),
  "coaching.braking_late": (a) => braking(a, "más tarde"),
  "coaching.repeated_lockups": (a) =>
    `Bloqueos repetidos de la rueda ${WHEEL[a.wheel]} en ${corner(a.corner, a.corner_name)}.`,
  "coaching.repeated_wheelspin": (a) =>
    a.corner || a.corner_name
      ? `Pérdida de tracción repetida a la salida de ${corner(a.corner, a.corner_name)}.`
      : "Pérdida de tracción repetida a la salida de las curvas.",
  "coaching.repeated_bottoming": (a) =>
    `Estás tocando fondo en ${corner(a.corner, a.corner_name)}.`,
  "coaching.corner_time_loss": (a) => {
    const where = corner(a.corner, a.corner_name);
    const clauses: string[] = [];
    if (a.brake_delta_m != null) {
      const when = a.brake_delta_m < 0 ? "antes" : "más tarde";
      clauses.push(`frenaste ${distance(Math.abs(a.brake_delta_m), a.units)} ${when}`);
    }
    if (a.apex_delta_kmh != null) {
      const how = a.apex_delta_kmh < 0 ? "más lento" : "más rápido";
      clauses.push(`pasaste por el vértice ${speed(Math.abs(a.apex_delta_kmh), a.units)} ${how}`);
    }
    if (clauses.length === 0) {
      return `Donde más tiempo perdiste fue en ${where}. Fuiste ${gap(a.loss_ms)} más lento.`;
    }
    return `Perdiste ${gap(a.loss_ms)} en ${where}. ${cap(clauses.join(" y "))}.`;
  },
};
