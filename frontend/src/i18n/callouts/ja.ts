// Japanese callouts, in the register of a race engineer on the radio: lap
// times as "1分32秒5", sub-second gaps as "コンマ3秒".

import { decimal, feet, fixed, mph, quantize, spokenGap, splitTime } from "./numbers";
import type { CalloutArgs, Phrasebook, Places, Rounding, Units, Wheel } from "./types";

const L = "ja";

function lapTime(ms: number, places: Places = 1, rounding: Rounding = "round"): string {
  const t = splitTime(ms, places, rounding);
  const secs = `${t.seconds}秒${t.fraction}`;
  return t.minutes ? `${t.minutes}分${secs}` : secs;
}

/** "コンマ3秒" under a second once rounded, "1秒" / "1.2秒" from one up. */
function gap(ms: number, places?: Places, rounding: Rounding = "round"): string {
  if (places) {
    const value = quantize(ms, places, rounding);
    const text = fixed(L, value, places);
    return value < 1 ? `コンマ${text.split(".")[1]}秒` : `${text}秒`;
  }
  const g = spokenGap(ms);
  return g.unit === "tenths" ? `コンマ${g.value}秒` : `${decimal(L, g.value)}秒`;
}

function distance(meters: number, units: Units): string {
  return units === "imperial" ? `${feet(meters)}フィート` : `${Math.round(meters)}メートル`;
}

function speed(kmh: number, units: Units): string {
  return units === "imperial" ? `時速${mph(kmh)}マイル` : `時速${Math.round(kmh)}キロ`;
}

function laps(n: number): string {
  return `${decimal(L, n)}周分`;
}

function corner(n: number | null, name: string, unknown = "次のブレーキングゾーン"): string {
  if (name) return name;
  return n ? `ターン${n}` : unknown;
}

const WHEEL: Record<Wheel, string> = {
  fl: "左フロント",
  fr: "右フロント",
  rl: "左リア",
  rr: "右リア",
};

function braking(a: CalloutArgs["coaching.braking_early"], when: string): string {
  return `${corner(a.corner, a.corner_name)}でブレーキが約${distance(a.meters, a.units)}${when}です。`;
}

export const ja: Phrasebook = {
  "system.enabled": () => "レースエンジニアを有効にしました。",
  "system.test": () => "レースエンジニアのテストメッセージです。",

  "lap.time": (a) => {
    let text = `ラップタイム、${lapTime(a.time_ms, a.places, a.rounding)}。`;
    if (a.delta_ms != null) {
      text += `${gap(a.delta_ms, a.places, a.rounding)}${a.delta_ms > 0 ? "遅い" : "速い"}です。`;
    }
    return text;
  },
  "lap.personal_best": (a) => {
    let text = `自己ベスト更新、${lapTime(a.time_ms, a.places, a.rounding)}。`;
    if (a.gain_ms != null) text += `${gap(a.gain_ms, a.places, a.rounding)}速いです。`;
    return text;
  },
  "sector.time": (a) => {
    const phrases: string[] = [];
    if (a.content !== "delta") phrases.push(lapTime(a.time_ms, a.places, a.rounding));
    if (a.content !== "time") {
      if (a.level) phrases.push("リファレンスと同タイム");
      else {
        const ahead = a.delta_ms < 0;
        const direction =
          a.comparison === "cumulative"
            ? ahead ? "リード" : "ビハインド"
            : ahead ? "速い" : "遅い";
        phrases.push(`${gap(a.delta_ms, a.places, a.rounding)}${direction}`);
      }
    }
    return `セクター${a.sector}、${phrases.join("、")}。`;
  },

  "pace.drop": (a) => `ペースが落ちています。ベストから${gap(a.gap_ms)}遅れです。`,

  "race.final_lap": () => "ファイナルラップです。",
  "race.halfway": () => "レースの折り返し地点です。",

  "position.gained": (a) => `ポジションアップ、現在${a.position}位です。`,
  "position.lost": (a) => `ポジションダウン、現在${a.position}位です。`,

  "fuel.remaining": (a) => `燃料残り${laps(a.laps)}です。`,
  "fuel.low": (a) => `燃料が少なくなっています。残り${laps(a.laps)}です。`,
  "fuel.critical": (a) => `燃料が危険な状態です。残り${laps(a.laps)}です。`,
  "fuel.shortfall": (a) => `このままでは燃料が${laps(a.laps)}足りません。`,
  "strategy.pit_window_next": () => "次の周でピットウィンドウが開きます。",
  "strategy.pit_window_open": () => "ピットウィンドウが開いています。",

  "engine.water_temp_high": () => "水温が上がっています。",
  "engine.water_temp_critical": () => "水温が危険域です。",
  "engine.oil_temp_high": () => "油温が上がっています。",
  "engine.oil_temp_critical": () => "油温が危険域です。",
  "engine.oil_pressure_low": () => "油圧が低下しています。",

  "tires.temp_high": () => "タイヤ温度が高くなっています。",
  "tires.imbalance": (a) => {
    const [hot, cold] = {
      front: ["フロントタイヤ", "リア"],
      rear: ["リアタイヤ", "フロント"],
      left: ["左側のタイヤ", "右側"],
      right: ["右側のタイヤ", "左側"],
    }[a.hotter];
    return `${hot}が${cold}より${a.degrees}度高くなっています。`;
  },

  "coaching.braking_early": (a) => braking(a, "早い"),
  "coaching.braking_late": (a) => braking(a, "遅い"),
  "coaching.repeated_lockups": (a) =>
    `${corner(a.corner, a.corner_name)}で${WHEEL[a.wheel]}のロックが続いています。`,
  "coaching.repeated_wheelspin": (a) =>
    a.corner || a.corner_name
      ? `${corner(a.corner, a.corner_name)}の立ち上がりでホイールスピンが続いています。`
      : "コーナーの立ち上がりでホイールスピンが続いています。",
  "coaching.repeated_bottoming": (a) => `${corner(a.corner, a.corner_name)}で底付きしています。`,
  "coaching.corner_time_loss": (a) => {
    const where = corner(a.corner, a.corner_name);
    // Joined as one sentence: the first clause in its -く form, the last
    // one finished ("ブレーキが18メートル早く、…低かったです").
    const clauses: [joined: string, final: string][] = [];
    if (a.brake_delta_m != null) {
      const d = distance(Math.abs(a.brake_delta_m), a.units);
      clauses.push(
        a.brake_delta_m < 0
          ? [`ブレーキが${d}早く`, `ブレーキが${d}早かったです`]
          : [`ブレーキが${d}遅く`, `ブレーキが${d}遅かったです`],
      );
    }
    if (a.apex_delta_kmh != null) {
      const v = speed(Math.abs(a.apex_delta_kmh), a.units);
      clauses.push(
        a.apex_delta_kmh < 0
          ? [`エイペックスでの速度が${v}低く`, `エイペックスでの速度が${v}低かったです`]
          : [`エイペックスでの速度が${v}高く`, `エイペックスでの速度が${v}高かったです`],
      );
    }
    if (clauses.length === 0) {
      return `最もタイムを失ったのは${where}です。${gap(a.loss_ms)}遅れました。`;
    }
    const detail = [...clauses.slice(0, -1).map((c) => c[0]), clauses[clauses.length - 1][1]];
    return `${where}で${gap(a.loss_ms)}ロスしました。${detail.join("、")}。`;
  },
};
