// Interface translation: the device's language, its message catalog, and t().
//
// Catalogs are flat JSON keyed "area.thing" (locales/en.json is the source
// and the type of every key). English ships in the main bundle as the
// fallback; each other language is its own chunk, fetched once at start-up
// or when the language changes — an OBS source or a phone on /dash never
// downloads languages it does not show.
//
// Placeholders are {name}. A message that depends on a count has `_one`,
// `_other` (and whatever else Intl.PluralRules names for the language)
// variants, and t() picks one from `count`. Numbers in placeholders are
// formatted for the language (1,234 / 1.234 / 1 234).

import { createElement, Fragment, useMemo, type ReactNode } from "react";
import { create } from "zustand";
import en from "./locales/en.json";
import {
  browserLanguages,
  isLanguage,
  resolvePreference,
  type Language,
  type LanguagePreference,
} from "./languages";

export type Messages = Record<string, string>;
type EnglishKey = keyof typeof en;
type PluralBase<K> = K extends `${infer B}_other` ? B : never;
/** A message id: a key of en.json, or the stem of a plural set in it. */
export type MessageKey = EnglishKey | PluralBase<EnglishKey>;
export type Vars = Record<string, string | number>;
export type Translate = (key: MessageKey, vars?: Vars) => string;

const ENGLISH: Messages = en;
const STORAGE_KEY = "gt7-language-v1";

const LOADERS: Record<Exclude<Language, "en">, () => Promise<{ default: Messages }>> = {
  es: () => import("./locales/es.json"),
  "pt-BR": () => import("./locales/pt-BR.json"),
  ja: () => import("./locales/ja.json"),
};

interface I18nState {
  /** The stored choice; "auto" follows the browser. */
  preference: LanguagePreference;
  /** The language actually shown. */
  language: Language;
  messages: Messages;
}

export const useI18n = create<I18nState>(() => ({
  preference: "auto",
  language: "en",
  messages: ENGLISH,
}));

const pluralRules = new Map<string, Intl.PluralRules>();
const numberFormats = new Map<string, Intl.NumberFormat>();

function plural(language: string, count: number): Intl.LDMLPluralRule {
  let rules = pluralRules.get(language);
  if (!rules) {
    rules = new Intl.PluralRules(language);
    pluralRules.set(language, rules);
  }
  return rules.select(count);
}

function formatNumber(language: string, n: number): string {
  let format = numberFormats.get(language);
  if (!format) {
    format = new Intl.NumberFormat(language, { maximumFractionDigits: 2 });
    numberFormats.set(language, format);
  }
  return format.format(n);
}

function lookup(language: Language, messages: Messages, key: string, vars?: Vars): string | undefined {
  const count = vars?.count;
  if (typeof count === "number") {
    const form = `${key}_${plural(language, count)}`;
    return messages[form] ?? messages[`${key}_other`] ?? messages[key];
  }
  return messages[key];
}

function interpolate(language: Language, template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = vars[name];
    if (value === undefined) return whole;
    return typeof value === "number" ? formatNumber(language, value) : value;
  });
}

/** t() for a given language and catalog, falling back to English per key. */
export function translator(language: Language, messages: Messages): Translate {
  return (key, vars) => {
    const own = lookup(language, messages, key, vars);
    if (own !== undefined) return interpolate(language, own, vars);
    // Missing here: English, with English plural rules, rather than the id.
    const fallback = lookup("en", ENGLISH, key, vars);
    return interpolate("en", fallback ?? key, vars);
  };
}

/** Translate outside React (toasts, stores). Components should use useT(). */
export function t(key: MessageKey, vars?: Vars): string {
  const { language, messages } = useI18n.getState();
  return translator(language, messages)(key, vars);
}

/** t() bound to the current language; re-renders the caller when it changes. */
export function useT(): Translate {
  const language = useI18n((s) => s.language);
  const messages = useI18n((s) => s.messages);
  return useMemo(() => translator(language, messages), [language, messages]);
}

export function useLanguage(): Language {
  return useI18n((s) => s.language);
}

/** The language shown now, for Intl formatting outside React. */
export function currentLanguage(): Language {
  return useI18n.getState().language;
}

/**
 * Put React nodes (a link, a <strong>) where a translated message has a
 * {name} placeholder that t() left unfilled, so the translator decides
 * where in the sentence they go: rich(t("x.body"), { link: <a>…</a> }).
 */
export function rich(message: string, nodes: Record<string, ReactNode>): ReactNode[] {
  return message.split(/(\{\w+\})/).map((part, i) => {
    const name = /^\{(\w+)\}$/.exec(part)?.[1];
    return name !== undefined && name in nodes ? createElement(Fragment, { key: i }, nodes[name]) : part;
  });
}

function storedPreference(): LanguagePreference {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === "auto" || isLanguage(raw) ? raw : "auto";
  } catch {
    return "auto";
  }
}

/**
 * ?lang=es on the page's URL (or in a hash route's query, #/dash?lang=es):
 * the language for this page load, not stored. An OBS browser source or a
 * kiosk tablet has no language menu to pick one from.
 */
export function urlLanguage(loc: { search: string; hash: string }): Language | null {
  const hashQuery = loc.hash.includes("?") ? loc.hash.slice(loc.hash.indexOf("?") + 1) : "";
  const value = new URLSearchParams(loc.search).get("lang") ?? new URLSearchParams(hashQuery).get("lang");
  return isLanguage(value) ? value : null;
}

/**
 * A link for another device — an OBS source, a phone's dash — that opens in
 * this page's language: "…?layout=strip&lang=ja". Unchanged in English, so
 * such a page follows its own browser.
 */
export function withLanguage(url: string): string {
  const language = currentLanguage();
  if (language === "en") return url;
  return `${url}${url.includes("?") ? "&" : "?"}lang=${language}`;
}

async function loadMessages(language: Language): Promise<Messages> {
  if (language === "en") return ENGLISH;
  return (await LOADERS[language]()).default;
}

/**
 * Switch this device's language. The catalog loads before anything changes,
 * so the UI never shows a half-switched mix; a failed load keeps the current
 * language and rejects.
 */
export async function setLanguagePreference(
  preference: LanguagePreference,
  { remember = true }: { remember?: boolean } = {},
): Promise<void> {
  const language = resolvePreference(preference, browserLanguages());
  const messages = await loadMessages(language);
  try {
    if (remember) localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // Private mode: the choice lasts for this page load.
  }
  useI18n.setState({ preference, language, messages });
  document.documentElement.lang = language;
}

/**
 * Load the URL's, the stored or the browser's language before the first
 * render. Never rejects: a catalog that will not load leaves the app in
 * English.
 */
export async function initI18n(): Promise<void> {
  const fromUrl = urlLanguage(window.location);
  const preference = fromUrl ?? storedPreference();
  try {
    await setLanguagePreference(preference, { remember: fromUrl == null });
  } catch {
    useI18n.setState({ preference, language: "en", messages: ENGLISH });
  }
}
