import { BestsBoard } from "gt7-datalogger-frontend";
import type { PersonalBest } from "gt7-datalogger-frontend";
import { PERSONAL_BESTS } from "../fixtures/app";
import { Surface } from "../preview-shell";

// The Sessions view's "Bests" sub-tab: one card per circuit, one row per car,
// fastest first. The board is the only thing that panel renders, so the cells
// are the component on the app's ground at the width the sub-tab gives it.
//
// PERSONAL_BESTS is the real /api/laps/bests payload — every circuit this
// database has a counting lap for, already ordered circuit-then-time the way
// the board groups it.

// Every car in the real payload happens to be the only car that circuit was
// driven in, so no gap column is ever exercised. This is that payload's own
// Bathurst row with a second car beside it and the ruling flags a lap picks up
// over time — the one constructed cell, and the only hand-made data left here.
const BATHURST = PERSONAL_BESTS.find(
  (b) => b.track_name === "Mount Panorama Motor Racing Circuit",
)!;

const FLAGGED: PersonalBest[] = [
  {
    ...BATHURST,
    salvaged: true,
    excluded_faster: [{ lap_id: 933, time_ms: 130412, reason: "off-track" }],
  },
  {
    ...BATHURST,
    car_id: 2712,
    car_name: "M6 GT3 '16",
    lap_id: 803,
    session_id: 171,
    number: 3,
    time_ms: 133845,
    lap_count: 4,
    clean_lap: null,
    finished_at: "2026-08-21T01:12:44.900000+00:00",
  },
  {
    ...BATHURST,
    car_id: 1689,
    car_name: "Civic Type R (EK) Touring Car",
    car_category: "GRN",
    lap_id: 1018,
    session_id: 232,
    number: 6,
    time_ms: 139310,
    lap_count: 3,
    clean_lap: false,
    off_survey_count: 4,
    finished_at: "2026-09-01T02:31:09.400000+00:00",
  },
];

export function Board() {
  // The whole payload: seven circuits, best time in each header.
  return (
    <Surface width={840}>
      <BestsBoard bests={PERSONAL_BESTS} />
    </Surface>
  );
}

export function FilteredByCategory() {
  // The sub-tab's category chips filter the rows before the board sees them
  // (SessionsView's `visibleBests`) — here, the Gr.X cars only.
  return (
    <Surface width={840}>
      <BestsBoard bests={PERSONAL_BESTS.filter((b) => b.car_category === "GRX")} />
    </Surface>
  );
}

export function FlaggedLaps() {
  // Everything the board says about a time it does not fully trust, on one
  // circuit: a salvaged best (#26), a quicker lap ruled out by hand (#74), a
  // lap recorded before surface flags existed (the dash), and a dirty lap with
  // excursions past the surveyed edge — with the gaps to the fastest.
  return (
    <Surface width={840}>
      <BestsBoard bests={FLAGGED} />
    </Surface>
  );
}

export function Loading() {
  // bests == null: the sub-tab opens on skeletons while /api/laps/bests answers.
  return (
    <Surface width={840}>
      <BestsBoard bests={null} />
    </Surface>
  );
}

export function NoBests() {
  // A fresh install, or sessions whose circuit was never identified — the board
  // says which of the two it is and how to fix it.
  return (
    <Surface width={840}>
      <BestsBoard bests={[]} />
    </Surface>
  );
}
