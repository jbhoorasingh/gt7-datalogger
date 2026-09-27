import { Tip } from "gt7-datalogger-frontend";
import { Panel, Row, Surface } from "../preview-shell";

// Tip wraps a single focusable element and opens its portalled bubble on hover
// or focus. A still card catches the closed state only — there is no `open`
// prop to force, and nothing here hand-draws a bubble to stand in for it. What
// these cells show is what the component does to the page it sits on: it
// decorates a trigger without changing it, which is exactly why it can be
// wrapped around table cells, checkboxes and icon buttons everywhere.

/** The Analysis toolbar's tipped buttons: the guide's "?" and the guest-lap
 *  adder, both dashed-outline controls that say nothing until asked. */
export function ToolbarButtons() {
  return (
    <Surface>
      <Row>
        <Tip content="Add a lap from another session at this circuit to the comparison">
          <button className="shrink-0 rounded border border-dashed border-edge px-2.5 py-1 text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent">
            + Add lap…
          </button>
        </Tip>
        <Tip content="What each chart channel and feature of this view shows">
          <button
            aria-label="Open the Analysis guide"
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border border-edge text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
          >
            ?
          </button>
        </Tip>
        <Tip content="Open the map full screen">
          <button
            aria-label="Maximise the race line map"
            className="rounded border border-edge px-2.5 py-0.5 text-[10.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
          >
            ⤢
          </button>
        </Tip>
      </Row>
    </Surface>
  );
}

/** A tipped checkbox — the Sessions lap table's "counts toward bests" box,
 *  whose whole explanation would never fit beside it. */
export function TippedCheckbox() {
  return (
    <Surface>
      <Panel>
        <div className="flex items-center gap-2 text-xs text-ink-soft">
          <Tip content="Excluded from bests by hand — off-track. Replay recordings and other drivers' laps are indistinguishable from your own in telemetry, so keeping them off the Bests board is a manual call.">
            <input
              type="checkbox"
              aria-label="Lap 5 counts toward bests"
              className="h-3.5 w-3.5 shrink-0 cursor-pointer"
            />
          </Tip>
          <span className="font-tabular">L5 · 1:06.387</span>
          <span className="text-warn">excluded · off-track</span>
        </div>
      </Panel>
    </Surface>
  );
}

/** Tipped text rather than a control: the corner report's column headings,
 *  where the tooltip carries the definition of the measurement. Only the
 *  measured columns are wrapped — Tip leaves a heading looking exactly like
 *  the plain ones beside it. */
export function ColumnHeadings() {
  return (
    <Surface>
      <Panel title="Corner report — vs L2">
        <table className="w-full">
          <thead>
            <tr className="text-[10px] uppercase text-ink-dim">
              <th className="px-2 py-1 text-left font-normal">Corner</th>
              <th className="px-2 py-1 text-right font-normal">
                <Tip content="Where the brake went on against the reference lap, in metres along the track. Negative = earlier, positive = later (deeper). Under 5 m is the same brake point.">
                  <span>Brake Δ m</span>
                </Tip>
              </th>
              <th className="px-2 py-1 text-right font-normal">
                <Tip content="Metres from the brake going on to its release.">
                  <span>Zone m</span>
                </Tip>
              </th>
              <th className="px-2 py-1 text-right font-normal">Min</th>
              <th className="px-2 py-1 text-right font-normal">
                <Tip content="Time through this corner vs the reference lap. Positive = lost here.">
                  <span>Δ s</span>
                </Tip>
              </th>
            </tr>
          </thead>
          <tbody className="font-tabular text-xs text-ink-soft">
            <tr>
              <td className="px-2 py-1">1 · Hairpin</td>
              <td className="px-2 py-1 text-right">−3</td>
              <td className="px-2 py-1 text-right">82</td>
              <td className="px-2 py-1 text-right">64.2</td>
              <td className="px-2 py-1 text-right text-brake">+0.114</td>
            </tr>
            <tr>
              <td className="px-2 py-1">2 · Dunlop</td>
              <td className="px-2 py-1 text-right">+6</td>
              <td className="px-2 py-1 text-right">41</td>
              <td className="px-2 py-1 text-right">98.7</td>
              <td className="px-2 py-1 text-right text-throttle">−0.061</td>
            </tr>
          </tbody>
        </table>
      </Panel>
    </Surface>
  );
}

/** The status bar's own tips, on the chrome that is always on screen: the
 *  source badge, the record toggle and the units switch. */
export function StatusBarControls() {
  return (
    <Surface>
      <Row>
        <Tip content="Telemetry from the simulated source — no PlayStation needed">
          <span className="rounded border border-edge px-2 py-0.5 text-[11px] text-ink-dim">
            sim
          </span>
        </Tip>
        <Tip content="Toggle lap recording">
          <button className="rounded border border-accent bg-accent/14 px-2 py-0.5 text-[11px] text-accent-300">
            ● REC
          </button>
        </Tip>
        <Tip content="Toggle speed units">
          <button className="rounded border border-edge px-2 py-0.5 font-tabular text-[11px] text-ink-dim">
            km/h
          </button>
        </Tip>
      </Row>
    </Surface>
  );
}
