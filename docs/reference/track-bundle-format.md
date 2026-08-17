# Track bundle format

A **track bundle** is everything this app knows about one circuit's geometry:
where its borders are, where the start/finish line is, and — once someone has
labelled them — where its corners are. One JSON document per circuit, stored
at `data/track-bundles/<slug>.json`, served by
`GET /api/track-bundles/{slug}` and accepted by
`POST /api/track-bundles/import`.

The format is versioned and self-describing on purpose: bundles are meant to
travel. They can be exported, mailed to somebody, merged into their store, and
merged back — and a shared repository of contributed bundles only works if
other tools can read them without asking this app anything.

Current version: **4**. Older documents are upgraded on read, and import
accepts every version from 1 up.

A machine-readable [JSON Schema for a v4 document](schemas/track-bundle.v4.schema.json)
is published alongside this page (and one for the
[compiled geometry](schemas/track-compiled.v1.schema.json) described at the
bottom). The schema describes a *current* document; the app's own import
validation additionally accepts and upgrades v1–v3, which the schema does not
model. If schema and code ever disagree, the code is what the app does.

Bundles contributed by other people live in
[gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data),
which stores them one record per line and sorted by position — the same
document, laid out so that git can diff and delta it. That repository carries
its own copy of the format in `tools/bundle_format.py`; if the two ever
disagree, this page and `app/processing/track_bundle.py` are what the app
actually does.

