import { LapSparkline } from "gt7-datalogger-frontend";
import type { LapSummary } from "gt7-datalogger-frontend";
import { LAPS } from "../fixtures/app";
import { Labelled, Row, Surface } from "../preview-shell";

// The mini lap-time line in a Sessions row, with the session's consistency
// figure (#112) under it. The component fetches the session's laps itself
// (api.sessionLaps, cached per lap_count) and renders nothing at all until it
// has two of them — so the cells answer that one request from a table of real
// lap times instead of leaving the card empty.
//
// One session is the fixture session itself (Tsukuba, AE86, 8 laps, out-lap
// and a scruffy lap 5 included); the others are plausible sets for circuits and
// cars the fixtures were driven on, because the point of the cells is the shape
// of a session: finding pace, losing it, or repeating yourself.

const SERVED: Record<string, LapSummary[]> = {};

function session(id: number, times: number[]): number {
  // The API answers newest lap first; the component reverses it to plot time.
  SERVED[String(id)] = times
    .map((time_ms, i) => ({ ...LAPS[0], id: id * 100 + i + 1, session_id: id, number: i + 1, time_ms }))
    .reverse();
  return id;
}

const TSUKUBA = session(228, LAPS.map((l) => l.time_ms).reverse());
const SUZUKA = session(280, [158_402, 155_930, 154_115, 152_764, 152_108, 151_739]);
const PANORAMA = session(194, [131_077, 131_544, 132_145, 133_480, 134_079, 135_501]);
const GRAND_VALLEY = session(232, [72_124, 72_338, 72_507, 72_290, 72_611, 72_455]);
const TRIAL_MOUNTAIN = session(239, [108_165, 109_402, 108_744, 111_920, 109_133, 108_507]);

// api.sessionLaps goes through fetch, and a preview card has no backend behind
// it. Answering that one path is the store-seeding of this component.
const realFetch: typeof window.fetch = window.fetch.bind(window);
window.fetch = ((input: Parameters<typeof window.fetch>[0], init?: Parameters<typeof window.fetch>[1]) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  const match = /\/api\/sessions\/(\d+)\/laps$/.exec(url);
  const laps = match && SERVED[match[1]];
  if (laps) {
    return Promise.resolve(
      new Response(JSON.stringify(laps), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }
  return realFetch(input, init);
}) as typeof window.fetch;

export function Session() {
  // The real Tsukuba session: an out-lap high on the left, the best on lap 2
  // (the accent dot), one wide lap 5. ±2.5 s over eight laps reads "loose".
  return (
    <Surface>
      <Labelled label="Tsukuba Circuit · AE86 · 8 laps">
        <LapSparkline sessionId={TSUKUBA} lapCount={8} />
      </Labelled>
    </Surface>
  );
}

export function Improving() {
  // Learning the circuit: the line falls away to the right and the best lap is
  // the last one, so the accent dot sits at the end.
  return (
    <Surface>
      <Labelled label="Suzuka Circuit · Civic Type R (EK) Touring Car · 6 laps">
        <LapSparkline sessionId={SUZUKA} lapCount={6} />
      </Labelled>
    </Surface>
  );
}

export function FallingAway() {
  // Tyres going off over a stint: best lap first, every lap slower after it.
  return (
    <Surface>
      <Labelled label="Mount Panorama · 911 GT3 R · 6 laps">
        <LapSparkline sessionId={PANORAMA} lapCount={6} />
      </Labelled>
    </Surface>
  );
}

export function ConsistencyBands() {
  // The three bands the figure is coloured by, side by side: under 0.5 % of the
  // median is a driver repeating themselves (green), past 1.5 % the laps are
  // different laps (amber).
  return (
    <Surface>
      <Row className="gap-6">
        <Labelled label="tight · Grand Valley South">
          <LapSparkline sessionId={GRAND_VALLEY} lapCount={6} />
        </Labelled>
        <Labelled label="steady · Trial Mountain">
          <LapSparkline sessionId={TRIAL_MOUNTAIN} lapCount={6} />
        </Labelled>
        <Labelled label="loose · Tsukuba">
          <LapSparkline sessionId={TSUKUBA} lapCount={8} />
        </Labelled>
      </Row>
    </Surface>
  );
}
