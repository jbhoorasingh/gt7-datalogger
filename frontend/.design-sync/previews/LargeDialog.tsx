import { AnalysisGuide, formatLapTime, LargeDialog, notCountingLabel } from "gt7-datalogger-frontend";
import { LAPS } from "../fixtures/app";
import { DarkPage } from "../preview-shell";

// The dialog is nearly the whole viewport, so each cell is the whole card:
// cfg.overrides.LargeDialog pins cardMode "single" at 1000x700. DarkPage puts
// the app's ground under the backdrop.
function Page({ children }: { children: React.ReactNode }) {
  return (
    <>
      <DarkPage />
      {children}
    </>
  );
}

const noop = () => {};

/** `size="large"` with the Analysis view's "add a lap from another session"
 *  list in it — the frame's real job: a list long enough to need the whole
 *  viewport, scrolling inside a fixed header. */
export function AddLapFromAnotherSession() {
  const choices = LAPS;
  return (
    <Page>
      <LargeDialog
        open
        title="Add a lap from another session — Tsukuba Circuit"
        onClose={noop}
      >
        <div className="h-full overflow-y-auto p-3">
          <div className="space-y-1">
            {choices.map((lap, i) => {
              const added = i < 2;
              return (
                <button
                  key={lap.id}
                  disabled={added}
                  className={`flex w-full items-baseline gap-2 rounded-md border border-edge px-3 py-2 text-left text-xs transition-colors ${
                    added ? "text-ink-ghost" : "text-ink hover:border-accent hover:bg-panel-2"
                  }`}
                >
                  <span className="shrink-0 font-tabular">
                    S#{lap.session_id} · L{lap.number} · {formatLapTime(lap.time_ms)}
                  </span>
                  <span className="min-w-0 truncate text-ink-dim">
                    · {lap.car_name ?? "–"} · 2 Sep 03:1{lap.number}
                  </span>
                  {lap.counts_for_best === false && (
                    <span className="ml-auto shrink-0 text-warn">{notCountingLabel(lap)}</span>
                  )}
                  {added && <span className="ml-auto shrink-0">already selected</span>}
                </button>
              );
            })}
          </div>
        </div>
      </LargeDialog>
    </Page>
  );
}

/** `size="medium"` — the only other size, and the Analysis guide is its one
 *  caller: a reference card over the page rather than instead of it, capped at
 *  a reading width with its own toolbar and scrolling body. */
export function Guide() {
  return (
    <Page>
      <AnalysisGuide
        open
        tab="features"
        onTabChange={noop}
        charted={["speed", "throttle", "brake", "gear"]}
        onClose={noop}
      />
    </Page>
  );
}

/** The empty case the same dialog has to hold: a full-viewport frame with one
 *  sentence in it. */
export function NothingToShow() {
  return (
    <Page>
      <LargeDialog
        open
        title="Add a lap from another session — Mount Panorama Motor Racing Circuit"
        onClose={noop}
      >
        <div className="h-full overflow-y-auto p-3">
          <div className="p-8 text-center text-sm text-ink-dim">
            No laps from other sessions at this circuit yet.
          </div>
        </div>
      </LargeDialog>
    </Page>
  );
}
