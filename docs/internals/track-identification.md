# Track identification

GT7 telemetry doesn't include the track name — but its world coordinates are fixed per
circuit. The datalogger exploits that in three ways, tried in order:

1. **A signature you wrote**, when you name a circuit by hand.
2. **A signature that shipped with the app** — geometry for 78 configurations,
   computed offline so a fresh install recognises circuits it has never seen driven.
3. **A [survey bundle](../guide/tracks-view.md)** — the surveyed road itself.

The order is the point. A name a person typed outranks anything inferred, and a
signature this installation learned outranks one the build supplied.

## The signature

When you name a track (**Sessions → name track…**), a signature is computed from one
completed lap on it:

| Component | Meaning |
| --- | --- |
| `length_m` | total lap distance (integrated from speed) |
| `min_x`, `max_x`, `min_z`, `max_z` | the lap's world-coordinate bounding box |

That's the whole fingerprint — five numbers.

## Matching

On the **first completed lap** of every new session, if the session has no track name
yet, the lap's signature is compared against every stored track. A match requires **all
four** gates to pass:

1. Both lengths are positive.
2. **Length within 4 %**: `|lap_length − track_length| ÷ track_length ≤ 0.04` — loose
   enough to absorb different racing lines, tight enough to separate layouts.
3. **Bounding-box centre within 120 m** on both the x and z axes — different circuits
   sit in different places in GT7's world space.
4. **Each extent within 20 %** of the stored width/depth.

The matching track's name is applied to the session and broadcast live, so the track
badge appears in the UI immediately.

Where **two** shipped signatures both match, no name is applied at all. A bounding box
cannot separate Lago Maggiore Full Course from Suzuka, nor Michelin Raceway Road Atlanta
from Watkins Glen International, and picking whichever came back first would put a wrong
circuit on a session with nothing to show it had guessed. Your own signatures are not
subject to this — the circuit you named is the one you meant.

## Why this works

- The signature is car- and pace-independent: a slow lap and a hot lap on the same
  circuit produce nearly identical bounding boxes and lengths.
- Different layouts of the same venue (e.g. full circuit vs short course) differ in
  length by far more than 4 %, so they register as separate tracks — name each layout
  once.
- Different **venues** can still collide. Two circuits that happen to occupy a similar
  rectangle at a similar length are indistinguishable to a bounding box; 15 of the 84
  published captures collide with another, which is why an ambiguous shipped signature
  declines rather than guesses.
- Reverse layouts share geometry exactly — same tarmac, same box, same length. A
  signature **you** wrote cannot tell them apart, so if you drive both directions
  regularly, include the direction in the name you give the first one you record. The
  shipped signatures *can* tell them apart; see below.

## The shipped signatures

A signature only exists once somebody has *named* a circuit, and a bundle only once
somebody has *surveyed* one — so a fresh install recognised nothing at all, and every
session stayed unnamed with no hint that naming one circuit would light up the track
badge, the outline, category bests and corner labels.

The app therefore ships `backend/data/track-signatures.json`: length, bounding box and
racing line for 78 GT7 configurations, generated in
[gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data)
and vendored so the first packet on an offline machine resolves.

