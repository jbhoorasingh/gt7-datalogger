# Bests board

**Sessions › Bests** (`#/sessions?sub=bests`; old `#/bests` links land there) — the
personal-bests board: for every circuit, the fastest **counting** lap per car, across
every session ever recorded. The Sessions sub-tab answers *what did I drive*; this
answers *what is the best I have ever done here, and in what* — the
numbers a lap has to beat.

## What a row means

One card per circuit, with the circuit's outright best time in its header. Inside
it, one row per car — the fastest counting lap that car ever set at that circuit,
whatever session it happened in, fastest first. Per row:

| Part | Meaning |
| --- | --- |
| **Car** and **class chip** | the car, and its class ("Gr.3", "N300"…) when GT7 broadcast one |
| **clean** / **⚠** | track limits on that lap from GT7's surface flags (`–` when the lap has no surface data), plus a `· N ⚠` count of excursions beyond the [surveyed](tracks-view.md) road edge |
| **of N laps** · date | how many counting laps stand behind the row — a best from one lap and a best from forty are different kinds of evidence — and when the best was set |
| **Δ** | gap to the circuit's outright best (`best` on the fastest row), so within a circuit the rows read as a ladder |
| **Time** | the best counting lap |
| **open** | opens the lap in [Analysis](analysis-view.md) |

**Counting laps only.** Pit out-laps and laps the capture only half-saw are excluded
by the same [span guard](../internals/lap-detection.md#best-lap-tracking) that keeps
them from owning a session best: a partial lap's reported time is *shorter* than any
real lap's, so without the rule the board would fill with laps that never happened.
Laps you [excluded by hand](sessions-view.md#excluding-a-lap-from-bests) are left out
too. Sessions whose circuit was never [named](sessions-view.md) can't be grouped and
don't appear — name the track once and every session there joins the board.

## Why a quicker time is missing

When a lap *quicker* than a row's best was excluded by hand, the row says so:
**⊘ 1:28.422 off-track** — the fastest excluded time and the reason it was given, with
`+N` when there are more. Hover it for the full list. Slower excluded laps change
nothing on the board, so they aren't mentioned; a circuit and car whose every lap is
excluded has no row at all.

## Category filter

The same class chips as the [Sessions sub-tab](sessions-view.md#category-filter) —
they sit above both: pick one and the board narrows to that class — *the Gr.3
board*. The view fetches the
whole board once and filters in place, so the chips always list every class that
actually holds a best; only classes present are offered, and cars whose class is
unknown (recorded below packet C) appear only under **All**. (API consumers can ask
the server for the same narrowing with `/api/laps/bests?category=` — it filters the
finished board rows, never re-ranking within a class.)

## Excluded sessions

Sessions flagged
[**exclude from bests**](sessions-view.md#excluding-a-session-from-bests) never own a
row here, however fast their laps: replays record *other drivers'* laps into your
history and telemetry cannot tell them from your own driving, so the flag is how a
salvaged leaderboard replay is kept for comparison without standing on your board as
your time. A row set by a
[salvaged](../internals/lap-detection.md#replay-salvage) lap carries a **⟲ salvaged**
marker for exactly this reason — if you don't recognize the time, that marker is the
trail back to the replay session to exclude. The same applies to
[imported](sessions-view.md#header-actions) lap files: an import lands in a session
like any lap and can own a row, so a friend's lap you imported to study belongs in
an excluded session.

## Open in Analysis

Every row's **open** opens its lap in [Analysis](analysis-view.md). From there,
[**+ Add lap…**](analysis-view.md#selecting-laps) pulls any other lap at the same
circuit into the comparison — your latest attempt against the board's row, or the
row against the class benchmark. The board is the index; Analysis is the microscope.
