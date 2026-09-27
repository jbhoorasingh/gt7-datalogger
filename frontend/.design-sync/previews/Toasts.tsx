import { Toasts, useToasts } from "gt7-datalogger-frontend";
import { DarkPage } from "../preview-shell";

// The stack is fixed to the bottom-right of the viewport and renders nothing
// at all when the queue is empty, so each cell seeds the store and is the
// whole card: cfg.overrides.Toasts pins cardMode "single". DarkPage puts the
// app's ground under it.
//
// setState replaces the queue rather than pushing onto it: the cells share one
// page and one store, and a push would leave the previous cell's toasts
// standing.
type Kind = "info" | "success" | "error";

function seed(...toasts: [string, Kind][]) {
  useToasts.setState({
    toasts: toasts.map(([text, kind], i) => ({ id: i + 1, text, kind })),
  });
}

// Unlike the dialogs, the toast stack is NOT portalled: it is a plain
// `position: fixed` div rendered where it is mounted. The card's story root
// (`.ds-single`) carries `transform: translateZ(0)`, which makes it the
// containing block for fixed children — so without this the stack anchors to a
// zero-height box at the top of the page instead of the viewport's corner.
// Clearing the transform on the story root puts it back where the app has it.
function Stacked() {
  return (
    <>
      <DarkPage />
      <style>{`.ds-single{transform:none}`}</style>
      <Toasts />
    </>
  );
}

/** One of each kind, as they stack when several things land at once — oldest
 *  at the top, newest nearest the corner. */
export function Stack() {
  seed(
    ["Saved in-progress lap #1069", "info"],
    ["Track saved as “Tsukuba Circuit”", "success"],
    ["Could not load sessions", "error"],
  );
  return <Stacked />;
}

/** The commonest one: a single confirmation after an action in the Tracks or
 *  Sessions view. */
export function Success() {
  seed(["Survey started — go touch some kerbs", "success"]);
  return <Stacked />;
}

/** The failure the app exists to explain: UDP is blocked, or the console IP is
 *  wrong. Error toasts are the only ones a user has to act on. */
export function Failure() {
  seed(["Console not reachable on 192.168.0.24", "error"]);
  return <Stacked />;
}

/** A plain notice — nothing succeeded or failed, there was simply nothing to
 *  do. The text is as long as the app's longest message, so the wrap is real. */
export function Info() {
  seed(["No surveyed circuit matched any of the 12 unlabelled sessions", "info"]);
  return <Stacked />;
}
