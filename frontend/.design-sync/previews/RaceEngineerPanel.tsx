import { RaceEngineerPanel, clientId, useEngineer, useTelemetry } from "gt7-datalogger-frontend";
import type { RaceEngineerStatus as ServerStatus, VoiceCallout } from "gt7-datalogger-frontend";
import { RACE_ENGINEER, STATUS } from "../fixtures/app";
import { Surface } from "../preview-shell";

// The Race Engineer controls. Every cell is the dashboard's own drawer (the
// `compact` panel in DashView: 320px wide, capped at 80vh, scrolling) because
// that is the narrow column the component is written for; the last cell is the
// /engineer page's wider card, which hides the status rows and shows them in its
// own rail instead.
//
// Two browser facts have to be supplied, the same way the stores are: the
// engineer store's state, and the browser's voice list — a preview card has no
// speech engine, and with an empty list the panel (correctly) warns about it in
// every cell instead of showing the picker.

const VOICES = [
  { voiceURI: "Samantha", name: "Samantha", lang: "en-US", localService: true, default: true },
  { voiceURI: "Daniel", name: "Daniel", lang: "en-GB", localService: true, default: false },
  { voiceURI: "Karen", name: "Karen", lang: "en-AU", localService: true, default: false },
  {
    voiceURI: "Google UK English Female",
    name: "Google UK English Female",
    lang: "en-GB",
    localService: false,
    default: false,
  },
] as unknown as SpeechSynthesisVoice[];

{
  const stub = { getVoices: () => VOICES, addEventListener() {}, removeEventListener() {} };
  if ("speechSynthesis" in window) window.speechSynthesis.getVoices = () => VOICES;
  else Object.defineProperty(window, "speechSynthesis", { value: stub, configurable: true });
}

const SERVER: ServerStatus = {
  enabled: RACE_ENGINEER.enabled,
  verbosity: RACE_ENGINEER.verbosity,
  categories: RACE_ENGINEER.categories,
  active: true,
  coaching_ready: true,
  active_client_id: "",
  clients: [],
};

const SPOKEN: VoiceCallout = {
  id: "c-pace-1063",
  event_type: "personal_best",
  text: "New personal best, fifty-eight point nine six. Six tenths faster.",
  category: "pace",
  priority: 70,
  created_at_ms: 0,
  expires_at_ms: 12_000,
  ttl_ms: 12_000,
  interrupt: false,
};

// The store persists to localStorage, and every cell of a card is served from
// one origin — so each seed states the whole preference set rather than
// inheriting whatever the last story left behind.
function seed(patch: Parameters<typeof useEngineer.setState>[0], wsConnected = true) {
  useTelemetry.setState({ status: STATUS, wsConnected });
  useEngineer.setState({
    voiceURI: "",
    lang: "en-US",
    volume: 1,
    rate: 1.05,
    pitch: 1,
    categories: RACE_ENGINEER.categories,
    captions: true,
    muteWhenHidden: false,
    ...patch,
  });
}

/** DashView's drawer, as it opens over the dashboard. */
function Drawer() {
  return (
    <Surface className="p-0" width={340}>
      <div className="elevated max-h-[80vh] w-80 overflow-y-auto rounded-panel bg-panel/95 backdrop-blur">
        <div className="flex items-baseline justify-between px-3.5 py-2.5">
          <span className="section-header">Race Engineer</span>
          <button className="text-xs text-ink-dim hover:text-ink">close</button>
        </div>
        <div className="rule" />
        <RaceEngineerPanel compact />
      </div>
    </Surface>
  );
}

export function DashDrawer() {
  // Voice off — how the drawer opens on a fresh browser: one primary action,
  // and status rows that already say the server is producing callouts.
  seed({
    enabled: false,
    supported: true,
    audioReady: false,
    spokenCount: 0,
    failedCount: 0,
    speechError: null,
    verbosity: "race",
    activeClientId: "",
    serverStatus: { ...SERVER, active: false },
    lastSpoken: null,
    queue: [],
  });
  return <Drawer />;
}

export function Speaking() {
  // Armed and holding the speaker role, at coach verbosity: the disable/stop
  // pair, the extra coaching status row, and the last thing it said.
  seed({
    enabled: true,
    supported: true,
    audioReady: true,
    spokenCount: 14,
    failedCount: 0,
    speechError: null,
    verbosity: "coach",
    voiceURI: "Daniel",
    lang: "en-GB",
    activeClientId: clientId(),
    serverStatus: { ...SERVER, active_client_id: clientId() },
    lastSpoken: SPOKEN,
    queue: [],
  });
  return <Drawer />;
}

export function SpeechFailed() {
  // The browser accepted the utterance and never made a sound — the failure the
  // captions fall back for. Voice is on, so the drawer offers to disable it.
  seed({
    enabled: true,
    supported: true,
    audioReady: true,
    spokenCount: 0,
    failedCount: 3,
    speechError: "no response from the speech engine",
    verbosity: "race",
    voiceURI: "Google UK English Female",
    activeClientId: clientId(),
    serverStatus: { ...SERVER, active_client_id: clientId() },
    lastSpoken: null,
    queue: [],
  });
  return <Drawer />;
}

export function NoSpeechSupport() {
  // A browser with no speech synthesis at all (an older WebView, a locked-down
  // kiosk): the panel says so, Test voice is disabled, and the captions
  // checkbox below is the only thing that will still work.
  seed({
    enabled: false,
    supported: false,
    audioReady: false,
    spokenCount: 0,
    failedCount: 0,
    speechError: null,
    verbosity: "race",
    activeClientId: "",
    serverStatus: { ...SERVER, active: false },
    lastSpoken: null,
    queue: [],
  });
  return <Drawer />;
}

export function EngineerPage() {
  // /engineer: the same controls without the status rows (that page gives them
  // their own rail card), at the page's own width. Capped and scrolling here —
  // the page itself scrolls instead.
  seed({
    enabled: true,
    supported: true,
    audioReady: true,
    spokenCount: 6,
    failedCount: 0,
    speechError: null,
    verbosity: "race",
    voiceURI: "Samantha",
    activeClientId: clientId(),
    serverStatus: { ...SERVER, active_client_id: clientId() },
    lastSpoken: SPOKEN,
    queue: [],
  });
  return (
    <Surface className="p-0" width={520}>
      <div className="panel min-w-0 overflow-y-auto" style={{ maxHeight: 600 }}>
        <div className="section-header px-3.5 py-2.5">Voice output</div>
        <div className="rule" />
        <RaceEngineerPanel hideStatus />
      </div>
    </Surface>
  );
}
