import { SegmentedControl, Tip, TooltipProvider } from "gt7-datalogger-frontend";
import { useState } from "react";
import { Panel, Row, Surface } from "../preview-shell";

// The provider draws nothing of its own: App mounts one at the root with
// delayDuration={300} and every Tip on the page shares that delay and Radix's
// skip-delay window, so moving along a toolbar shows the second tip at once
// instead of waiting again. A cell can only show it doing its job — a real
// cluster of tipped controls, all of them governed by the one provider.

/** The Analysis toolbar as App wraps it: four tipped controls, one delay
 *  between them. */
export function AnalysisToolbar() {
  const [sync, setSync] = useState("time");
  return (
    <TooltipProvider delayDuration={300}>
      <Surface>
        <div className="flex flex-wrap items-center gap-3">
          <Tip content="Add a lap from another session at this circuit to the comparison">
            <button className="shrink-0 rounded border border-dashed border-edge px-2.5 py-1 text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent">
              + Add lap…
            </button>
          </Tip>
          <Tip content="Drag across a chart to zoom · double-click to reset">
            <span className="font-tabular text-[10.5px] text-ink-faint">0–2045 m</span>
          </Tip>
          <Tip content="Where the other laps' cars are drawn: where each was after the same lap time as the reference, or level with the reference car">
            <span className="inline-flex items-center gap-1.5 text-[10.5px] text-ink-faint">
              sync
              <SegmentedControl
                ariaLabel="Race line sync"
                size="sm"
                value={sync}
                onValueChange={setSync}
                options={[
                  { value: "time", label: "Time" },
                  { value: "position", label: "Position" },
                ]}
              />
            </span>
          </Tip>
          <Tip content="What each chart channel and feature of this view shows">
            <button
              aria-label="Open the Analysis guide"
              className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border border-edge text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
            >
              ?
            </button>
          </Tip>
        </div>
      </Surface>
    </TooltipProvider>
  );
}

/** A session's action row, where every button is an icon and the provider is
 *  what makes them explainable at all. */
export function SessionActions() {
  return (
    <TooltipProvider delayDuration={300}>
      <Surface width={320}>
        <Panel title="#228 · Tsukuba Circuit · AE86">
          <Row className="items-center">
            <Tip content="Open this session in the Analysis view">
              <button className="rounded border border-edge px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent">
                Analyse
              </button>
            </Tip>
            <Tip content="Download every lap of this session as lap files, with the session's details, in one ZIP">
              <button className="rounded border border-edge px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent">
                Export ZIP
              </button>
            </Tip>
            <Tip content="Download this session's lap analysis: every lap measured corner by corner against the session's best — braking points, minimum speeds, throttle and time lost">
              <button className="rounded border border-edge px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent">
                Report
              </button>
            </Tip>
            <Tip content="Replay recordings and other drivers' laps are indistinguishable from your own driving in telemetry — keeping them off the Bests board is a manual call.">
              <label className="flex items-center gap-1.5 text-[11px] text-ink-dim">
                <input type="checkbox" className="h-3.5 w-3.5 cursor-pointer" defaultChecked />
                counts
              </label>
            </Tip>
          </Row>
        </Panel>
      </Surface>
    </TooltipProvider>
  );
}

/** The race-line map's layer toggles — the densest run of tips in the app, and
 *  the case the shared skip-delay window is for. */
export function MapLayerToggles() {
  const layers = [
    [
      "Events",
      "Mark where each lockup, wheelspin, bottoming and kerb strike began, in the lap's colour. Click a marker to zoom every panel to it",
    ],
    [
      "TCS",
      "Ring every stretch where traction control was cutting power. The same exit every lap means the car is over the limit there",
    ],
    ["ASM", "Ring every stretch where stability management was intervening"],
  ] as const;
  return (
    <TooltipProvider delayDuration={300}>
      <Surface>
        <div className="flex flex-wrap items-center gap-1">
          {layers.map(([label, tip], i) => (
            <Tip key={label} content={tip}>
              <button
                aria-pressed={i < 2}
                className={`rounded border px-2.5 py-0.5 text-[10.5px] transition-colors ${
                  i < 2
                    ? "border-accent bg-accent/14 text-accent-300"
                    : "border-edge text-ink-dim hover:border-accent hover:text-accent"
                }`}
              >
                {label}
              </button>
            </Tip>
          ))}
          <span className="mx-1 h-3.5 w-px bg-edge" />
          <Tip content="While playback runs, zoom the map in and pan with the car instead of framing the whole circuit">
            <button
              aria-pressed={false}
              className="rounded border border-edge px-2.5 py-0.5 text-[10.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
            >
              follow
            </button>
          </Tip>
        </div>
      </Surface>
    </TooltipProvider>
  );
}
