# Race Engineer internals

How a telemetry condition becomes a spoken sentence, and every rule that decides
whether it is worth saying. User-facing behavior is in
[Race Engineer](../guide/race-engineer.md).

## Split of responsibility

```
GT7 → backend detectors → callout event (WebSocket) → browser queue → speech
```

The backend decides **what happened, whether it is reliable, what to say, how
urgent it is and when it stops being worth saying**. The browser decides **whether
this device speaks, which voice, how loud, and which categories the user wants**.

Keeping detection server-side means every client behaves identically and a phone
doesn't need the telemetry history to work out fuel range.

Module layout (`backend/app/race_engineer/`):

| File | Role |
| --- | --- |
| `models.py` | `VoiceCallout`, categories, verbosity presets, the per-event `SPECS` policy table |
| `formatter.py` | numbers → speech ("1:32.487" → "one minute thirty-two point five") |
| `state.py` | packet clock, lap history, the context detectors read |
| `thresholds.py` | trigger/clear values and persistence windows |
| `detectors/` | `lap`, `pace`, `race`, `fuel`, `engine`, `tires`, `coaching` (also chassis) |
| `manager.py` | category filtering, cooldowns, dedupe, priority, expiry, diagnostics |

## Reusing what already exists

Race Engineer adds no second copy of logic the datalogger already has:

- **Position changes** come from `processing/live_events.py`, the same debounced
  watcher the webhooks use (GT7 flips the position field every few frames in
  side-by-side racing; a second detector could disagree with the notifications).
- **Lap validity** uses `counts_for_best` / `invalidated_best` / `span_confirmed`
  from `processing/laps.py` (see below). A lap the logger only half-saw gets a GT7
  lap time — a short one — but is never announced and never becomes the reference.
- **Chassis events** (lockups, wheelspin) come from `processing/events.py`, computed
  once at lap save. Coaching only aggregates them across laps.
- **Corner numbers** come from `processing/analysis.py` `detect_corners`, run on the
  session-best lap.
- **Fuel** uses `processing/strategy.py`, a port of the frontend's `projectStrategy`
  (last three same-car laps, partial-lap outliers dropped). The two copies must stay
  in sync so voice and the strategy widget never disagree.

## Timing comes from packet ids

Every persistence window and cooldown is measured on a packet clock
(`state.PacketClock`) derived from the console's packet counter, exactly like lap
timing. GT7 keeps streaming at 60 Hz while paused, so wall-clock timers would count
a paused game as elapsed time; a dropped datagram widens one step instead of
silently shortening every window.

The only wall-clock value is `created_at_ms` on the emitted event — and even that is
not what the browser expires on (see below).

## Reliability rules

Every detector uses at least one of these, because "reliability over frequency" is
the whole point:

**Persistence** — a value must hold past a window before it counts. Instantaneous
facts (lap completed, position changed, final lap) are exempt.

**Hysteresis** — separate trigger and clear thresholds, so a value hovering on the
limit doesn't chatter:

| Condition | Trigger | Clear | Persistence |
| --- | --- | --- | --- |
| Water temperature high | 110 °C | 106 °C | 5 s |
| Water temperature critical | 120 °C | 116 °C | 5 s |
| Oil temperature high | 130 °C | 126 °C | 5 s |
| Oil temperature critical | 140 °C | 136 °C | 5 s |
| Oil pressure low | < 2.0 bar (above 2000 rpm) | 2.4 bar | 1.5 s |
| Tires hot | 110 °C | 104 °C | 5 s |
| Fuel low | < 3.0 laps | 3.5 laps | 2 s |
| Fuel critical | < 1.5 laps | 1.8 laps | 2 s |

Trigger values mirror `frontend/src/lib/alerts.ts` `THRESHOLDS` so the voice and the
dashboard's colors agree. Change one, change the other.

**Cooldowns** — per dedupe key, on the packet clock. Lap-scoped callouts have no
timer at all: their key contains the lap number, so a repeat is simply a duplicate.

**Escalation** — a request whose severity exceeds the last one for the same key
bypasses the cooldown. "Water temperature high" → "critical" is heard immediately
instead of waiting out a 20 s timer.

**Deduplication** — semantic keys (`fuel_short:2`, `position:2:4`,
`lap_time:2:8`) are remembered for 10 minutes. The browser keeps its own short-lived
cache of ids, which is what makes a reconnect safe.

## Priority and expiry

Priorities follow the spec's bands: 90–100 critical (may interrupt speech), 75–89
race-critical, 60–74 important, 40–59 coaching, 20–39 informational. Only callouts
that are both `interrupt` and ≥ 90 cancel speech in progress.

Each callout carries `ttl_ms`, `created_at_ms` and `expires_at_ms`. The browser
expires on **`ttl_ms` measured from its own receipt time**, not on `expires_at_ms`:
a phone or a Pi without NTP can be minutes off, and a stale callout is worse than no
callout — but so is dropping every callout because a clock disagrees.

## Detection cost