Most of them come from the circuit captures published by
[zetetos/gt-telemetry](https://github.com/zetetos/gt-telemetry) (MIT): one recorded lap
per configuration, as GT7 world coordinates. Those coordinates are the same ones the
packets carry, so no transform is involved — verified against this project's own
surveys, where the capture and the surveyed borders agree to within a few metres of
centre and a few percent of extent.

Nine of them are derived from circuits that repository has **surveyed** — a bundle is the
road itself, so its bounding box is better evidence than one lap's, and the configuration
it belongs to is already confirmed rather than matched by name. The rest come from
published circuit captures. A configuration we have surveyed but whose capture was
dropped for not being a whole lap gets a signature with no racing line, and is named
forward without its direction judged.

They are loaded into the same `tracks` table as your own, marked `provenance = "seed"`,
which is what keeps the two kinds apart:

- A row you created **wins outright** over a shipped one.
- A re-sync replaces every shipped row and **no** row of yours.
- The Tracks view labels a circuit named this way **shipped**, so a name the build
  supplied is never mistaken for one you made.

Syncing runs at startup off the file's content hash, so it writes on a first run and on
a release that changes the seed, and never otherwise. A missing or invalid file is not
fatal — identification simply falls back to what you have named and surveyed.

### Telling a layout from its reverse

A reverse layout has exactly its forward twin's bounding box and length, so seeding
would have named reverse laps after the forward configuration. That is worse than a
cosmetic mislabel: personal bests are keyed on the circuit name, so forward and reverse
times would pool and compete for the same best. On the author's own recordings that was
10 laps of Deep Forest Reverse filed as Deep Forest Raceway, alongside 6 genuine forward
laps at that circuit — and 36 of the 78 shipped configurations have a reverse twin.

What separates them is the one thing a bounding box discards: **the order the road is
driven in**. Each shipped signature carries the racing line thinned to a point every
20 m, kept in driving order, and a lap is walked against it — for each position, the
nearest path point, and whether that index advanced or retreated, wrapping at the line.

This is deliberately *not* a test of whether the loop runs clockwise. Signed area does
not survive a crossover, and Suzuka is a figure-eight whose signed area says almost
nothing; walking the path is indifferent to crossovers because both arms of the eight
sit at different path indices.

A lap running the seed's way keeps the forward name. A lap running the other way is
named after the **reverse configuration**, whose own official id and name GT7 publishes
and the seed carries. A lap running backwards along a circuit that *has* no reverse
configuration is declined — if the road only runs one way, a lap going the other way
did not drive it.

!!! note "Calibration"
    Across 101 recorded laps, **every** direction score landed at ±1.00 and nothing at
    all fell between the thresholds. So the ±0.5 cut is not a balance point — a weak
    score means the lap does not follow that path at all, which is evidence the geometry
    match itself was wrong, and the session is left unnamed.

A shipped signature with no path (an older seed) is named forward without its direction
being judged, which is how the app behaved before direction existed.

## Matching against a survey bundle

The signature has a bootstrapping hole, and it is not a small one: a signature only
exists once somebody has *named* a circuit. Survey a track, and the app has a
metre-accurate map of it while still being unable to recognise the next session driven
there — so the track badge, the outline under the race line, category bests and corner
labels all stay empty on a circuit it has mapped in detail. Having surveyed a track and
having named it were two separate facts, and nothing joined them.

So when no signature matches, the lap is compared against the survey bundles. A bundle
is a strictly better fingerprint than a bounding box — it is the road, not a rectangle
around it — and matching asks the only question that matters: **did this lap drive on
this surveyed tarmac?**

- The lap has to be a **whole lap** — one that ends where it started. Coverage is a share
  of whatever samples it is given, so a fragment can sit entirely on tarmac two layouts
  share and score 100 % on the wrong one, the part that would have told them apart being
  the part that is missing. This matters most on the first lap of a session, which is the
  one that gets to name it and often the one that capture started in the middle of.
- Border records go into a 20 m occupancy grid, coarse enough to span a carriageway so
  the answer doesn't depend on which line through the corner was taken.
- Up to 600 evenly spread positions from the lap are tested against the 3×3
  neighbourhood of their own cell.
- The circuit scoring highest wins if it covers **≥ 60 %** of the lap **and** beats the
  runner-up by **≥ 25 percentage points**.

Both thresholds matter. The coverage floor is well below the 100 % a fully surveyed
circuit scores, because a bundle only covers ground that has actually been driven — a
half-finished survey should still recognise its own circuit. The margin exists because
two configurations of one venue share tarmac, so the loser is never near zero; when the
two are close the evidence genuinely doesn't distinguish them, and the session is left
unnamed rather than given a wrong name silently.

!!! note "Calibration"
    **Coverage.** Measured over 321 real recorded sessions scored against three bundles,
    the result is sharply bimodal: 13 sessions at 100 %, 5 more at 65–85 % (all on the
    circuit with the thinnest survey — 406 border records), then **nothing at all** until
    33 %, below which sit the 302 sessions driven at circuits with no bundle. The 60 %
    cut sits in the middle of that empty band.

    **Closure.** A lap counts as whole when it ends within 60 m of where it started, or
    5 % of its own length, whichever is more forgiving. Over 1,635 recorded laps, 1,583
    of the 1,618 full ones close within 10 m (median 1 m) and not one of the 17 partial
    laps closes within 50 m — so a flat threshold separates them. The relative term is
    for the tail of genuine laps that end 100–200 m out because dropped packets cost them
    their last second: 120 m means nothing on a 4 km lap and everything on a 500 m one.
    Together the two keep 99.6 % of full laps and turn away 15 of the 17 partials.

Fingerprints are cached against the bundle files' identity, so a re-survey, an import or
a rename rebuilds them without anyone having to remember to.

## Naming sessions that were recorded first

New sessions identify themselves as they are recorded, but a history recorded before the
app could recognise a circuit has already missed its chance. **Tracks → Identify sessions**
(`POST /api/tracks/identify`) re-runs identification over every unlabelled session,
signature first and then bundles — the same order, and the same code, the live path uses,
so a backfilled name means exactly what a live one does. The lap's positions go with it,
so a reverse lap in your history is named after the reverse configuration rather than its
forward twin. Each session is judged on its shortest **full** lap. Full, because a pit
out-lap can cover only shared tarmac and prove nothing; shortest for a duller reason —
a lap is a sample blob of a few hundred kilobytes, and this is the difference between
reading a gigabyte and reading a fraction of it. Being full costs nothing on top: a lap
that covers the route covers it however quickly it was driven. Sessions with no confident
match are left alone.

## Managing tracks

- `POST /api/tracks {name, lap_id}` — what the *name track…* dialog calls; stores the
  signature and back-fills the current session's track name.
- `GET /api/tracks` / `DELETE /api/tracks/{id}` — list and remove signatures.
- `POST /api/tracks/identify` — name every unlabelled session that was driven on a
  surveyed circuit.

Deleting a track signature doesn't touch any session data — it only stops future
auto-matching by signature. A surveyed circuit keeps identifying itself from its bundle.

Deleting a **shipped** signature is temporary: the next sync that sees a changed seed
file restores it. To override one permanently, name that circuit yourself — your own
signature wins over the shipped one for good.
