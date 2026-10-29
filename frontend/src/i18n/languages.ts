// The languages this build ships. Adding one means a row here, a UI catalog
// in locales/ and a callout phrasebook in callouts/ — see
// docs/internals/translations.md. English is the source: every other catalog
// is checked against it by i18n.test.ts.

export const LANGUAGES = [
  // `speech` is the BCP 47 tag voices are matched by; a voice of any region
  // is accepted when no exact match is installed (es-MX for es-ES).
  { code: "en", name: "English", speech: "en-US" },
  { code: "es", name: "Español", speech: "es-ES" },
  { code: "pt-BR", name: "Português (Brasil)", speech: "pt-BR" },
  { code: "ja", name: "日本語", speech: "ja-JP" },
] as const;

export type Language = (typeof LANGUAGES)[number]["code"];

/** What the device setting stores: a language, or "follow the browser". */
export type LanguagePreference = Language | "auto";

export const LANGUAGE_CODES: readonly Language[] = LANGUAGES.map((l) => l.code);

export function isLanguage(value: unknown): value is Language {
  return typeof value === "string" && (LANGUAGE_CODES as readonly string[]).includes(value);
}

export function languageName(code: Language): string {
  return LANGUAGES.find((l) => l.code === code)?.name ?? code;
}

export function speechTag(code: Language): string {
  return LANGUAGES.find((l) => l.code === code)?.speech ?? "en-US";
}

/** The base subtag ("pt" for "pt-BR"), lower-cased, for loose matching. */
export function baseOf(tag: string): string {
  return tag.replace("_", "-").split("-")[0].toLowerCase();
}

/**
 * The shipped language closest to the browser's preferences: an exact tag
 * first, then the same base language (pt-PT reads pt-BR, es-MX reads es),
 * else English.
 */
export function detectLanguage(preferred: readonly string[]): Language {
  for (const tag of preferred) {
    const exact = LANGUAGE_CODES.find((c) => c.toLowerCase() === tag.toLowerCase());
    if (exact) return exact;
    const base = LANGUAGE_CODES.find((c) => baseOf(c) === baseOf(tag));
    if (base) return base;
  }
  return "en";
}

export function resolvePreference(
  pref: LanguagePreference,
  preferred: readonly string[] = browserLanguages(),
): Language {
  return pref === "auto" ? detectLanguage(preferred) : pref;
}

export function browserLanguages(): readonly string[] {
  if (typeof navigator === "undefined") return [];
  return navigator.languages?.length ? navigator.languages : [navigator.language ?? "en"];
}