`on_packet` runs at 60 Hz and does comparisons and counters only. Anything that
walks a lap's samples runs in `on_lap`. The one genuinely expensive step, corner
detection on a new session-best lap (10–90 ms), runs on a worker thread via
`asyncio.to_thread` at a lap boundary, and only when a category that names corners
(`coaching` or `chassis`) is enabled.

Detection does not run at all unless a browser has registered itself as voice-capable
(`TelemetryService.engineer_active`), so the feature costs nothing for users who
never enable it.

## Which laps may be compared

Every coaching callout compares one lap against another *by position on the track*.
That is only meaningful if both laps cover the same track, which is exactly what a
half-recorded lap does not.

`LapProcessor._apply_span_guard` judges each lap by its distance span against recent
laps, and re-judges **every** lap of the session each time a new one arrives — the
yardstick moves, so a verdict fixed at lap time would go stale. Dropping a lap
promotes the fastest remaining full lap rather than blanking the best, and the stored
rows are rewritten in both directions (`mark_session_laps_partial`).

Calibrated against 850 real recorded laps:

| | Value | Why |
| --- | --- | --- |
| Yardstick | median span of the last 5 laps | 12 of those 850 laps ran *longer* than their session's median (one by 44 % — an off-track excursion); against a max-based yardstick a single such lap makes every normal lap look partial |
| Full-lap ratio | 97 % | 98 % of laps sit within 0.5 % of their session median, and no legitimate lap fell below 94.7 % |
| Ratio before 3 laps | 93 % | two laps that disagree are ambiguous — "this one is 6 % short" and "that one ran 6 % wide" are the same picture — so only a flagrant shortfall counts until a third lap settles it |
| `span_confirmed` | 2 of the last laps agreeing | reached by lap 3 in a typical session |

The failure this exists for, from the user's own data: a session whose lap 1 covered
87.9 % of the track was recorded as the fastest lap of the session, because GT7
reported a real (short) time for it.

`span_confirmed` is what `CoachingDetector` gates on, and it reaches the browser as
`coaching_ready` in `race_engineer_status` so the panel can explain the silence.

## Coaching

