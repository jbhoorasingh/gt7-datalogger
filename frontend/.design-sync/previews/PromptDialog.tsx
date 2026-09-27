import { PromptDialog } from "gt7-datalogger-frontend";
import { DarkPage } from "../preview-shell";

// The dialog portals to the body and covers the page, so each cell is the
// whole card: cfg.overrides.PromptDialog pins cardMode "single". DarkPage puts
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
const submit = (_value: string) => {};

// The circuits this installation has surveyed — what TracksView offers as the
// datalist behind a track name, so a near-miss spelling is one keystroke away.
const KNOWN_TRACKS = [
  "Tsukuba Circuit",
  "Trial Mountain Circuit",
  "Grand Valley - South",
  "Mount Panorama Motor Racing Circuit",
  "Deep Forest Raceway",
];

/** Naming an unidentified session's track, from the Sessions view. Nothing
 *  typed yet, so Save is disabled and the placeholder carries the example. */
export function NameTrack() {
  return (
    <Page>
      <PromptDialog
        open
        title="Name this track"
        label="Future sessions on this track will be identified automatically."
        placeholder="e.g. Suzuka Circuit"
        onSubmit={submit}
        onCancel={noop}
      />
    </Page>
  );
}

/** Renaming a survey bundle: a value already in the field (so the submit is
 *  live), a custom submit label, and the known-names datalist. */
export function RenameWithSuggestions() {
  return (
    <Page>
      <PromptDialog
        open
        title="Rename Tsukuba Circut"
        label="Renaming onto an existing bundle merges the two — which is the fix for one circuit living under two near-miss spellings."
        placeholder="track name"
        submitLabel="Rename"
        initialValue="Tsukuba Circuit"
        suggestions={KNOWN_TRACKS}
        onSubmit={submit}
        onCancel={noop}
      />
    </Page>
  );
}

/** The layout builder's save-as: the shortest label, the default submit, and a
 *  placeholder that names two real layouts. */
export function SaveLayout() {
  return (
    <Page>
      <PromptDialog
        open
        title="Save layout"
        label="Name this layout — the name becomes part of its URL."
        placeholder="e.g. race-strip, endurance-dash"
        onSubmit={submit}
        onCancel={noop}
      />
    </Page>
  );
}

/** Assigning an orphaned survey log to a circuit: label only, no initial
 *  value, suggestions offered. */
export function AssignSurveyRun() {
  return (
    <Page>
      <PromptDialog
        open
        title="Assign this run to a track"
        label="The log is replayed through the normal merge path, so the result is the same as having named the circuit while driving."
        placeholder="track name"
        submitLabel="Assign"
        suggestions={KNOWN_TRACKS}
        onSubmit={submit}
        onCancel={noop}
      />
    </Page>
  );
}
