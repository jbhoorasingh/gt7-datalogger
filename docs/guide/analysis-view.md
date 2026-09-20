# Analysis view

`#/analysis` — overlay any number of laps against a reference lap and find where the
time goes. The x-axis everywhere is **distance into the lap**, so laps of different
speeds line up corner-for-corner (see [Lap comparison math](../internals/analysis-math.md)).

![Analysis view](../screenshots/analysis.png)

## Selecting laps

1. Pick a **session** from the dropdown (`#id · car · n laps`). The newest session with
   laps is selected by default.
2. **Click lap chips** to toggle them into the comparison.
3. **Double-click a chip** (or use the `ref:` dropdown) to make it the **reference
   lap** — the lap everything else is measured against.
4. **+ Add lap…** brings in laps from *other* sessions at the same circuit: the
   picker lists every lap ever recorded there, **fastest first** — the lap you are
   hunting for is almost always the quick one — and a picked lap joins the comparison
   as a **guest chip** labelled `S12·L3` (session 12, lap 3). Guests behave exactly
   like local laps: time diff, corner report, map, reference — all of it.

Until you pick manually, the view auto-selects *latest vs best* — best meaning the
quickest lap that [counts](sessions-view.md#excluding-a-lap-from-bests), so a pit
out-lap or an excluded lap is never the default reference — and keeps following as
new laps arrive live — useful on a second screen while driving. Any manual change pins
your selection.

!!! note "Same circuit only"
    Every chart's x-axis is the **reference lap's** distance from the start line, and
    every other lap is placed on it by where it was on track — which works across
    sessions **on the same circuit**, and is why the picker only ever offers laps from
    this session's own circuit (it needs the session's track to be
    [named](sessions-view.md)). A lap that can't be followed along the reference's path
    keeps its own distance instead.
    Overlaying laps from different circuits would align nothing with nothing, so the
    UI doesn't offer it.

!!! tip "Deep links"
    The full selection is encoded in the URL —
    `#/analysis?session=3&laps=12,15&ref=15&ch=speed,brake` — so a bookmark or shared
    link reproduces the exact view. The Sessions and Live views use these links for
    their *compare* / *analyze* shortcuts. Lap ids are global, so a link may name
    laps from *other* sessions (`#/analysis?session=3&laps=12,208`) — they load as
    guest chips rather than being pruned.

## Stacked charts

The first panel is always **Time diff (s)** — each lap's gap to the reference at each
point of the track (positive = slower; where the curve climbs is where you lose time).
Below it, one panel per selected channel. Every panel runs to the end of the lap, the
line itself, not the last whole 5 m before it.

- **Synced cursors** — hover any panel and a crosshair appears at the same distance in
  every panel, on the race line map, and in the Corner Detail widget. The tooltip shows
  one column per lap, unit-formatted.
- **Zoom** — drag across any chart to zoom a section; every panel, the map, and the
  deviation chart crop to it. Double-click to reset, or use the **S1 / S2 / S3** buttons
  to jump to thirds of the lap. (Mouse-wheel zoom is off on purpose so page scrolling
  stays normal.)
- **Event shading** — detected [chassis events](../internals/event-detection.md) shade
  the panel that explains them: lockups on Brake, wheelspin on Throttle,
  bottoming/kerbs on suspension panels; TCS activity shades Throttle, ASM shades Speed.
  Bands are tinted in the lap's color.

## Lap playback

Above the charts sits a transport — **play / pause, a scrub bar, and 0.25× – 4×
speed** — that drives the same synced cursor the hover does. Press play and the lap
*happens*: the dot runs around the race line, the traction-circle marker sweeps, and the
Corner Detail widget follows, with no mouse involved.

