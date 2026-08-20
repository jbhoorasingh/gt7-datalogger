# Lap detection & sessions

GT7 doesn't send "lap completed" messages — the datalogger has to cut clean laps out of
a 60 Hz packet stream that also includes menus, replays, pauses, and restarts. This page
explains exactly how.

## What gets sampled

For every packet, a sample is recorded onto the in-progress lap **only if all of these
hold**:

- the car is **on track** (flag bit 0) and the game is **not paused** (bit 1);
- the race isn't already finished — after the checkered flag GT7 reports
  `current_lap = total_laps + 1` for the cool-down lap, which is skipped.

Lap 0 — GT7's number for the time-trial out-lap — is **buffered but can never commit
as a normal lap**. It has to be buffered, because a single-lap replay streams entirely
as lap 0 (see [replay salvage](#replay-salvage) below); but the only way a lap-0
buffer becomes a saved lap is that salvage path, where GT7's own reported time must
vouch for it — a real out-lap has no such time, so out-laps still record nothing.
The lap-0 buffer is capped at **15 minutes**: some menu-adjacent states also report
lap 0 while nominally on track, and no plausible GT7 lap approaches 15 minutes, so
the cap sheds accumulated noise without ever touching a salvageable lap.

Packets with the `LOADING` flag set are never sampled, but they are not inert:
a `LOADING` transition with a full lap in the buffer is one of the
[replay-salvage](#replay-salvage) triggers — a replay's ending streams `LOADING`
while the menu builds, and that is the moment the buffered lap is either saved
or given up.

**Time and distance** are synthesized from the console's own **packet counter**, not
from wall clocks or a fixed tick assumption:

- each sample covers `Δpacket_id` frames (clamped to 1–60; a pid reset,
  non-monotonic value, or a gap over 1 s falls back to a single frame);
- `t += Δframes × 1/60` seconds, `dist += speed_mps × Δframes × 1/60` meters.

Dropped datagrams therefore widen the time/distance steps instead of silently
compressing the axes. The pid tracker also advances on paused/off-track packets, so
unpausing sees a ~1-frame gap — pauses still add no lap time or distance. Frames
lost in transit are counted and reported as `frames_dropped` in `/api/status` and
the Admin diagnostics.

Input metrics (full-throttle %, braking %, coasting %, tire spin, TCS/ASM activity)
are **time-weighted** using the `t` deltas, so a sample recorded after a gap counts
for the whole gap it covers.

## When a lap completes

A lap-counter change is only accepted as a real completed lap when **all four**
conditions hold:

1. the previous lap number was `> 0` (a real lap, not the out-lap);
2. the counter moved **exactly +1** (monotonic step);
3. the game reported a positive `last_lap_time_ms`;
4. the lap collected at least **600 samples** (~10 s of received packets at 60 Hz).

Condition 4 is the **phantom-lap guard**: in menus and replays GT7's lap counter
flickers through stale values and re-reports an old `last_lap_time`. Requiring 10
seconds of actual on-track samples filters all of that out.

The lap time itself is taken **verbatim from GT7's `last_lap_time_ms`** — it is never
computed from tick counts, so it matches the in-game timing exactly. (The one exception
is the manual *Log lap now* action, which saves a partial lap and derives its time from
the sample clock.)

!!! note "Why state is committed before the database write"
    Packets keep arriving at 60 Hz while a lap is being written to SQLite. All processor
    state — lap counter, sample buffer, fuel and engine aggregates — is committed
    *before* the asynchronous save starts; otherwise the lap boundary would re-trigger
    on the next packet and duplicate the lap. There's a regression test for exactly this.

## Replay salvage

GT7 streams a replay exactly like driving — there is no replay flag anywhere in the
packet — so a multi-lap race replay records its laps through the normal path above.
The failure case was the **single-lap replay** (the time-trial leaderboard's *watch
replay*): it ends *at* the finish line, so the `+1` counter step that commits a lap
is never observed. The fully-driven lap sat in the buffer, the stream cut to
`LOADING`, the buffer was discarded, and the now-empty session was deleted — the
replay left no trace at all. (#26 is what made this matter: the lap most worth
co-selecting in a cross-session comparison is the leader's.)

Now, when the stream breaks off with a lap in the buffer — a menu/`LOADING`
transition, a lap-counter reset, or a car change — the buffer is saved as a real lap
**only when GT7's own reported lap time vouches for it**:

| Check | Value | Why |
| --- | --- | --- |
| Minimum ticks | the same **600-sample** phantom guard as the counter path | menus flicker stale lap counters at stream boundaries too |
| Time agreement | GT7's reported lap time (last/best, read from the final packets) within **max(500 ms, 0.5 %)** of the integrated duration | the only evidence the buffer covers *exactly one* lap — an aborted half-lap has no matching GT7 time and is still discarded, as is a race replay's final lap when the replay cuts before the flag |

The buffer can also hold **more than the lap**: replays open with pre-roll —
footage from before the line — and a stream may run a beat *past* the crossing
before cutting to `LOADING`. Raw, either would fail the time check or anchor the
distance axis in the wrong place. With packet C, GT7's own live lap clock resolves
both: the clock re-anchors at every line crossing, so a **backward jump of more
than 250 ms** splits the buffer into segments — pre-roll, the flying lap, a
post-line stub — and within each, a frozen head (the clock parked at 0 until the
line) or frozen tail (the stream holding its final frame) is walked off. (250 ms
because ordinary jitter and packet loss move the clock by a frame or two, while a
genuine re-anchor jumps by whole seconds — the two are nowhere near each other.)
Each segment is tried against the reported times, latest first, so the lap that
just ended wins even when a looping replay holds more than one match. The salvaged
lap's distance axis therefore starts at the start line, which is what lets it
align with driven laps in a comparison.

A salvaged lap keeps GT7's reported time — the same verbatim rule as a normal lap —
flows through the same [span guard](#best-lap-tracking) and save path as any other,
and carries a **`salvaged` flag** on the completed-lap event, the stored row, and
every lap summary, so a time on the [Bests board](../guide/bests-view.md) can always
be traced to its provenance. Salvage runs *before* the zero-lap session deletion,
so the replay's session persists — and a salvaged lap **ends its session**: the
stream it came from broke off, so whatever streams next (another replay, your own
driving in the same car) opens a fresh session. Without that split, a lap-0 replay
would share a session with the driving after it — the counter never comes down from
above zero, so no lap-counter reset would ever separate them — and excluding the
replay from bests would take your own laps with it. A buffer that looked like a lap
but failed the time check logs its tick count and the candidate times, so a
near-miss can be diagnosed from the log instead of vanishing silently.

One residual accepted risk: aborting a lap when its elapsed time sits *within
half a second* of a time GT7 last reported — the previous lap **or** the session
best, each opening its own window — can salvage the abandoned lap with that stale
time. The windows are tight, the row is flagged `salvaged` (so it is recognizable
and deletable), and the alternative — demanding a counter step — is exactly what
replays never provide.

Note what salvage deliberately does not fix: telemetry cannot tell a salvaged replay
of someone else's lap from your own driving — that is what the Sessions view's
[exclude-from-bests toggle](../guide/sessions-view.md#excluding-a-session-from-bests)
is for. `GT7_SIM_SCENARIO=leader_replay` stages exactly this stream (pre-roll, one
flying lap as lap 0 with a running packet-C clock, then `LOADING`) for testing the
path without a console.

## What's stored per lap

- The **full 60 Hz sample series** (~28 channels — see
  [Derived channels](derived-channels.md)), JSON-encoded.
- Per-lap aggregates: fuel used, full-throttle / full-brake / coasting / tire-spin
  percentages, max speed, min body height, TCS/ASM usage, engine health, time of day.
- [Detected chassis events](event-detection.md), computed once at save time.
- Gearing metadata (ratios, tune top speed, redline) captured from the boundary packet.

## Session lifecycle

A **session** groups consecutive laps that belong together. A new session starts when:

- the **car changes** (`car_id` differs from the session's car), or
- the **lap counter resets** — `current_lap` drops below where it was (race restart,
  return to menu and back out), or
- a lap was **[salvaged](#replay-salvage)** — the stream it came from broke off, so
  whatever streams next is a new stint.

When a new session starts, the previous one is closed first:

- a session that ended with **zero laps is deleted** — menu visits and quick restarts
  don't pile up as empty rows. [Replay salvage](#replay-salvage) runs first, so a
  replay session whose only lap ended exactly at the line is saved, not swept away;
- a session with laps triggers the `session_summary`
  [webhook notification](../guide/admin.md#notifications) (car, track, laps, best lap,
  fuel used).

On the **first completed lap** of a session, the lap's geometry is compared against the
saved track signatures and the session is tagged automatically if it matches — see
[Track identification](track-identification.md).

## Best-lap tracking

The service tracks the session best and, separately, the best *before* the
just-completed lap (`prev_best_ms`). The live "Δ best" readout compares against
`prev_best_ms`, so when you set a new personal best you see the improvement
(e.g. `−0.312`) instead of a useless `+0.000`.

The `personal_best` webhook fires only when an existing session best is actually beaten —
never on the first lap of a session.

**Partial-lap guard.** A lap the logger only saw part of — a pit out-lap, or capture
starting mid-lap — passes every structural check above (GT7 reports a time for it, and
it lasts more than 10 s) while covering less of the track, with a distance axis
anchored wherever recording began rather than at the line. Its reported time is
*shorter* than a real lap's, so left alone it wins the session, becomes the live-delta
reference, and turns every position-based comparison built on it into garbage.

`_apply_span_guard` judges each lap by its distance span against recent laps, so it
needs no knowledge of the track's true length. Two properties matter more than the
numbers:

- **Every lap of the session is re-judged when a new one arrives.** The yardstick
  moves as laps accumulate, so a verdict fixed at lap time goes stale — a lap that
  looked full next to one short lap is partial once three laps agree, and vice versa.
  Stored rows are rewritten in both directions (`mark_session_laps_partial`).
- **Dropping a lap promotes the fastest remaining full lap**, rather than blanking the
  best until the next one arrives.

Calibrated against 850 recorded laps of real driving:

| | Value | Why |
| --- | --- | --- |
| Yardstick | median span of the last 5 laps | 12 of those laps ran *longer* than their session median (one by 44 %, an off-track excursion), and against a max-based yardstick one such lap makes every normal lap after it look partial |
| Full-lap ratio | 97 % | 98 % of laps sit within 0.5 % of their session median; no legitimate lap fell below 94.7 %, while real partials measured 40-95 % |
| Ratio before 3 laps | 93 % | with two laps "this one is 6 % short" and "that one ran 6 % wide" are the same picture, so only a flagrant shortfall counts until a third lap settles it |

`span_confirmed` — several laps agreeing on the distance — is exported for consumers
that compare laps *by position*: the Race Engineer's coaching callouts stay silent
until it is true (see [Race Engineer callouts](race-engineer.md)).

## What "invalid lap" means here

There is no track-limits detection (GT7 doesn't expose it). Laps are excluded only by
the structural rules above: lap-0 out-laps (buffered, but only ever saved through
[replay salvage](#replay-salvage)), laps under 600 ticks, post-finish laps, and
paused/off-track ticks. The Analysis view additionally hides laps that ended up with no
samples.
