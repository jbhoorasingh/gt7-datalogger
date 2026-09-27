import { ConfirmDialog } from "gt7-datalogger-frontend";
import { DarkPage } from "../preview-shell";

// The dialog portals to the body and covers the page, so each cell is the whole
// card: cfg.overrides.ConfirmDialog pins cardMode "single". DarkPage puts the
// app's ground under the backdrop.
function Page({ children }: { children: React.ReactNode }) {
  return (
    <>
      <DarkPage />
      {children}
    </>
  );
}

const noop = () => {};

export function Confirm() {
  return (
    <Page>
      <ConfirmDialog
        open
        title="Reset the layout?"
        body="The dashboard goes back to the six default widgets. Any cell you have moved or resized is lost."
        confirmLabel="Reset"
        onConfirm={noop}
        onCancel={noop}
      />
    </Page>
  );
}

export function Destructive() {
  return (
    <Page>
      <ConfirmDialog
        open
        danger
        title="Delete this session?"
        body="Tsukuba Circuit · 8 laps · AE86. The laps and their telemetry go with it, and a personal best set here stops counting."
        confirmLabel="Delete session"
        onConfirm={noop}
        onCancel={noop}
      />
    </Page>
  );
}

export function TitleOnly() {
  return (
    <Page>
      <ConfirmDialog open title="Stop recording?" onConfirm={noop} onCancel={noop} />
    </Page>
  );
}
