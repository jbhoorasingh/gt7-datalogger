# Sessions view

`#/sessions` — your lap archive. Sessions are created automatically (split on car change
or race restart — see [Lap detection & sessions](../internals/lap-detection.md)) and
listed newest first.

![Sessions view](../screenshots/sessions.png)

## Session rows

Each row shows the session id, car, start time, lap count, best lap, a **lap-time
sparkline** (chronological lap times with the best lap dotted in accent), and the track:

- a track **badge** when the track is known;
- a dashed **name track…** button when it isn't. Naming it fingerprints the circuit from
  the session's first lap, and **every future session on that track is tagged
  automatically** — see [Track identification](../internals/track-identification.md).

Click a row (or the chevron) to expand its lap table; **Analyze** opens the session in
Analysis with *latest vs best* selected.

## Category filter

When GT7 broadcasts the car's class (packet C — "Gr.3", "Gr.4", "N300"…), it appears as
a chip on each row and a filter strip above the list: *show me only the Gr.3 runs*. Only
classes actually present are offered, so the strip disappears entirely on a history
recorded before packet C, and **All** is the only way back to sessions that have no
class at all. A session whose own class is blank — its very first packet was a narrower
format — takes the class its laps recorded.

## Race results

A session that saw the checkered flag carries its result: a **P3/12**-style chip on the
row, with the race length and — when it can be known — the total race time in the
chip's tooltip. The result is written once, at the moment GT7's lap counter passes the
race distance, so it means *position at the finish*:

- A **time trial** (GT7 reports no positions there) shows no chip — "no race" is kept
  distinct from finishing last.
- A session that **ended mid-race** (stream stopped, race restarted) claims no result
  either, however well it was going at the time.
- The **total race time** is the sum of the stored lap times, and is only stated when
  every lap of the race is accounted for — a dropped stream or a mid-race join leaves a
  gap, and a sum across a gap would be confidently wrong, so the tooltip simply omits
  it. (GT7 broadcasts no total-time field, and the in-game clock runs at the event's
  own day-speed multiplier, so neither is a substitute.)

