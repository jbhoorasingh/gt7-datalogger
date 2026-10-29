# Translations

The interface and the Race Engineer's callouts can be shown and spoken in English,
Spanish (`es`), Brazilian Portuguese (`pt-BR`) and Japanese (`ja`). The language is a
per-device choice, like the speed units: a phone in Spanish and a laptop in English
can share one logger. User-facing behavior is in
[Race Engineer › Language](../guide/race-engineer.md#language).

## What is translated

| Part | Where the words come from |
| --- | --- |
| The interface: every view, the dash and its widgets, the OBS overlays, the error pages, dates | `frontend/src/i18n/locales/<lang>.json` |
| Spoken callouts, their captions, the callout feed, **Replay**, the examples under *What each category says*, and the Analysis view's post-lap coaching notes | `frontend/src/i18n/callouts/<lang>.ts` |
| A Tracks layout suggestion's reason | `tracks.why.*` in the catalogs, from the suggestion's `match` |
| Telemetry readouts, lap times and chart axes | Not translated: `1:32.487`, `245 km/h` and `12.4 L` keep one format in every language |
| Setting names and descriptions in **Settings › Advanced** | English: they come from the server's settings registry |
| Speech-engine diagnostics (*no response from the speech engine …*) | English: the same text goes back to the server's log |

## Interface catalogs

`locales/en.json` is the source: a flat object keyed `area.thing`, and the type of
every key — `t("nav.live")` does not compile if `nav.live` is missing from it. The
other files have the same keys. English is in the main bundle; every other language is
its own chunk, loaded once before the first render (`initI18n` in `main.tsx`) and again
when the language changes, so a page never shows half of one language and half of
another, and an OBS source never downloads a language it does not show.

- **Placeholders** are `{name}`. A number placeholder is formatted for the language
  (`12,345` / `12.345`).
- **Plurals** are separate keys: `settings.pending.changes_one`,
  `settings.pending.changes_other`, and whatever else `Intl.PluralRules` names for
  the language (`_few`, `_many`). `t(key, { count })` picks one; Japanese only needs
  `_other`.
- **Missing keys** fall back to English, key by key, with English plural rules.
- Components use `useT()`, which re-renders them when the language changes; `t()`
  works outside React (toasts, stores) and reads the current language. A memo or
  effect that builds text — an ECharts option, a tooltip — lists `t` in its
  dependencies, so switching language rebuilds it.
- **Tables** defined at module level (widget names, channel titles, map layers) hold
  `MessageKey`s, never translated text: they exist before the language is known. The
  component calls `t()` when it renders.
- **Markup inside a sentence** (a link, a `<strong>`): `rich(t("x.body"), { link: <a>…</a> })`
  puts the node where the message has `{link}`, so the translator decides where in the
  sentence it goes.
- **Dates and whole numbers** follow the interface language: `formatTime`,
  `formatTimeShort`, `formatDuration` and `formatInt` in `lib/format.ts`, or
  `currentLanguage()` as the `Intl` locale.

## Which language a page shows

The stored choice (localStorage `gt7-language-v1`, set by the 🌐 menu), else the
browser's languages matched to a shipped one — exact tag, then base language (`pt-PT`
reads `pt-BR`, `es-MX` reads `es`) — else English. `?lang=es` on a page's URL, or in a
hash route's query (`#/dash?lang=ja`), overrides both for that page load without
storing anything: an OBS browser source or a kiosk tablet has no menu to pick from.
Links built for another device — the overlay builder's **Copy URL**, Settings ›
Overlays — carry `lang` unless the page they came from is in English
(`withLanguage()`).

`i18n.test.ts` fails when a catalog lacks a key English has or has one English does
not, when a translation drops or adds a placeholder, when a plural set has no
`_other`, or when a whole English sentence was copied in untranslated.

## Callouts

The server words every callout in English (`text`, numbers spelled out by
`formatter.py`) and sends a `message_key` and `message_args` with it. The browser
words a callout in another language from those two, with that language's
phrasebook: one function per message key, typed against the arguments the backend
sends (`callouts/types.ts`). It is code rather than a template string because
callouts are composed: a gap is "*3 décimas*" below a second and "*1,2 segundos*"
above it, the clause about the braking point only exists when the server measured
one, and Japanese puts the corner first.

**The arguments describe the English sentence.** That is the contract between the
two sides: a clause the English leaves out arrives as `null` (a lap-time gap under
50 ms, a brake-point difference under the noise floor), and the sector-voice
precision travels as `places` and `rounding`, so a phrasebook never re-decides what
is worth saying. Corner names (`corner_name`) and the server's spoken units
(`units`) ride along where the sentence uses them. A change to a detector's wording
that adds or drops a clause has to change its arguments too.

The Analysis view's post-lap coaching notes come from the same detector, replayed, and
carry the same `message_key`/`message_args`, so they are worded with the same
phrasebook. A Tracks layout suggestion's reasoning travels as `match` beside its English
`why`.

Gaps without a set precision are rounded to whole tenths **before** the unit is
chosen (`spokenGap()` in `callouts/numbers.ts`, `spoken_gap` on the server): 950–999 ms
is ten tenths, said as one second, never "コンマ10秒". With the sector-voice precision
the server sends, `quantize()` rounds first and carries the same way.

Numbers stay digits outside English — "32,5" in Spanish and Portuguese, "1分32秒5"
in Japanese — because voices read their own language's digits correctly; English
spells them out because English voices read "1:32.487" badly. The only number a
phrasebook spells is 1 before a gendered noun (*una vuelta*, *uma volta*), which
some voices otherwise read as *uno* / *um*.

**Fallbacks.** A key this build does not know (a newer server), arguments that do
not word cleanly (missing, malformed, or a result containing `undefined`/`NaN`), or
a phrasebook that throws: the callout is said and shown in the server's English
rather than half-translated. A device with no voice for the callout language speaks
the English text in an English voice — a Japanese sentence handed to an English
voice is noise or silence — while the captions stay translated, and the Voice panel
says so.

## Adding a language

1. Add a row to `LANGUAGES` in `frontend/src/i18n/languages.ts`: the code (a BCP 47
   tag), the language's own name for itself, and the speech tag voices are matched by.
2. Copy `locales/en.json` to `locales/<code>.json`, translate the values, and add the
   file to `LOADERS` in `i18n/index.ts`. Keep every `{placeholder}`; give each plural
   the forms `Intl.PluralRules("<code>")` uses.
3. Write `callouts/<code>.ts` exporting a `Phrasebook` (the compiler lists every key
   still missing) and add it to `PHRASEBOOKS` in `callouts/index.ts`.
4. Add it to the `CATALOGS` map in `i18n.test.ts` and run `npm test`.

A native speaker's read of both files is worth more than any check here.