The app can also **pull** straight from that repo (or any host laid out like
it — `GT7_SHARED_BUNDLES_URL`): it reads the site's `index.json` (format
`gt7-datalogger-track-index`, v1 — the shape the repo's builder publishes,
with a `configurations` array whose surveyed rows carry a `bundle: {file,
track, points, runs, updated_at, …}` object, plus `unmatched_bundles` for
surveyed circuits tied to no official layout), resolves each `file` against
the index's own URL, and merges the fetched document through exactly the
import path described under [Validation](#validation). The index's counts are
advisory display numbers; nothing in it is trusted past "what is on offer and
where".

```json
{
  "format": "gt7-datalogger-track-bundle",
  "version": 4,
  "meta": {
    "track": "Lago Maggiore - Centre",
    "runs": 12,
    "source_runs": { "9f2c1ab0e441": 9, "3b7d0c15aa92": 3 },
    "updated_at": "2026-08-11T18:42:07.113904+00:00",
    "official": {
      "track": "Autodrome Lago Maggiore",
      "layout": "Centre",
      "official_id": "0a1b2c",
      "official_name": "Autodrome Lago Maggiore - Centre",
      "turns": 17,
      "length_m": 5648,
      "reverse": false
    }
  },
  "edges": [ /* border records — see below */ ],
  "finish_crossings": [ /* lap-rollover points on the start/finish line */ ],
  "corners": [ /* hand-labelled corners */ ],
  "sections": [ /* hand-labelled sections */ ]
}
```

## `meta`

| field | meaning |
|---|---|
| `track` | the circuit's name as typed by a driver. Slugified for the filename |
| `runs` | total survey runs behind this document — the sum of `source_runs` |
| `source_runs` | runs per **source id** (see below). The authoritative counter |
| `updated_at` | ISO-8601 UTC of the last write |
| `official` | which official GT7 configuration this is, `null` until confirmed |

`official` is never inferred. GT7 broadcasts **no track identifier**, and the
bundled catalog (`backend/data/tracks.json`) carries no world coordinates, so
nothing on either side identifies the other. The app suggests a match from the
name and the measured lap length; a human confirms it in the Tracks view.

## Source ids

A **source id** is 6–32 lowercase hex characters identifying one installation.
It is generated on first use into `data/source-id.json` and never shown to
anyone: it exists so that two people's *run ordinals* can be told apart, not to
identify a person.

It has to exist because of how votes are counted (below). Run ordinals are
local — my run 7 and your run 7 are unrelated facts — so merging two people's
bundles on the ordinal alone would double-count one and silently drop the
other depending on which ordinals happened to collide. With the source carried
per vote, each source's own highest run is what a merge advances.

## `edges` — border records

One record per **metre per side**, on a 1 m grid. A metre of border is one
fact; the kinds observed there are *votes* on what that fact is.

```json
{
  "x": 812.5, "z": -344.0, "y": 31.25,
  "hx": 0.9563, "hz": -0.2923,
  "side": "L",
  "kind": "wall",
  "votes": { "wall": { "9f2c1ab0e441": [3, 9] },
             "straddle": { "9f2c1ab0e441": [1, 4] } },
  "run": 4,
  "tw": 1.72
}
```

| field | meaning |
|---|---|
| `x`, `z` | world position, metres. First-seen wins on merge, which keeps files stable |
| `y` | elevation, or `null` for a metre first mapped before v3. Re-driving it fills it in |
| `hx`, `hz` | unit travel direction at the moment of evidence |
| `side` | `"L"` / `"R"` — the border **relative to the direction of travel** |
| `kind` | what the votes settled on. Derived; recompute rather than trust it |
| `votes` | `kind -> source -> [count, last_run]` |
| `run` | the run ordinal that first evidenced this metre (of `source_runs`' owner) |
| `tw` | axle track width in use when it was laid, or `null` |

`kind` is one of:

| kind | tier | how it was obtained |
|---|---|---|
| `wall` | manual | driver marked a wall/barrier/fence beyond the edge |
| `runoff` | manual | driver marked paved run-off beyond the edge |
| `edge` | manual | driver marked an ordinary track edge |
| `auto` | inferred | derived from a surface-type transition under a wheel |
| `straddle` | inferred | sampled while one side's wheels were held off the tarmac |

**Resolution:** the manual tier beats the inferred tier outright, not by
majority — the surface characters are *blind* to walls and paved run-off (both
read as plain tarmac), so an `auto` point at a hand-marked metre is not
evidence against the mark, it is evidence that the character stream could not
see it. Within a tier, the highest total vote count wins, ties broken by the
order above.

**Counting:** a vote is one *run* of one *source*, never one sample.
`[count, last_run]` means "this source has evidenced this kind here `count`
times, most recently on its run `last_run`". A merge advances a source's entry
only when the incoming `last_run` is **higher** than the one already recorded,
which is what makes both the ~60 s autosave and a repeated import idempotent.

## `finish_crossings`

Points where the lap counter rolled over, which GT7 does exactly as the car
crosses the line.

```json
{ "x": 12.5, "z": -880.25, "hx": 0.0, "hz": 1.0, "lap": 4 }
```

One crossing locates the line provisionally; repeat crossings landing within a
few metres of each other make it confident.

## `corners` — authored (v4)

Hand-labelled corners. **Authored data outranks derived data**: the app's
`detect_corners()` re-infers corners from each lap's curvature, so its
numbering can differ between two laps of one session; these do not.

```json
{
  "n": 4,
  "name": "Parabolica",
  "direction": "R",
  "apex":  { "x": 812.5, "z": -344.0 },
  "entry": { "x": 764.0, "z": -300.5 },
  "exit":  null,
  "note": ""
}
```

| field | meaning |
|---|---|
| `n` | 1-based, in track order from the start line. Renumbered from list order on save |
| `name` | optional; a named corner is what the race engineer speaks |
| `direction` | `"L"`, `"R"` or `null` |
| `apex` | required |
| `entry`, `exit` | optional turn-in / exit anchors, `null` when not marked |
| `note` | free text |

Anchors are **world positions, not lap distances**, because distance depends
on the racing line taken — a corner pinned at 1,240 m on one lap sits
somewhere else on the next. Each lap resolves its own distances by finding
where it passed the anchor.

Where a corner has no `entry`/`exit`, consumers should use a default window
around the apex, clipped at the midpoint to the neighbouring corners (this app
uses ±75 m).

## `sections` — authored (v4)

Optional named stretches, the input real sectors need (GT7 broadcasts none).

```json
{ "n": 1, "name": "Infield", "start": { "x": 0.0, "z": 0.0 },
  "end": { "x": 400.0, "z": 120.0 } }
```

## Version history

| version | change | upgrade on read |
|---|---|---|
| 1 | one point per (cell, side, **kind**), no provenance | co-located kinds become votes on one record, all at run 0 |
| 2 | one record per (cell, side); `votes`, `run`, `tw` added | — |
| 3 | `y` (elevation) added | existing records get `y: null`; re-driving fills them |
| 4 | votes attributed per source; `source_runs`, `official`, `corners`, `sections` | votes are attributed to the reading installation (a pre-v4 file could only have been written by it) |

An imported pre-v4 document is the exception: its votes are attributed to a
**synthetic** source id derived from the document's own contents, not to the
importing installation. Claiming a stranger's evidence as ours would collide
their ordinals with the ones our next survey is about to use — and deriving
the id from the contents keeps re-importing the same file idempotent.

A document declaring a version **newer** than the reader is refused rather
than partially read, so an old build can never save a lossy copy over a newer
one.

## Limits

| limit | value | why |
|---|---|---|
| `edges` | 50,000 records | a memory and file-size backstop; the JSONL log always has everything |
| `finish_crossings` | 20 | the newest are kept |
| `corners` | 100 | the Nordschleife has 73 named corners |
| `sections` | 40 | |
| names | 60 characters | |
| import body | 64 MB | |

## Validation

An imported bundle writes into the same store the app surveys into, so nothing
in an incoming document is taken on trust: every field is rebuilt from checked
values and unknown keys are dropped. Rejected outright — never partially
merged — are a wrong `format`, a future `version`, a blank `meta.track`,
non-finite or out-of-range numbers (`json.loads` accepts the `NaN` literal
happily, and one NaN in a border poisons every bounding box drawn from it),
unknown vote kinds, malformed source ids, records with no votes at all, and
anything over the limits above.

Authored corners and a confirmed `official` match are **never overwritten** by
an import: if the local bundle already has them, the incoming ones are dropped
and the response says so.

## Compiled geometry (derived, not part of the bundle)

The bundle stores *evidence* — an unordered cloud of voted border metres.
Everything that needs the borders in **order** (drawing the road, measuring
coverage against the boundary, judging a lap against the edges) reads a
second, derived document instead: the border cells walked into ordered
polylines, a centerline with width and elevation, the road surface as quads,
and per-side coverage measured against the boundary itself.

It lives at `data/track-bundles/compiled/<slug>.json`
([schema](schemas/track-compiled.v1.schema.json), format
`gt7-datalogger-track-compiled`, version 1) and is **recompiled automatically
whenever the bundle file changes** — a survey save, an import, a merge. It is
never exported and never imported: an imported bundle brings evidence, and
the receiving installation rebuilds the geometry from it. Delete the
`compiled/` directory at any time; it is repopulated on next use.

Two properties worth knowing when reading one:

- **Gaps are honest.** A stretch the ordering had to bridge without evidence
  is flagged in `gaps`, excluded from the drawn `borders`, excluded from the
  `centerline`, and counted against `coverage`. A partially surveyed circuit
  compiles into exactly the fragments that were driven.
- **`coverage` is boundary-relative.** `pct` is surveyed metres over total
  boundary metres known to the ordering — gaps in the denominator, and on a
  closed loop the closure too. `closed` says whether that denominator is the
  whole lap. `road_pct` is the share of surveyed border with the opposite
  border found across from it: how much of the road *surface* is resolved.