In race sessions the lap table also gains a **Pos** column — the position each lap was
completed in, which is what makes the race readable after the fact: the lap you gained
two places on is right there next to its time. Per-tick position is recorded too, as
the **Race position** channel in [Analysis](analysis-view.md#channel-picker).

## Notes & tags

Expanding a session reveals an editor for two user-set fields:

- **Notes** — free text, up to 500 characters: setup changes, conditions, what to try
  next. A session with a note shows a ✎ marker on its row; hover it to read the note
  without expanding.
- **Tags** — short repeatable labels ("wet", "race sim", "testing new diff"), added
  with **Enter** or the **add** button and removed from their chips. Tags appear on the
  session row, and once any exist a **Tag** filter strip joins the category filter above
  the list — the two combine, so *Gr.3 + wet* is one click each.

Tags are deduplicated case-insensitively, limited to 40 characters, and may not contain
commas. Both fields save through the same admin-gated PATCH as the bests toggle below,
and editing one never disturbs the other.

## Excluding a session from bests

Each session offers an **exclude from bests** toggle. It exists because of replays:
GT7 streams a replay exactly like driving — no flag distinguishes them — so watching
the time-trial leader's lap [records it](../internals/lap-detection.md#replay-salvage)
into *your* history, and nothing in the telemetry can tell that lap from one you
drove. Left alone it would own a row on the [Bests board](bests-view.md) and stand as
the class benchmark under your name.

An excluded session keeps every lap, and its laps stay selectable in Analysis —
overlaying your line against the leader's is the whole point of capturing the
replay — but it never owns a Bests row and never provides the
[class benchmark](analysis-view.md#side-panels). The toggle is admin-gated when
`GT7_ADMIN_TOKEN` is set, like every other mutation.

## Excluding a lap from bests

The session toggle is for laps that aren't yours. For your own laps that shouldn't
stand — an off-track moment, contact, a restart, a lap you know was dirty — each row of
the lap table has a **Counts** checkbox. Untick it and the lap leaves every best at
once: the session best, the [Bests board](bests-view.md), the
[class benchmark](analysis-view.md#side-panels), and the Race Engineer's pace and
coaching comparisons. A **why?** picker appears beside it — *off-track*, *contact*,
*restart*, *dirty* or *pit-out* — and the Bests board shows that reason next to the
time it replaced.

The checkbox works the other way too. The
[partial-lap guard](../internals/lap-detection.md#best-lap-tracking) marks laps it
thinks covered only part of the track as **partial** — and so is lap 1 of a race, which
[starts from the grid](../internals/lap-detection.md#where-a-lap-begins), not the line,
so its time is not a lap time. If the guard got one wrong, tick it and the lap counts
(**kept**). Ticking or unticking a lap back to what the guard says hands
it back to the guard, so a lap you excluded and then re-ticked follows the guard again
rather than staying pinned.

An excluded lap keeps its row, its telemetry and its place in Analysis, and its Δ
column still shows how it compared (signed, because a lap that doesn't count can be
quicker than the best). If the lap belongs to the session you are driving right now,
the live session best and the Δ-best reference move with it immediately. The checkbox
is admin-gated when `GT7_ADMIN_TOKEN` is set.

## Lap table

Per lap: time (best in accent), Δ to session best, whether it **counts** toward bests
(see above), fuel used, full-throttle %, full-brake %, coasting %, tire-spin %, events,
and max speed.

The **Events** column is a compact code — `2L·1S·3B·1K` means 2 lockups, 1 wheelspin,
3 suspension bottomings, 1 kerb strike; `–` means a clean lap.

The **Off-track** column can carry two figures, because two different judges
watch the lap. The first counts excursions by GT7's own per-wheel surface
flags (three or more wheels on the loose) — which are blind to paved run-off:
running wide over asphalt reads as tarmac and stays "clean". The second
appears once the circuit has been [surveyed](tracks-view.md) well enough
(≥ 50 % of the road resolved): the lap's positions are judged against the
**surveyed edges**, and sustained excursions beyond them count even on
pavement. Unsurveyed stretches never count against a lap, and laps recorded
before the session was identified are re-judged the moment it is. Where the
circuit crosses over itself, the lap's elevation says which level it was on;
laps recorded before elevation was stored (0.6.1 and earlier) are judged
against both levels there and get the benefit of the doubt. A lap is *clean*
only when both judges agree.

The survey's verdict follows the survey. Whenever a circuit's bundle changes —
a survey run stops, a bundle is merged in, renamed or deleted — every lap ever
driven there is judged again against the road as it now is, so a lap a bad
survey flagged reads clean again once the survey is corrected, and a lap
judged against a bundle since deleted goes back to *unknown* on that count.
The surface-flag verdict is GT7's own and never moves. **Re-check laps** on
the [Tracks view](tracks-view.md) forces the pass and says how many verdicts
changed.

Row actions:

| Action | What it does |
| --- | --- |
| **compare** | opens Analysis with this lap vs the session's best (best as reference) |
| **set ref** | opens Analysis with this lap as the reference |
| **json** | downloads the lap as `gt7-lap-<id>.json` — the full 60 Hz recording, shareable and re-importable |
| **csv** | downloads a **MoTeC-compatible CSV** for MoTeC i2 or Excel |
| **delete** | removes the lap and its telemetry (confirmed, irreversible) |

**Export session** at the bottom of an expanded session downloads the whole session as
`gt7-session-<id>.zip`: every lap's `.json` file plus a `session.json` with the car,
circuit, tags, note and race result — a backup or a hand-off in one click. See
[Session archive](../reference/lap-file-format.md#session-archive-zip).

**Delete session** removes the session and all its laps.

## Header actions

- **Log lap now** — saves the in-progress lap immediately without waiting for the start
  line. Handy for capturing a partial run or a test.
- **Import lap…** — load a `.json` lap file exported from any GT7 Datalogger instance.
  Older v1 files import cleanly; the newer per-corner channels are simply absent and
  the charts skip them. Events and aid metrics are recomputed from the samples on
  import. The lap lands in the **currently live session** when one is open (otherwise
  a fresh "imported" session), and keeps the verdicts it was exported with — a
  partial or [excluded](#excluding-a-lap-from-bests) lap stays that way — so otherwise
  it counts toward [bests](bests-view.md) like any lap. Someone else's lap belongs in a
  session you [exclude from bests](#excluding-a-session-from-bests). A session
  archive's lap files import one at a time the same way.

## Recording control

The **● REC / ○ Paused** toggle in the status bar pauses lap recording globally — the
live view keeps streaming, but nothing is written to the database until you resume.

See [Lap file format](../reference/lap-file-format.md) for what's inside the JSON and
CSV exports.
