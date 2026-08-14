# Track identification

GT7 telemetry doesn't include the track name — but its world coordinates are fixed per
circuit. The datalogger exploits that in two ways, tried in order:

1. **A stored signature**, written when you name a circuit by hand.
2. **A [survey bundle](../guide/tracks-view.md)** — the surveyed road itself.

A name a person typed outranks anything inferred, so the signature goes first.

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

The first matching track's name is applied to the session and broadcast live, so the
track badge appears in the UI immediately.

## Why this works

- The signature is car- and pace-independent: a slow lap and a hot lap on the same
  circuit produce nearly identical bounding boxes and lengths.
- Different layouts of the same venue (e.g. full circuit vs short course) differ in
  length by far more than 4 %, so they register as separate tracks — name each layout
  once.
- Reverse layouts share geometry, so they will match the forward layout's signature.
  If you drive both directions regularly, include the direction in the name you give
  the first one you record.

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

New sessions identify themselves as they are recorded, but a history recorded before a
circuit was surveyed has already missed its chance. **Tracks → Identify sessions**
(`POST /api/tracks/identify`) re-runs the bundle match over every unlabelled session,
using each one's shortest **full** lap. Full, because a pit out-lap can cover only shared
tarmac and prove nothing; shortest for a duller reason — a lap is a sample blob of a few
hundred kilobytes, and this is the difference between reading a gigabyte and reading a
fraction of it. Being full costs nothing on top: a lap that covers the route covers it
however quickly it was driven. Sessions with no confident match are left alone.

## Managing tracks

- `POST /api/tracks {name, lap_id}` — what the *name track…* dialog calls; stores the
  signature and back-fills the current session's track name.
- `GET /api/tracks` / `DELETE /api/tracks/{id}` — list and remove signatures.
- `POST /api/tracks/identify` — name every unlabelled session that was driven on a
  surveyed circuit.

Deleting a track signature doesn't touch any session data — it only stops future
auto-matching by signature. A surveyed circuit keeps identifying itself from its bundle.
