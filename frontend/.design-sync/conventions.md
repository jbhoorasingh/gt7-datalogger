# Building with the GT7 Datalogger UI

A racing-telemetry library: live readouts, lap comparison charts, race-line maps
and the screens that hold them. It is **dark by construction** — every token is
tuned for a near-black ground, and a component dropped on a white page looks
broken rather than merely different.

## The page

There is no root provider to install. Two things are required instead:

- **Give the page the system's own ground.** Put `bg-surface text-ink` on the
  element the design lives in (this is exactly what the app puts on `<body>`).
  Without it, panels float on white and the ink ramp is unreadable.
- **Wrap in `TooltipProvider` only if you use `Tip`.** `Tip` is built on Radix
  and throws outside a provider; nothing else needs one.

Inter ships with the library (400/500/600/700) and is applied at 13px base.
**Numerals belong in `font-tabular`** — every readout in this system uses it, so
digits don't jitter as values change.

```tsx
<TooltipProvider>
  <div className="min-h-screen bg-surface p-4 text-ink font-tabular">…</div>
</TooltipProvider>
```

## Styling idiom: Tailwind v4 utilities over theme tokens

Style with ordinary Tailwind utilities; the colour names are this system's, not
Tailwind's defaults. The full layout, spacing, sizing and typography vocabulary
is available, including arbitrary values (`text-[10px]`, `max-h-[80vh]`),
fractions (`w-1/2`) and opacity modifiers (`bg-accent/12`).

| family | names | use |
|---|---|---|
| ground | `surface` `panel` `panel-2` | page, card, inset card |
| lines | `edge` `edge-bright` `hairline` `divider` | borders, hover borders, panel rings |
| ink | `ink` `ink-soft` `ink-muted` `ink-dim` `ink-faint` `ink-ghost` | brightest to faintest text |
| accent | `accent` `accent-100…900` | the one highlight colour |
| data | `throttle` `brake` `coast` `warn` | green / red / blue / amber, everywhere a channel is coloured |

Reach them as `bg-`, `text-`, `border-` (and `fill-`/`stroke-` in SVG):
`bg-panel`, `text-ink-dim`, `border-edge`, `text-brake`, `bg-accent/12`.

**Ready-made classes — prefer these over rebuilding them:**

- `.panel` — the one container surface (rounded, hairline ring, never a shadow)
- `.elevated` — for anything floating above the page (popovers, dialogs)
- `.section-header` — 10px caps label above a panel's content
- `.rule` / `.rule-row` — full-width divider with faded ends
- `.btn`, `.btn-primary`, `.btn-danger` — outlined buttons; the accent reads as
  an edge and a label, never a filled block
- `.skeleton` — loading placeholder
- `.font-tabular`, `.animate-pulse-dot`

Buttons in this system are outlined, panels are separated by a hairline ring
rather than a drop shadow, and section labels are small uppercase caps. A filled
primary button or a drop-shadowed card is off-system.

## Feeding the components

Most components are pure: you pass them data, they draw it.

- **Live readouts** (`SpeedWidget`, `GearWidget`, `DeltaWidget`, …16 widgets)
  take `{ frame, laps, variant, w, h, options }`. `frame` is a `LiveFrame` —
  `speed_kmh`, `rpm`, `gear`, `throttle`, `brake`, `fuel_level`, `delta_ms`,
  `tire_temps`, `car_name`, `track_name` and ~30 more. **`demoFrame(ms)` returns
  a realistic one** (a 20s lap loop: flat out at 8000, braking at 3500,
  wheelspin at 7000), and `DEMO_LAPS` is its lap history — use them rather than
  hand-building a frame. `WIDGET_META` lists each widget's variants and legal
  grid footprints; `WIDGET_COMPONENTS` maps ids to components.
- **Dashboards**: `GridRenderer` takes a `LayoutConfig` — start from
  `DASH_PRESETS` or `DEFAULT_LAYOUT`, and pass layouts through
  `normalizeLayout`. Don't hand-write a grid.
- **Lap analysis**: components take `LapSummary[]` and a `CompareResult`
  (`laps` keyed by lap id, each with `series` of distance-resampled channels).
  Colour laps with `lapColorMap(ids, fastestId)` — id-keyed, collision-free, and
  the fastest lap always takes purple. `formatLapTime(ms)` is the time format.
- **Charts** are ECharts via `EChart`; build options with `baseGrid()`,
  `baseAxis()` and `CHART_COLORS` so a new chart matches the shipped ones. An
  `EChart` needs a laid-out height or it renders blank.
- **App state** lives in exported zustand stores — `useTelemetry` (connection
  and status), `useSettings` (units), `useToasts`, `useEngineer`. Set them with
  `useTelemetry.setState({…})` to put a screen in a given state.

One trap worth knowing: `projectStrategy` (behind `FuelWidget`, `StrategyWidget`
and `AlertsWidget`) ignores laps whose `car_id` differs from the frame's, so a
mismatched frame and lap set silently render "–".

## Where the truth is

Read `styles.css` and the files it imports for the tokens and component classes,
and `<Name>.prompt.md` beside each component for its props and examples. The
`.d.ts` next to it is the API contract.

```tsx
<div className="bg-surface p-4 text-ink font-tabular">
  <div className="panel p-3">
    <div className="section-header mb-2">Stint</div>
    <div className="flex items-center gap-4">
      <SpeedWidget frame={demoFrame(8000)} laps={DEMO_LAPS}
                   variant="digits" w={2} h={1} options={{}} />
      <DeltaWidget frame={demoFrame(2800)} laps={DEMO_LAPS}
                   variant="bar" w={2} h={1} options={{}} />
    </div>
    <div className="rule my-3" />
    <button className="btn btn-primary">Save lap</button>
  </div>
</div>
```
