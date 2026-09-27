import { RaceEngineerStatus, clientId, useEngineer, useTelemetry } from "gt7-datalogger-frontend";
import type { RaceEngineerStatus as ServerStatus } from "gt7-datalogger-frontend";
import { RACE_ENGINEER, STATUS } from "../fixtures/app";
import { Panel, Surface } from "../preview-shell";

// The status rows of the /engineer page's rail card: the answer to the only
// question that page exists for — "is it going to speak?". Every row comes out
// of the engineer store and the live socket, so each cell puts both stores in
// the state it is describing.

const SERVER: ServerStatus = {
  enabled: RACE_ENGINEER.enabled,
  verbosity: RACE_ENGINEER.verbosity,
  categories: RACE_ENGINEER.categories,
  active: true,
  coaching_ready: true,
  active_client_id: "",
  clients: [],
};

// One callout waiting its turn. QueuedCallout lives in src/lib/speech.ts, which
// the bundle does not export, and the rows only read `queue.length`.
const QUEUED = [
  {
    deadline: 12_000,
    seq: 15,
    callout: {
      id: "c-lap-1063",
      event_type: "lap_time",
      text: "Lap time, fifty-nine point zero. Two tenths slower.",
      category: "lap",
      priority: 60,
      created_at_ms: 0,
      expires_at_ms: 12_000,
      ttl_ms: 12_000,
      interrupt: false,
    },
  },
] as never[];

function seed(patch: Parameters<typeof useEngineer.setState>[0], wsConnected = true) {
  useTelemetry.setState({ status: STATUS, wsConnected });
  useEngineer.setState(patch);
}

function Card() {
  return (
    <Surface width={340}>
      <Panel title="Status">
        <RaceEngineerStatus />
      </Panel>
    </Surface>
  );
}

export function Speaking() {
  // The healthy state: this browser holds the speaker role, the server is
  // producing callouts and one is waiting its turn.
  seed({
    enabled: true,
    supported: true,
    audioReady: true,
    spokenCount: 14,
    failedCount: 0,
    speechError: null,
    verbosity: "race",
    activeClientId: clientId(),
    serverStatus: { ...SERVER, active_client_id: clientId() },
    queue: QUEUED,
  });
  return <Card />;
}

export function AnotherDevice() {
  // Two browsers open on the same session: only one may speak, and this is the
  // other one. Callouts still arrive and still caption.
  seed({
    enabled: true,
    supported: true,
    audioReady: true,
    spokenCount: 0,
    speechError: null,
    verbosity: "race",
    activeClientId: "9c1f5f2e-4d31-4a0b-9d2e-7c8a1b6e0f44",
    serverStatus: { ...SERVER, active_client_id: "9c1f5f2e-4d31-4a0b-9d2e-7c8a1b6e0f44" },
    queue: [],
  });
  return <Card />;
}

export function NeedsAClick() {
  // A stored preference does not carry the browser's autoplay permission across
  // a reload: voice reads "enabled" while audio waits for a click.
  seed({
    enabled: true,
    supported: true,
    audioReady: false,
    spokenCount: 0,
    failedCount: 0,
    speechError: null,
    verbosity: "race",
    activeClientId: "",
    serverStatus: { ...SERVER, active: false },
    queue: [],
  });
  return <Card />;
}

export function SpeechFailing() {
  // The diagnosis this panel exists for: the socket is down, so nothing is
  // arriving, and the last utterances the browser did get never made a sound.
  seed(
    {
      enabled: true,
      supported: true,
      audioReady: true,
      spokenCount: 0,
      failedCount: 3,
      speechError: "no response from the speech engine",
      verbosity: "race",
      activeClientId: "",
      serverStatus: null,
      queue: [],
    },
    false,
  );
  return <Card />;
}

export function CoachVerbosity() {
  // At coach verbosity the panel adds a row for the lap-vs-lap coaching, which
  // stays quiet until several laps agree on the track distance.
  seed({
    enabled: true,
    supported: true,
    audioReady: true,
    spokenCount: 6,
    failedCount: 0,
    speechError: null,
    verbosity: "coach",
    activeClientId: clientId(),
    serverStatus: { ...SERVER, active_client_id: clientId(), coaching_ready: false },
    queue: [],
  });
  return <Card />;
}