Coaching compares laps **by place, not by distance**. A lap's own distance is
integrated from its own speed, so two laps reach the same metre mark at different
places on the road — a median 2.9 m apart over real lap pairs and 64 m at worst
([Lap comparison math](analysis-math.md)) — and a braking point is judged to within
5 m. Every lap is therefore put on the **reference lap's distance axis** before
anything is compared (`EngineerContext.on_axis`, which is `alignment.align_to_reference`
with the lap's events moved along with it). A lap that cannot be lined up with
confidence keeps its own distance, as does a lap from an earlier session.

Lining a lap up is a walk along the reference's whole path — tens of milliseconds,
several times that on a Raspberry Pi — so it never runs on the event loop:
`prepare_lap` places the finished lap on a worker thread before `on_lap` runs the
detectors, and `refresh_reference` re-places the last three laps on the same thread
that detects the new reference's corners.

**Repeated lockups / wheelspin / bottoming** buckets `detect_events` output by 120 m
of track distance across the last three laps, keyed by wheel, and needs three
occurrences past a severity gate before it speaks. The bucket's mean distance is
mapped to a corner number via `detect_corners` — looking *ahead* for braking events
(a lockup happens before the corner it belongs to) and *behind* for wheelspin (which
happens on exit), within 250 m. Without a corner the wording falls back to "the next
braking zone".

At most one *driving* observation is emitted per lap. Bottoming is the exception: it
is setup feedback in the `chassis` category, so it does not consume the coaching
slot — a driver who has switched coaching off still hears that the car is grounding.

**Pace trend** (`detectors/pace.py`) averages the last three counted laps and compares
them to the session best — the pace the driver has already proven on this track, in
this car. It speaks at 0.8 s off, clears at 0.4 s, and stays quiet whenever any lap in
the window is itself on the pace: a window containing a personal best is the opposite
of a slump, and "your pace is dropping" seconds after one reads as a bug. A recovery
increments a spell counter that is part of the dedupe key, so a second slump later in
the stint is speakable again while a still-slipping one escalates instead of repeating.

**Braking point** (`_braking_point`) compares where each lap's braking zone for a
corner began against the reference lap's, and speaks only when every one
of the last two laps is on the same side of it and the *mildest* of them is still more
than 10 m off. Reporting the mildest lap rather than the worst gives a driver a marker
they can trust; one early stop is a moment, not a habit. Independent of time loss on
purpose — "am I braking too early?" deserves an answer even on a lap that was quick
elsewhere.

**Corner time loss** slices `time_delta_series` (current lap vs session best) at each
corner's entry and exit and reports the worst loss above 150 ms. Corners whose extent
wraps past the start line are skipped — the delta series restarts at zero there, so
the subtraction would be meaningless. It only fires on laps that were slower overall:
telling a driver who just set a personal best where they "lost time" reads as a bug.

It then explains *how* the corner was driven differently, by comparing the same two
laps directly:

| Measure | How | Noise floor |
| --- | --- | --- |
| Braking point | where the corner's braking zone began (below) | 5 m |
| Apex speed | minimum speed between entry and exit | 2 km/h |

What a lap did at a corner is not defined here. It is
`app/processing/corner_metrics.py`'s answer, shared with the
[lap analysis document](../reference/lap-analysis-format.md#what-is-measured-at-a-corner)
and the [corner report card](../guide/analysis-view.md#braking), so the engineer, the
export and the card cannot disagree about where a driver braked. The card dims a
brake-point difference under the same 5 m floor:

- A **brake application** is the pedal at or above 20 % (the gate
  `processing/events.py` uses for a lockup) for at least 0.1 s; two applications less
  than 10 m apart are one.
- Each application belongs to **one corner**: the first apex its *midpoint* has not
  yet reached, provided it began no more than 250 m before that corner's entry. The
  midpoint, so that trail braking carried a few metres past an apex still belongs to
  it.
- A corner's **braking zone** is the application, of those it was given, that took
  the most speed off, and its braking point is where that zone began.

The braking point used to be "the first sample above 20 % brake within 250 m before
the corner's entry". Through a sequence of corners that is often the braking for
the corner *before*, and a brake applied after an authored corner's entry marker (its
apex − 75 m, not where the driver turned in) was not found at all. On the best laps
of nine stored sessions it disagreed with where the corner's braking zone began at 41
of 105 corners — 13 of Mount Panorama's 23 — and replayed over 21 sessions it
produced callouts such as "you are braking early into turn ten, about two hundred
thirteen meters".

Either measure is dropped when it is inside its noise floor or missing (a flat-out
corner has no braking point), and when neither survives the callout falls back to the
plain "most time was lost in turn six" wording — inventing a cause for the loss would
send the driver chasing a marker they already hit.

Distances and speeds are spoken in the units of `GT7_RACE_ENGINEER_UNITS`
(`ctx.units`). It is a server setting because the text is worded server-side; the
dashboard's own km/h-vs-mph toggle only affects what is displayed.

## WebSocket protocol

`/ws/live` gained a client → server direction. Anything unparseable is ignored, so
pages from older builds (which send pings, or nothing) keep working. These messages
are **not** token-gated even when `GT7_ADMIN_TOKEN` is set: claiming voice output is
a driver-device action on a read surface, not an admin one.

Server → browser:

| Message | Payload |
| --- | --- |
| `voice_callout` | the `VoiceCallout` (id, event_type, text, category, priority, ttl, interrupt, dedupe key, `message_key`/`message_args`, metadata) |
| `voice_output_status` | `{active_client_id}` |
| `race_engineer_status` | enabled/active, verbosity, categories, `coaching_ready`, connected voice clients |

Browser → server:

| Message | Payload |
| --- | --- |
| `client_capabilities` | `{client_id, page, voice_supported, voice_enabled}` |
| `claim_voice_output` | `{client_id}` |
| `release_voice_output` | `{client_id}` |
| `voice_callout_ack` | `{callout_id, client_id, status, spoken_at_ms}` |

Callouts travel on the **event lane** of the per-client queue (never dropped, unlike
telemetry frames): staleness is handled by expiry, not by dropping messages.
Acknowledgements are diagnostics only and never gate the pipeline.

A claim is only accepted from the socket that registered that `client_id`, and only
from a `dash` or `engineer` page. A reconnecting client with the previous id gets its
claim back; no other device is ever promoted automatically.

`message_key` and `message_args` ride along with the English `text` from day one, so
localized wording can be produced in the browser later without changing the event
model.

## Browser side

`frontend/src/lib/speech.ts` holds the queue: at most three messages, ordered by
priority, expired entries dropped before speaking, duplicates rejected by id and
dedupe key, and a watchdog that cancels and moves on when an utterance never reports
back (some engines never fire `end`, which would otherwise wedge the queue for the
rest of the session).

A failing speech engine is reported, not swallowed: `SpeechSynthesisErrorEvent.error`
is turned into a sentence, shown in the panel, sent back in the ack's `reason` and
logged server-side, because "the browser refused to make a sound" and "the backend
produced nothing" are indistinguishable to a driver otherwise. `canceled` and
`interrupted` are filtered out first — those are our own `cancel()` coming back
(a critical callout, a disconnect, the Test voice button) and are not faults.
`speechSynthesis.resume()` runs before every utterance, since Chrome leaves the
engine paused after some tab-visibility changes and `speak()` then does nothing at
all. The watchdog is two-phase — 10 s for the utterance to *start*, then a
length-based window for it to *finish* — because Chrome's default voices are often
network-backed and take seconds to begin; one deadline measured against completion
cancels speech that was only slow. After three consecutive failures the queue stops attempting speech and falls
back to captions, because each attempt otherwise costs a watchdog timeout during
which the messages behind it expire.

Voice selection falls back in order: the stored `voiceURI`, another voice in the same
language, the browser's English default, then anything installed. Nothing assumes a
particular voice name exists.

`store/engineer.ts` owns the per-device preferences (localStorage key
`gt7-race-engineer-settings-v1`) and the client id. Runtime state — the queue, what is
being spoken, server status — is deliberately excluded from persistence: callouts are
live events, not saved notifications.
