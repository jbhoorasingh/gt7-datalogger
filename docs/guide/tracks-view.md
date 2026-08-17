# Tracks view

**What do I have, and what is missing.**

Three separate things can be true about a circuit, and until this view existed
nothing on any screen showed them together:

| you have | which means | where it lives |
|---|---|---|
| a **named track** | a five-number signature sessions can match against | the `tracks` table |
| a **survey bundle** | its borders, elevation and finish line are mapped — *and* sessions driven on it identify themselves | `data/track-bundles/` |
| an **official layout match** | its real length and turn count are known | `backend/data/tracks.json` |

Having one is not having the others: a named track you have never surveyed has
no map. The Tracks tab lists every circuit this installation knows anything
about, merged across all three, with the gaps called out.

## Identifying sessions

A surveyed circuit recognises itself. When a new session's first lap has no
matching signature, the lap is compared against the survey bundles — *did this
lap drive on this tarmac?* — and a confident match names the session. That is
what makes the track badge, the surveyed road under the
[race line](analysis-view.md#race-line-map), category bests and corner labels
appear without anyone naming anything. See
[Track identification](../internals/track-identification.md#matching-against-a-survey-bundle).

Sessions recorded **before** a circuit was surveyed never got that chance.
**Identify sessions** re-runs the match over every unlabelled session in your
history and names the ones that were driven on a circuit you have since
mapped. Sessions with no confident match are left alone — an unlabelled
session is honest, a mislabelled one is not.

## Reading a row

Each row starts with three chips — **auto-ID**, **survey**, **official
layout** — filled in when you have that thing, dashed when you do not. Then
the bundle's numbers:

- **metres mapped** — border records, one per metre per side.
- **coverage** — the compiled score: each border's surveyed share of the
  boundary itself (with **✓ closed** once a side forms a loop), and **road %**,
  the share of surveyed border with the opposite border found across from it.
  Measured against the boundary, not the path you drove — a circuit you lapped
  fifty times with one unmapped chicane still says so. See the
  [compiled geometry](../reference/track-bundle-format.md#compiled-geometry-derived-not-part-of-the-bundle).
- **runs / sources** — how many survey runs are behind it, and how many
  different installations contributed (see [importing](#importing-and-sharing)).
- **% elevation** — the share of mapped metres that know their height.
  Bundles started before elevation capture sit near zero and **only fill in by
  re-driving**: this number is a fact about the data, not a bug.
- **finish line located** — from lap rollovers, which GT7 does exactly on the
  line.
- **corners labelled** — and, once the layout is confirmed, out of how many
  the official catalog says the circuit has.

## Runs that went nowhere

A survey with no circuit label saves **no bundle at all**. A run that starts
before the track is identified, and never gets named, accumulates border
evidence against nothing — the author lost about 55 minutes of driving that
way, with no screen anywhere reporting it.

Those runs are not gone: the JSONL log of a survey is a complete record, and
the Tracks view lists every orphaned one in a banner at the top with an
**Assign to a track…** button. Assigning replays the log through the normal
merge path, so the result is identical to having named the circuit while
driving.

(The same job from a terminal, for a log that has been moved off the machine
that recorded it, or for a dry run first:
`python scripts/jsonl_to_bundle.py <log.jsonl> <data_dir> "<Track>"`.)

## Confirming the official layout

When the name looks like an official GT7 configuration, the row offers the
match with its reasoning — the name, and the measured lap length where the
circuit has also been named.

You confirm it; the app never applies it silently. **GT7 broadcasts no track
identifier**, and the catalog carries no world coordinates, so nothing on
either side identifies the other and the match is genuinely a guess. A wrong
one would attach the wrong turn count and quietly mislead every "15 of 17
corners labelled" readout afterwards.

Confirming stores the match in the bundle, so it travels with export.

## Two names, one circuit

Bundle names are typed by a driver and then slugified, so "Lago Maggiore -
East" and "Lago Maggiore - East End" become two bundles of the same tarmac,
each with half the evidence. **Rename…** onto an existing name merges them:
one circuit is one bundle.

## Labelling corners

**Corners…** opens the refine view: the circuit's surveyed map, with clicking
placing corner apexes.

This exists because `detect_corners()` runs **per lap**, off the racing line.
A driver who straightlines an S takes the same tarmac on a shallower arc,
detection stops calling it a corner, and every corner after it renumbers — so
"turn 4" means a different piece of tarmac from one lap to the next. Anything
built on comparing corners across laps or sessions needs numbering that holds
still, and hand-labelled corners do.

Click **Place corners** and work around the map from the first corner after
the start line, then name them. Per corner you can optionally mark turn-in
(**entry**) and **exit**, and set the direction. **Sections** below the corner
list mark named stretches — the input real sectors need, since GT7 broadcasts
none.

Corners are stored **in the bundle**, not the database: they are knowledge
about the circuit, so they travel with export and import, and they are a large
part of what makes someone else's bundle worth pulling.

Once labelled, they take over from detection everywhere:

- the Analysis race-line map and corner detail use them, so corner 4 is corner
  4 in every session;
- the Race Engineer speaks the name — "you lost three tenths in the
  Parabolica" rather than "in turn four".

## Importing and sharing

Contributed bundles live in their own repository —
[**gt7-datalogger-track-data**](https://github.com/jbhoorasingh/gt7-datalogger-track-data)
— with a [browsable map of every circuit in
it](https://jbhoorasingh.github.io/gt7-datalogger-track-data/). Grab the pack
from its latest release, unzip, and run its `import_into_app.py` against your
datalogger; or download a single track from the site and use **Import
bundle…** below.

The data is separate from the app on purpose: it changes every time somebody
drives, and a corrected corner label should not have to wait for a software
release.

**Export** downloads the bundle document (see the
[format reference](../reference/track-bundle-format.md)) — that file is what
you contribute. **Import bundle…** merges one in; **Merge into…** on a row
does the same but forces the incoming document onto that circuit, which is how
a friend's slightly-differently-named bundle lands in the right place.

Raw survey runs travel too: every JSONL log has a **download** link (the
orphan banner and the collapsed *Survey logs* section list them all), and
**Upload survey log…** accepts one recorded elsewhere — assign it to a
circuit and it replays through the normal voting path, credited to the
installation that drove it. That is the finer-grained sibling of bundle
import: a bundle is a circuit's whole accumulated record, a log is one
evening's driving.

Merging accumulates fidelity across people rather than picking a winner. Each
installation stamps a **source id** on every vote it casts, so two people who
each drove a metre once have between them seen it twice — and re-importing the
same shared bundle a second time changes nothing, because each source's own
run count is what advances.

Your own corner labels and your confirmed layout match are never overwritten
by an import.

Imported documents are validated field by field before anything is merged: an
import writes into the same store the app surveys into, so a malformed or
hostile document is rejected outright, not partially applied.
