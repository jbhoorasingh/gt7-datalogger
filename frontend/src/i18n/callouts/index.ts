// Callout wording in the device's language, built from the callout's
// message_key/message_args. English is the server's own `text` (worded in
// backend formatter.py, numbers spelled out); every other language comes from
// its phrasebook here.
//
// Anything a phrasebook cannot word — a key this build does not know (a newer
// server), arguments missing or malformed — falls back to the English text
// rather than saying something half-built. A callout is a live event: an
// English sentence is better than a wrong one, or silence.

import type { VoiceCallout } from "@/lib/types";
import type { Language } from "../languages";
import { es } from "./es";
import { ja } from "./ja";
import { speakable } from "./numbers";
import { ptBR } from "./pt-BR";
import type { CalloutArgs, CalloutKey, Phrasebook } from "./types";

const PHRASEBOOKS: Record<Exclude<Language, "en">, Phrasebook> = {
  es,
  "pt-BR": ptBR,
  ja,
};

export interface Worded {
  text: string;
  /** The language `text` is in: the requested one, or "en" on fallback. */
  language: Language;
}

function isKey(key: string, book: Phrasebook): key is CalloutKey {
  return Object.prototype.hasOwnProperty.call(book, key);
}

/** Word one message in a language, or null if that is not possible. */
export function wordMessage(
  key: string,
  args: Record<string, unknown>,
  language: Exclude<Language, "en">,
): string | null {
  const book = PHRASEBOOKS[language];
  if (!isKey(key, book)) return null;
  try {
    const say = book[key] as (a: CalloutArgs[CalloutKey]) => string;
    const text = say(args as unknown as CalloutArgs[CalloutKey]);
    return speakable(text) ? text : null;
  } catch {
    return null;
  }
}

export function wordCallout(
  callout: Pick<VoiceCallout, "text" | "message_key" | "message_args">,
  language: Language,
): Worded {
  if (language !== "en" && callout.message_key) {
    const text = wordMessage(callout.message_key, callout.message_args ?? {}, language);
    if (text) return { text, language };
  }
  return { text: callout.text, language: "en" };
}

/** The Enable / Test voice phrase, which never comes from the server. */
export const ENABLED_PHRASE_EN = "Race engineer enabled.";

export function enabledPhrase(language: Language): Worded {
  if (language === "en") return { text: ENABLED_PHRASE_EN, language };
  return { text: PHRASEBOOKS[language]["system.enabled"]({}), language };
}

export type { CalloutArgs, CalloutKey } from "./types";
