import { describe, expect, it } from "vitest";
import { CALLOUT_CATALOG, calloutExample } from "@/lib/calloutCatalog";
import { CALLOUT_CATEGORIES } from "@/lib/types";
import { translator, type Messages } from ".";
import { wordCallout, wordMessage } from "./callouts";
import { detectLanguage, LANGUAGE_CODES, type Language } from "./languages";
import en from "./locales/en.json";
import es from "./locales/es.json";
import ja from "./locales/ja.json";
import ptBR from "./locales/pt-BR.json";

const CATALOGS: Record<Exclude<Language, "en">, Messages> = { es, "pt-BR": ptBR, ja };
const PLURAL = /_(zero|one|two|few|many|other)$/;

/** Keys with plural variants folded into their stem. */
function stems(messages: Messages): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const [key, text] of Object.entries(messages)) {
    const stem = key.replace(PLURAL, "");
    out.set(stem, [...(out.get(stem) ?? []), text]);
  }
  return out;
}

function placeholders(texts: string[]): string[] {
  return [...new Set(texts.flatMap((t) => [...t.matchAll(/\{(\w+)\}/g)].map((m) => m[1])))].sort();
}

describe("catalogs", () => {
  const english = stems(en);

  it("ship every language they list", () => {
    expect(Object.keys(CATALOGS).sort()).toEqual(LANGUAGE_CODES.filter((c) => c !== "en").sort());
  });

  for (const [language, messages] of Object.entries(CATALOGS)) {
    const own = stems(messages);

    it(`${language} has exactly English's messages`, () => {
      const missing = [...english.keys()].filter((k) => !own.has(k));
      const extra = [...own.keys()].filter((k) => !english.has(k));
      expect({ missing, extra }).toEqual({ missing: [], extra: [] });
    });

    it(`${language} keeps every placeholder`, () => {
      const wrong = [...english.entries()]
        .filter(([key, texts]) => own.has(key) && placeholders(texts).join() !== placeholders(own.get(key)!).join())
        .map(([key]) => key);
      expect(wrong).toEqual([]);
    });

    it(`${language} has an _other form for every plural`, () => {
      const plurals = Object.keys(en).filter((k) => k.endsWith("_other"));
      expect(plurals.filter((k) => !(k in messages))).toEqual([]);
    });

    it(`${language} leaves nothing untranslated by accident`, () => {
      // Identical to English is fine for names, units and symbols; a whole
      // English sentence is not.
      const same = Object.entries(messages).filter(
        ([key, text]) => text === (en as Messages)[key] && /[a-z]{4,} [a-z]{4,} [a-z]{4,}/i.test(text),
      );
      expect(same.map(([key]) => key)).toEqual([]);
    });
  }
});

describe("t()", () => {
  const t = translator("en", en);

  it("fills placeholders and formats numbers for the language", () => {
    expect(t("engineer.lapN", { n: 4 })).toBe("lap 4");
    expect(t("settings.meta.dropped", { n: 12345 })).toBe("12,345 dropped");
    expect(translator("es", es)("settings.meta.dropped", { n: 12345 })).toMatch(/^12\.345 /);
  });

  it("picks the plural form from count", () => {
    expect(t("settings.pending.changes", { count: 1 })).toBe("1 change");
    expect(t("settings.pending.changes", { count: 3 })).toBe("3 changes");
    // Japanese has one form for every count: _other serves 1 as well.
    expect(translator("ja", ja)("settings.pending.changes", { count: 1 })).toBe("1件の変更");
  });

  it("falls back to English for a message the language lacks", () => {
    const sparse = translator("es", { "nav.live": "En vivo" });
    expect(sparse("nav.live")).toBe("En vivo");
    expect(sparse("nav.settings")).toBe("Settings");
    expect(sparse("settings.pending.changes", { count: 2 })).toBe("2 changes");
  });
});

describe("detectLanguage", () => {
  it("matches a tag, then its base language, else English", () => {
    expect(detectLanguage(["ja-JP"])).toBe("ja");
    expect(detectLanguage(["pt-PT", "en"])).toBe("pt-BR");
    expect(detectLanguage(["es-MX"])).toBe("es");
    expect(detectLanguage(["de-DE", "es"])).toBe("es");
    expect(detectLanguage(["de-DE"])).toBe("en");
    expect(detectLanguage([])).toBe("en");
  });
});