The playhead is **distance-locked** (every compared lap sits at the same point of the
track, so the time diff under the cursor stays directly readable) but it advances on the
**reference lap's own clock** — it dwells through slow corners and sweeps down the
straights, rather than gliding at constant metres per second. The race line shows the
other laps by [time](#position-or-time-sync) meanwhile, so the gap opens up as distance
on track.

Beside the transport, a strip of the live-dashboard widgets renders the reference lap at
the playhead: the **steering wheel** turning (packet B+ recordings), the
**throttle/brake bars**, current **gear** and **speed** — the driver's hands, replayed.

Details worth knowing:

- **Scrubbing while playing** repositions the clock and playback continues from there;
  chart *hover* is ignored while playing (the playhead owns the cursor) and works
  normally whenever playback is paused or stopped.
- Playback **pauses itself when the tab is hidden**, and under a system
  *reduced motion* preference the cursor steps a few times a second instead of
  animating every frame.
- Switching the reference lap (or the selection) rewinds to the start.

## Corner report card

Below the charts, one row per corner: entry / minimum / exit speed and the time spent
between the corner's entry and exit, for the focused lap with the reference's figures in
small type beside it. The **Δ s** column is the time lost (red) or gained (green) through
that corner vs the reference — and the table is **sorted by it**, so the first row is
where the lap is actually being lost. The footer sums it: your total gap that happened
inside corners rather than on the straights.

Every lap is measured through the *reference lap's* corner windows (the same
distance-from-start convention as the time-diff chart), so the times are comparable;
with more than one comparison lap, chips pick which one the card focuses on. Clicking a
row zooms every chart and the map to that corner. Corners come from the circuit's
[authored set](tracks-view.md#labelling-corners) when it has one — stable numbers and
names across sessions — otherwise from detection on the reference lap.

## The guide

**?** in the toolbar opens a short guide to the view: a **Features** tab with a sentence
or two on each part of the page, and a **Channels** tab explaining every chart channel —
what it shows, how to read it, and what a recording needs to have it (a wider packet
format, a race). Channels already on the chart are marked **charted**. A search box
filters both, so *understeer* or *lockup* finds the channels that show them, and every
entry links to its section of this documentation. It never opens by itself.

## Channel picker

The **Channels (n)** button opens a grouped picker with 30 channels. Hovering one shows
what it is, and **What are these?** opens the guide's channel list:

| Group | Channels |
| --- | --- |
| Driving | Speed, Throttle, Brake, Coasting, Gear, Yaw rate, **Steering**, **Throttle/Brake applied**, **TCS cut**, **ABS release** |
| Race | **Race position** — recorded only while GT7 reports positions, so the panel is simply empty on time trials |
| Engine | RPM, Boost |
| Tires & wheels | Tire spd/car spd, slip front/rear avg, slip per wheel, tire temp front/rear avg, **tire temp F−R balance** |
| Chassis | Ride height, susp travel front/rear avg, **lateral / longitudinal / vertical g** |

Your selection persists in the browser and is added to the URL when it differs from the
default nine-panel stack. Laps recorded before a channel existed simply skip that line.

!!! note "Aid intervention, measured rather than inferred"
    **Throttle applied** / **Brake applied** are the pedal *after* the aids acted on it.
    Plotted against the raw pedal the two lines separate exactly where TCS or ABS
    intervened; **TCS cut** and **ABS release** are that separation on its own. These
    need the `~` packet format or wider — see
    [Configuration](../getting-started/configuration.md).

## Race line map

A top-down plot of the reference lap's driven line, colored by input zone — green =
throttle, red = braking, blue = coasting — with ▲ speed peaks and ▼ valleys marked.
Other selected laps overlay as colored lines. It follows the chart cursor, placing one
dot per lap at the hovered distance so you can see the spatial gap between lines, and it
auto-crops when you zoom a section.

A metre across is a metre down, so a corner's shape on screen is its shape on the track.
The axis ranges follow the plotting area's pixel aspect to keep it that way in any
window; letting each axis fill the box independently stretches the map by whatever the
circuit's aspect ratio happens to be — 8 % at Lago Maggiore Centre, nearly 3× at Deep
Forest.

### Position or time sync

**sync: Time | Position** in the map header (shown once more than one lap is on the
map) chooses where the other laps' dots go:

- **Time** (the default) leaves the reference dot at the cursor and moves every other
  dot to where *that* lap had got to after the same elapsed lap time. A slower lap's
  dot trails the reference, a quicker one runs ahead, and the distance between them is
  the gap — which is what makes a gap legible while a lap plays back. Hovering a chart,
  the dots show where the other cars were when the reference reached that point. A dot
  that has finished its lap waits at its last recorded point, a few metres short of the
  line.
- **Position** puts every dot level with the reference car — at the same point of the
  track, as the charts and Corner Detail compare them. The dots then differ only by the
  line each lap took there, which is worth seeing zoomed into a corner and very little
  at circuit scale.

The choice is remembered on this device, like **Follow**. Only the map changes; the
charts, the traction circle and Corner Detail stay on distance.

Both are exact about *where* a car was. Every compared lap is
[lined up with the reference by place](../internals/analysis-math.md#lining-laps-up-by-place-on-track),
not by how far it had gone — the two differ by metres a lap, because a wider line is a
longer lap — so in Position mode "level" really means level, to within centimetres.
Time mode draws each car from its own recorded positions against its own clock,
likewise within centimetres; where GT7 **reset** a car onto the track, its dot jumps to
the new spot instead of sliding across the infield. **Lap 1 of a race** starts from the
grid rather than the line; it doesn't count as a lap time (see
[Sessions](sessions-view.md#lap-table)), and time sync can't line it up with a lap
that started at the line.

Dots and the Follow camera are placed at the exact cursor position, between the
recorded 5 m steps, so the camera pans smoothly during playback.

### Events and driver aids

Two layers sit on top of the lines, each with a toggle in the map header. A toggle only
appears when the selected laps have something to put on it, and both choices are
remembered on this device.

- **Events** marks where each detected [chassis event](../internals/event-detection.md)
  *began*: ◆ lockup, ● wheelspin, ✚ bottoming, ✖ kerb strike. Markers are filled with
  the lap's color, so with several laps selected the pattern is the reading — three
  diamonds in one braking zone is a lockup that happens there every lap. Hover a marker
  for the lap, the wheels and how bad it was; **click** it to zoom every panel to the
  event with 60 m of track either side. Suspension events are detected per wheel, so
  same-type events that begin within 6 m of each other are drawn as one marker naming
  all the wheels.
- **TCS** draws a hollow ring, in the lap's color, on every sample where traction
  control was cutting power. Traction control catching the car at the same exit every
  lap says the car is over the limit there; rings that disappear from one lap to the
  next without the lap getting slower say TCS can come down a step. **ASM** does the
  same for stability management with hollow squares, and starts switched off.

Both layers follow the zoom, and the key under the map lists only what is drawn in the
current window. Laps recorded before per-corner channels existed have neither.

### Corners

The corner strip under the map is the fast way around a lap: **click a number** — or the
numbered circle on the map itself — and everything zooms to that corner, charts included,
with the braking zone into it and the exit out of it for context. `‹` `›` step through
them and wrap; **lap** goes back to the whole circuit. The selected corner is named
beside the strip (`T5 · left`).

The corners come from the reference lap's curvature, or from the circuit's
[authored corners](tracks-view.md#labelling-corners) when it has them — in which case the
numbering is the same in every session, not just within this one.

### Full screen

**⤢** (top right of the map) opens the same map as large as the window will allow, which
is the difference between a squiggle and a track on anything longer than a kart circuit.
The maximized view adds **scroll to zoom** and **drag to pan** — off in the rail, where a
wheel that sometimes scrolls the page and sometimes zooms a chart is worse than one that
always scrolls — plus the selected corner's minimum speed and total angle. Escape or
**Close** returns.

**Under the line: the surveyed road.** If the session's circuit has been named *and*
[surveyed](tracks-view.md), the map draws the track itself beneath every lap — road surface,
both borders, hand-marked walls in dark red, and the start/finish line. A racing line
only means something against the road it was driven on: whether the apex was clipped,
how much kerb was used, whether there was tarmac left on the exit. Circuits with no
bundle simply draw as before. The geometry is compiled server-side
(`/api/track-outline`) — a bundle holds up to 50,000 border records and the browser
never sees them.

The road is drawn only where it was actually surveyed. Stretches the survey
never covered appear as **dashed amber "unsurveyed gap" markers** rather than
invented road — the map's own *go touch this* prompt: drive that stretch on a
[survey run](tracks-view.md) and the hole fills in.

## Traction circle (g-g)

Every moment of the lap plotted as lateral g against longitudinal g, coloured by input
zone: braking at the bottom, power at the top, corners out to the sides, and the
combined phases — trail-braking in, picking up throttle while still turning — filling
the diagonals. How much of the ring gets used is the reading; an empty middle-left and
middle-right is a car that never brakes and turns at the same time. Other selected laps
appear as faint dots in their own colours, and the four numbers underneath are the peak
g reached in each direction.

The panel appears whenever the recording has the accelerometer (packet B or wider).

!!! warning "GT7 documents no unit for these channels"
    So the app checks them rather than trusting them: the server fits each axis against
    physics the same lap recorded — lateral against `v × ω` from the driven path,
    longitudinal against `dv/dt` — which recovers both the unit and the sign. The
    footnote under the diagram reports the fit. When a lap gave too little steady
    cornering or braking to check, it says **scale unverified** and assumes m/s²: the
    shapes still compare between laps, the absolute numbers may be off by a constant.

## Corner Detail widget

A top-down car with four corner cells that replays the load-transfer story as you scrub
the charts:

- **cell color** = that wheel's tire temp (blue < 55 °C, green 55–95 °C, red ≥ 95 °C)
- **bars** = suspension compression, normalized to that lap's travel range
- **LOCK / SPIN badges** using the live thresholds (brake ≥ 20 % & slip < 0.9;
  throttle ≥ 40 % & slip > 1.1)
- **F/R temp balance** readout at the bottom

One lap is in focus at a time; the reference lap is always the *ghost* — small secondary
temps and dashed suspension levels — and a cell gets a red/blue ring when the focus lap
runs more than 3 °C hotter/cooler than the reference at that point. With 3+ compared
laps, focus chips let you switch the focus lap.

## Side panels

- **Race engineer — post-lap notes** — the [race engineer's](race-engineer.md)
  coaching findings as text, in the exact wording voice would have used:
  "repeated front-left lockups into turn four", "you are braking early into turn
  six, about fifteen meters", "you lost three tenths in turn five — braked
  eighteen meters earlier and carried five kilometers per hour less at the
  apex". Grouped per lap, newest first, with the currently compared laps
  highlighted; a note that names a corner zooms the charts and map to it on
  click. The notes are **replayed** from the stored session through the same
  detector the voice engineer uses — same thresholds, same reference (the
  session best as it stood at the time), same repetition windows — so they
  exist for every recorded session, whether or not voice was ever enabled.
- **Gearing (reference lap)** — per-gear ratios with estimated speed at redline, tune
  top speed, and redline RPM.
- **Consistency — best 5 laps** — median speed plus a deviation band across the
  session's best laps; a wide band marks corners you drive differently every lap.
- **Lap times — this session** — every lap as a point, lap number across and lap time
  up, with a line through the laps that count, the **median** dashed and a band one
  standard deviation either side of it. Under the chart: the **spread** (the standard
  deviation of the counting laps' times, and that as a percentage of the median so
  circuits of different lengths compare), the median and the best. The spread is taken
  over the laps that count toward bests and no others — a pit out-lap, a race's lap 1
  from the grid and laps [excluded by hand](sessions-view.md#excluding-a-lap-from-bests)
  are drawn hollow and left out, and a lap too slow for the scale is pinned to the top
  edge as a hollow △ so one out-lap can't flatten the rest. It needs three counting
  laps. Laps in the current comparison take their chart color; click any point to add
  that lap to the comparison or take it out.
- **Fuel strategy** — the relative [fuel-map table](../internals/fuel-strategy.md):
  for each setting −5…+5 vs the reference lap's, projected fuel/lap, laps remaining,
  time remaining, and lap-time cost.
- **Tuning info (reference lap)** — max speed, min ride height, input percentages, tire
  spin, fuel used, car category, aid usage (TCS/ASM %), engine health (max water/oil
  temp, min oil pressure), and the detected-event summary. When the circuit is named and
  the car's class is known, it also shows the **class benchmark** — the fastest full lap
  ever recorded at this circuit in the same category (laps excluded by hand never
  set it, and a reference lap that doesn't count is not measured against it), the gap
  to the reference lap, a
  link to open it, and **compare**, which pulls the benchmark lap straight into the
  current comparison as a guest chip — the question the benchmark raises ("where does
  it gain?") answered in the same view that raised it. Scoped by class on purpose: a
  Gr.3 time and an N100 time around the same corners are not the same achievement.
  Sessions [excluded from bests](sessions-view.md#excluding-a-session-from-bests) never
  provide the benchmark — a replay of the leaderboard leader is not *your* class best.
