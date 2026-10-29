// Brazilian Portuguese callouts. Plurals follow CLDR (and the Brazilian
// norm): a quantity under two takes the singular, "1,5 volta".

import { decimal, feet, fixed, mph, pluralOf, quantize, spokenGap, splitTime } from "./numbers";
import type { CalloutArgs, Phrasebook, Places, Rounding, Units, Wheel } from "./types";

const L = "pt-BR";

/** "1" before a noun reads as "um" whatever its gender; say "uma" for one. */
function count(n: number, one: string, many: string, feminine = false): string {
  if (n === 1) return `${feminine ? "uma" : "um"} ${one}`;
  return `${decimal(L, n)} ${pluralOf(L, n) === "one" ? one : many}`;
}

function cap(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function seconds(value: string, total: number): string {
  return `${value} ${pluralOf(L, total) === "one" ? "segundo" : "segundos"}`;
}

function lapTime(ms: number, places: Places = 1, rounding: Rounding = "round"): string {
  const t = splitTime(ms, places, rounding);
  const secs = `${t.seconds},${t.fraction}`;
  if (t.minutes === 0) return seconds(secs, t.total);
  return `${count(t.minutes, "minuto", "minutos")} e ${secs}`;
}

/** A gap as a driver hears it: "três décimos", "1,2 segundo". */
function gap(ms: number, places?: Places, rounding: Rounding = "round"): string {
  if (places) {
    const value = quantize(ms, places, rounding);
    return seconds(fixed(L, value, places), value);
  }
  const g = spokenGap(ms);
  return g.unit === "tenths"
    ? count(g.value, "décimo", "décimos")
    : count(g.value, "segundo", "segundos");
}

function distance(meters: number, units: Units): string {
  return units === "imperial"
    ? count(feet(meters), "pé", "pés")
    : count(Math.round(meters), "metro", "metros");
}

function speed(kmh: number, units: Units): string {
  return units === "imperial"
    ? `${count(mph(kmh), "milha", "milhas", true)} por hora`
    : `${count(Math.round(kmh), "quilômetro", "quilômetros")} por hora`;
}

function laps(n: number): string {
  return count(Math.round(n * 10) / 10, "volta", "voltas", true);
}

/** "na curva 4" / "na Parabolica" — the preposition comes with it. */
function inCorner(n: number | null, name: string, unknown = "na próxima zona de frenagem"): string {
  if (name) return `em ${name}`;
  return n ? `na curva ${n}` : unknown;
}

function ofCorner(n: number | null, name: string): string {
  if (name) return `de ${name}`;
  return `da curva ${n}`;
}

const WHEEL: Record<Wheel, string> = {
  fl: "dianteira esquerda",
  fr: "dianteira direita",
  rl: "traseira esquerda",
  rr: "traseira direita",
};

function braking(a: CalloutArgs["coaching.braking_early"], when: string): string {
  return `Você está freando cerca de ${distance(a.meters, a.units)} ${when} ${inCorner(a.corner, a.corner_name)}.`;
}

export const ptBR: Phrasebook = {
  "system.enabled": () => "Engenheiro de corrida ativado.",
  "system.test": () => "Mensagem de teste do engenheiro de corrida.",

  "lap.time": (a) => {
    let text = `Tempo de volta, ${lapTime(a.time_ms, a.places, a.rounding)}.`;
    if (a.delta_ms != null) {
      text += ` ${cap(gap(a.delta_ms, a.places, a.rounding))} mais ${a.delta_ms > 0 ? "lento" : "rápido"}.`;
    }
    return text;
  },
  "lap.personal_best": (a) => {
    let text = `Nova melhor volta pessoal, ${lapTime(a.time_ms, a.places, a.rounding)}.`;
    if (a.gain_ms != null) text += ` ${cap(gap(a.gain_ms, a.places, a.rounding))} mais rápido.`;
    return text;
  },
  "sector.time": (a) => {
    const phrases: string[] = [];
    if (a.content !== "delta") {
      const t = splitTime(a.time_ms, a.places, a.rounding);
      const secs = seconds(`${t.seconds},${t.fraction}`, t.minutes ? 2 : t.total);
      phrases.push(t.minutes ? `${count(t.minutes, "minuto", "minutos")} e ${secs}` : secs);
    }
    if (a.content !== "time") {
      if (a.level) phrases.push("empatado com a referência");
      else {
        const ahead = a.delta_ms < 0;
        const direction =
          a.comparison === "cumulative"
            ? ahead ? "à frente" : "atrás"
            : ahead ? "mais rápido" : "mais lento";
        phrases.push(`${gap(a.delta_ms, a.places, a.rounding)} ${direction}`);
      }
    }
    return `Setor ${a.sector}, ${phrases.join(", ")}.`;
  },

  "pace.drop": (a) => `Seu ritmo está caindo, ${gap(a.gap_ms)} acima da sua melhor volta.`,

  "race.final_lap": () => "Última volta.",
  "race.halfway": () => "Metade da corrida.",

  "position.gained": (a) => `Ganhou uma posição. Agora você está na posição ${a.position}.`,
  "position.lost": (a) => `Perdeu uma posição. Agora você está na posição ${a.position}.`,

  "fuel.remaining": (a) => `Combustível restante, ${laps(a.laps)}.`,
  "fuel.low": (a) => `Combustível baixo, restam ${laps(a.laps)}.`,
  "fuel.critical": (a) => `Combustível crítico, restam ${laps(a.laps)}.`,
  "fuel.shortfall": (a) => `Vai faltar combustível para ${laps(a.laps)}.`,
  "strategy.pit_window_next": () => "A janela de pit abre na próxima volta.",
  "strategy.pit_window_open": () => "A janela de pit está aberta.",

  "engine.water_temp_high": () => "Temperatura da água alta.",
  "engine.water_temp_critical": () => "Temperatura da água crítica.",
  "engine.oil_temp_high": () => "Temperatura do óleo alta.",
  "engine.oil_temp_critical": () => "Temperatura do óleo crítica.",
  "engine.oil_pressure_low": () => "Pressão do óleo baixa.",

  "tires.temp_high": () => "Temperatura dos pneus alta.",
  "tires.imbalance": (a) => {
    const pair = {
      front: ["dianteiros", "traseiros"],
      rear: ["traseiros", "dianteiros"],
      left: ["do lado esquerdo", "do lado direito"],
      right: ["do lado direito", "do lado esquerdo"],
    }[a.hotter];
    return (
      `Os pneus ${pair[0]} estão ${count(a.degrees, "grau", "graus")} ` +
      `mais quentes que os ${pair[1]}.`
    );
  },

  "coaching.braking_early": (a) => braking(a, "antes"),
  "coaching.braking_late": (a) => braking(a, "depois"),
  "coaching.repeated_lockups": (a) =>
    `Travamentos repetidos da roda ${WHEEL[a.wheel]} ${inCorner(a.corner, a.corner_name)}.`,
  "coaching.repeated_wheelspin": (a) =>
    a.corner || a.corner_name
      ? `Patinação repetida na saída ${ofCorner(a.corner, a.corner_name)}.`
      : "Patinação repetida na saída das curvas.",
  "coaching.repeated_bottoming": (a) =>
    `O carro está batendo o fundo ${inCorner(a.corner, a.corner_name)}.`,
  "coaching.corner_time_loss": (a) => {
    const where = inCorner(a.corner, a.corner_name);
    const clauses: string[] = [];
    if (a.brake_delta_m != null) {
      const when = a.brake_delta_m < 0 ? "antes" : "depois";
      clauses.push(`freou ${distance(Math.abs(a.brake_delta_m), a.units)} ${when}`);
    }
    if (a.apex_delta_kmh != null) {
      const how = a.apex_delta_kmh < 0 ? "mais lento" : "mais rápido";
      clauses.push(`passou ${speed(Math.abs(a.apex_delta_kmh), a.units)} ${how} no ápice`);
    }
    if (clauses.length === 0) {
      return `Você perdeu mais tempo ${where}. Foi ${gap(a.loss_ms)} mais lento.`;
    }
    return `Você perdeu ${gap(a.loss_ms)} ${where}. Você ${clauses.join(" e ")}.`;
  },
};