describe("callout phrasebooks", () => {
  const languages = ["es", "pt-BR", "ja"] as const;

  it("word every example in the catalog", () => {
    for (const language of languages) {
      for (const category of CALLOUT_CATEGORIES) {
        for (const callout of CALLOUT_CATALOG[category].callouts) {
          const text = wordMessage(callout.key, callout.sample as unknown as Record<string, unknown>, language);
          expect(text, `${language} ${callout.event}`).toBeTruthy();
          expect(calloutExample(callout, language)).toBe(text);
        }
      }
    }
  });

  it("say lap times the way each language reads them", () => {
    const lap = { time_ms: 92_487, delta_ms: 210 };
    expect(wordMessage("lap.time", lap, "es")).toBe("Tiempo de vuelta, un minuto 32,5. 2 décimas más lento.");
    expect(wordMessage("lap.time", lap, "pt-BR")).toBe("Tempo de volta, um minuto e 32,5. 2 décimos mais lento.");
    expect(wordMessage("lap.time", lap, "ja")).toBe("ラップタイム、1分32秒5。コンマ2秒遅いです。");
  });

  it("leave out a clause the server left out", () => {
    const lap = { time_ms: 92_487, delta_ms: null };
    expect(wordMessage("lap.time", lap, "es")).toBe("Tiempo de vuelta, un minuto 32,5.");
    const loss = {
      corner: 6,
      corner_name: "",
      loss_ms: 310,
      brake_delta_m: null,
      apex_delta_kmh: null,
      units: "metric",
    };
    expect(wordMessage("coaching.corner_time_loss", loss, "ja")).toBe(
      "最もタイムを失ったのはターン6です。コンマ3秒遅れました。",
    );
  });

  it("use the sector-voice precision when the server sends it", () => {
    const lap = { time_ms: 92_487, delta_ms: 247, places: 3, rounding: "round" };
    expect(wordMessage("lap.time", lap, "es")).toBe(
      "Tiempo de vuelta, un minuto 32,487. 0,247 segundos más lento.",
    );
    expect(wordMessage("lap.time", { ...lap, rounding: "truncate", places: 1 }, "ja")).toBe(
      "ラップタイム、1分32秒4。コンマ2秒遅いです。",
    );
  });

  describe("round a gap before choosing tenths or seconds", () => {
    // 950–999 ms is ten tenths once rounded: one second, as the explicit
    // precision carries it, never "コンマ10秒" / "10 décimas".
    const cases = [
      { ms: 949, ja: "コンマ9秒", es: "9 décimas", pt: "9 décimos" },
      { ms: 950, ja: "1秒", es: "un segundo", pt: "um segundo" },
      { ms: 980, ja: "1秒", es: "un segundo", pt: "um segundo" },
      { ms: 999, ja: "1秒", es: "un segundo", pt: "um segundo" },
      { ms: 1000, ja: "1秒", es: "un segundo", pt: "um segundo" },
    ];
    const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
    const loss = (loss_ms: number, brake_delta_m: number | null = null) => ({
      corner: 6,
      corner_name: "",
      loss_ms,
      brake_delta_m,
      apex_delta_kmh: null,
      units: "metric",
    });

    it.each(cases)("a lap time $ms ms slower", ({ ms, ja, es, pt }) => {
      const lap = { time_ms: 92_487, delta_ms: ms };
      expect(wordMessage("lap.time", lap, "ja")).toBe(`ラップタイム、1分32秒5。${ja}遅いです。`);
      expect(wordMessage("lap.time", lap, "es")).toBe(
        `Tiempo de vuelta, un minuto 32,5. ${cap(es)} más lento.`,
      );
      expect(wordMessage("lap.time", lap, "pt-BR")).toBe(
        `Tempo de volta, um minuto e 32,5. ${cap(pt)} mais lento.`,
      );
    });

    it.each(cases)("a corner that cost $ms ms", ({ ms, ja, es, pt }) => {
      expect(wordMessage("coaching.corner_time_loss", loss(ms), "ja")).toBe(
        `最もタイムを失ったのはターン6です。${ja}遅れました。`,
      );
      expect(wordMessage("coaching.corner_time_loss", loss(ms, -18), "ja")).toBe(
        `ターン6で${ja}ロスしました。ブレーキが18メートル早かったです。`,
      );
      expect(wordMessage("coaching.corner_time_loss", loss(ms), "es")).toBe(
        `Donde más tiempo perdiste fue en la curva 6. Fuiste ${es} más lento.`,
      );
      expect(wordMessage("coaching.corner_time_loss", loss(ms), "pt-BR")).toBe(
        `Você perdeu mais tempo na curva 6. Foi ${pt} mais lento.`,
      );
    });

    it("carry the same way the explicit precision does", () => {
      const lap = { time_ms: 92_487, delta_ms: 980 };
      expect(wordMessage("lap.time", { ...lap, places: 1, rounding: "round" }, "ja")).toBe(
        "ラップタイム、1分32秒5。1.0秒遅いです。",
      );
    });

    it("caption and speak the carried wording", () => {
      const callout = {
        text: "Lap time, one minute thirty-two point five. Ten tenths slower.",
        message_key: "lap.time",
        message_args: { time_ms: 92_487, delta_ms: 980 },
      };
      expect(wordCallout(callout, "ja")).toEqual({
        text: "ラップタイム、1分32秒5。1秒遅いです。",
        language: "ja",
      });
    });
  });

  it("name a labelled corner, and say feet and mph for imperial", () => {
    const braking = { corner: 1, corner_name: "Parabolica", meters: 15, units: "imperial" };
    expect(wordMessage("coaching.braking_late", braking, "es")).toBe(
      "Estás frenando unos 49 pies más tarde en Parabolica.",
    );
  });

  it("fall back to the server's English rather than say something half-built", () => {
    const callout = { text: "Lap time, one minute.", message_key: "lap.time", message_args: {} };
    expect(wordCallout(callout, "es")).toEqual({ text: "Lap time, one minute.", language: "en" });
    expect(wordCallout({ ...callout, message_key: "future.thing" }, "ja").language).toBe("en");
    expect(wordCallout(callout, "en").text).toBe("Lap time, one minute.");
  });
});

describe("urlLanguage", () => {
  it("reads ?lang= from the query or a hash route's query", async () => {
    const { urlLanguage } = await import(".");
    expect(urlLanguage({ search: "?layout=strip&lang=ja", hash: "" })).toBe("ja");
    expect(urlLanguage({ search: "", hash: "#/dash?lang=pt-BR" })).toBe("pt-BR");
    expect(urlLanguage({ search: "?lang=de", hash: "" })).toBeNull();
    expect(urlLanguage({ search: "", hash: "#/live" })).toBeNull();
  });
});
