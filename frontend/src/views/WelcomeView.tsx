// First-run wizard (#191) at #/welcome. Until setup is completed the
// desktop app runs the simulator with recording, mDNS and LAN access off
// (#184), so the first network prompt (Windows Firewall, macOS Local
// Network) appears only when this page has explained it and the person has
// asked to look for the console. Finishing — or Skip setup — stores the
// marker and turns recording on.

import { useEffect, useMemo, useState } from "react";
import { phoneUrl, qrPath } from "@/components/settings/qr";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Toggle } from "@/components/ui/Toggle";
import { useT, type MessageKey } from "@/i18n";
import { api } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { useInstallKind } from "@/lib/health";
import { navigate } from "@/lib/router";
import type { AdminStats, ConnectionStatus, SetupCompleted, SetupState } from "@/lib/types";
import {
  classifyProblem,
  completionBody,
  detectOs,
  preferencesPatch,
  unitsForLocale,
  type Os,
  type Problem,
} from "@/lib/welcome";
import { toast } from "@/store/toasts";

type Step = "start" | "demo" | "os" | "find" | "live" | "prefs" | "phone" | "done";

const POLL_MS = 1000;

const SCENARIOS = ["practice", "race", "fuel_shortage", "overheating", "oil_pressure", "leader_replay"];

const SCENARIO_LABEL: Record<string, MessageKey> = {
  practice: "welcome.scenario.practice",
  race: "welcome.scenario.race",
  fuel_shortage: "welcome.scenario.fuel_shortage",
  overheating: "welcome.scenario.overheating",
  oil_pressure: "welcome.scenario.oil_pressure",
  leader_replay: "welcome.scenario.leader_replay",
};

const OS_NOTE: Record<Os, MessageKey> = {
  windows: "welcome.os.windows",
  mac: "welcome.os.mac",
  linux: "welcome.os.linux",
  other: "welcome.os.other",
};

const PROBLEM_HELP: Record<Problem, MessageKey> = {
  portBusy: "welcome.problem.portBusy",
  listenerOther: "welcome.problem.listenerOther",
  localNetwork: "welcome.problem.localNetwork",
  firewall: "welcome.problem.firewall",
  refused: "welcome.problem.refused",
  noRoute: "welcome.problem.noRoute",
  heartbeatOther: "welcome.problem.heartbeatOther",
};

function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export function WelcomeView() {
  const t = useT();
  const installKind = useInstallKind();
  const desktop = installKind === "desktop";
  const os = useMemo(() => detectOs(typeof navigator === "undefined" ? undefined : navigator), []);

  const [step, setStep] = useState<Step>("start");
  const [path, setPath] = useState<"ps" | "demo">("ps");
  const [setup, setSetup] = useState<SetupState | null>(null);
  const [lanChoice, setLanChoice] = useState(true);
  const [units, setUnits] = useState<"metric" | "imperial">(() =>
    unitsForLocale(typeof navigator === "undefined" ? undefined : navigator.language),
  );
  // The background requests, each with its own switch (#191). The update
  // check is on by default only in the desktop app, as on the server.
  const [updateCheck, setUpdateCheck] = useState<boolean | null>(null);
  const [carRefresh, setCarRefresh] = useState(true);
  const [finishing, setFinishing] = useState(false);
  const [result, setResult] = useState<SetupCompleted | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);

  useEffect(() => {
    api.admin.setup().then(setSetup).catch(() => {});
  }, []);

  async function finish(skip = false) {
    setFinishing(true);
    try {
      if (!skip) {
        await api.admin.updateConfig({
          values: preferencesPatch({ units, updateCheck: updateCheck ?? desktop, carRefresh }),
        });
      }
      const done = await api.admin.completeSetup(skip ? undefined : completionBody(installKind, lanChoice));
      if (skip) {
        navigate("live");
        return;
      }
      setResult(done);
      api.admin.stats().then(setStats).catch(() => {});
      setStep("done");
    } catch (e) {
      toast(message(e, t("welcome.finishFailed")), "error");
    } finally {
      setFinishing(false);
    }
  }

  // After the console (or the demo), the phone question — only the desktop
  // app asks it: Docker and source installs already serve the LAN.
  const afterCapture = () => setStep("prefs");
  const afterPrefs = () => (desktop ? setStep("phone") : void finish());

  const tail: Step[] = desktop ? ["prefs", "phone"] : ["prefs"];
  const steps: Step[] = path === "demo" ? ["start", "demo", ...tail] : ["start", "os", "find", "live", ...tail];

  return (
    <div className="flex min-h-full items-start justify-center px-4 py-10">
      <div className="flex w-full max-w-[640px] flex-col gap-4">
        <div className="flex items-center gap-2">
          <span
            className="h-[7px] w-[7px] rounded-full bg-accent"
            style={{ boxShadow: "0 0 8px var(--color-accent)" }}
          />
          <span className="text-[13px] font-semibold">GT7 Datalogger</span>
          {step !== "done" && (
            <button
              className="ml-auto text-[11.5px] text-ink-faint hover:text-ink hover:underline"
              disabled={finishing}
              onClick={() => void finish(true)}
            >
              {t("welcome.skip")}
            </button>
          )}
        </div>

        {step !== "done" && (
          <ol className="flex gap-1.5" aria-label={t("welcome.progress")}>
            {steps.map((s) => (
              <li
                key={s}
                aria-current={s === step ? "step" : undefined}
                className={`h-1 flex-1 rounded-full ${
                  steps.indexOf(s) <= steps.indexOf(step) ? "bg-accent" : "bg-edge"
                }`}
              />
            ))}
          </ol>
        )}

        <div className="panel flex flex-col gap-4 px-6 py-5">
          {step === "start" && (
            <StartStep
              setup={setup}
              onPick={(p) => {
                setPath(p);
                setStep(p === "demo" ? "demo" : "os");
              }}
            />
          )}
          {step === "demo" && (
            <DemoStep setup={setup} onBack={() => setStep("start")} onNext={afterCapture} busy={finishing} />
          )}
          {step === "os" && (
            <Section title={t("welcome.os.title")}>
              <p className="text-[13px] text-ink-dim">{t(OS_NOTE[os])}</p>
              <p className="text-[12px] text-ink-faint">{t("welcome.os.sticky")}</p>
              <Nav onBack={() => setStep("start")} onNext={() => setStep("find")} next={t("welcome.os.ready")} />
            </Section>
          )}
          {step === "find" && (
            <FindStep
              setup={setup}
              setSetup={setSetup}
              onBack={() => setStep("os")}
              onNext={() => setStep("live")}
            />
          )}
          {step === "live" && <LiveStep onBack={() => setStep("find")} onNext={afterCapture} busy={finishing} />}
          {step === "prefs" && (
            <Section title={t("welcome.prefs.title")}>
              <p className="text-[13px] text-ink-dim">{t("welcome.prefs.body")}</p>
              <div className="flex flex-col gap-1.5">
                <span className="text-[12.5px] text-ink">{t("welcome.prefs.units")}</span>
                <SegmentedControl
                  ariaLabel={t("welcome.prefs.units")}
                  value={units}
                  onValueChange={setUnits}
                  options={[
                    { value: "metric", label: t("welcome.prefs.metric") },
                    { value: "imperial", label: t("welcome.prefs.imperial") },
                  ]}
                />
              </div>
              <PrefSwitch
                title={t("welcome.prefs.updateCheck")}
                desc={t("welcome.prefs.updateCheckDesc")}
                checked={updateCheck ?? desktop}
                onChange={setUpdateCheck}
              />
              <PrefSwitch
                title={t("welcome.prefs.carRefresh")}
                desc={t("welcome.prefs.carRefreshDesc")}
                checked={carRefresh}
                onChange={setCarRefresh}
              />
              <p className="text-[11.5px] text-ink-faint">{t("welcome.prefs.later")}</p>
              <Nav
                onBack={() => setStep(path === "demo" ? "demo" : "live")}
                onNext={afterPrefs}
                next={
                  desktop ? t("common.next") : finishing ? t("welcome.finishing") : t("welcome.finish")
                }
                disabled={finishing}
              />
            </Section>
          )}
          {step === "phone" && (
            <Section title={t("welcome.phone.title")}>
              <p className="text-[13px] text-ink-dim">{t("welcome.phone.body")}</p>
              <div role="radiogroup" aria-label={t("welcome.phone.title")} className="flex flex-col gap-2">
                <Choice
                  on={lanChoice}
                  onClick={() => setLanChoice(true)}
                  title={t("welcome.phone.yes")}
                  desc={t("welcome.phone.yesDesc")}
                />
                <Choice
                  on={!lanChoice}
                  onClick={() => setLanChoice(false)}
                  title={t("welcome.phone.no")}
                  desc={t("welcome.phone.noDesc")}
                />
              </div>
              <Nav
                onBack={() => setStep("prefs")}
                onNext={() => void finish()}
                next={finishing ? t("welcome.finishing") : t("welcome.finish")}
                disabled={finishing}
              />
            </Section>
          )}
          {step === "done" && result && <DoneStep result={result} stats={stats} />}
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <h1 className="text-[17px] font-medium">{title}</h1>
      {children}
    </>
  );
}

function Nav({
  onBack,
  onNext,
  next,
  disabled = false,
  extra,
}: {
  onBack?: () => void;
  onNext: () => void;
  next: string;
  disabled?: boolean;
  extra?: React.ReactNode;
}) {
  const t = useT();
  return (
    <div className="flex flex-wrap items-center gap-2.5 pt-1">
      {onBack && (
        <button className="btn" onClick={onBack}>
          {t("common.back")}
        </button>
      )}
      {extra}
      <button className="btn btn-primary ml-auto" onClick={onNext} disabled={disabled}>
        {next}
      </button>
    </div>
  );
}

function PrefSwitch({
  title,
  desc,
  checked,
  onChange,
}: {
  title: string;
  desc: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-edge px-3.5 py-2.5">
      <div className="flex flex-1 flex-col gap-1">
        <span className="text-[13px] text-ink">{title}</span>
        <span className="text-[11.5px] text-ink-dim">{desc}</span>
      </div>
      <Toggle checked={checked} onCheckedChange={onChange} ariaLabel={title} />
    </div>
  );
}

function Choice({
  on,
  onClick,
  title,
  desc,
}: {
  on: boolean;
  onClick: () => void;
  title: string;
  desc: string;
}) {
  return (
    <button
      role="radio"
      aria-checked={on}
      onClick={onClick}
      className={`flex flex-col gap-1 rounded-md border px-3.5 py-2.5 text-left transition-colors ${
        on ? "border-accent/60 bg-accent/8" : "border-edge hover:border-edge-bright"
      }`}
    >
      <span className={`text-[13px] ${on ? "text-accent-300" : "text-ink"}`}>{title}</span>
      <span className="text-[11.5px] text-ink-dim">{desc}</span>
    </button>
  );
}

function StartStep({ setup, onPick }: { setup: SetupState | null; onPick: (p: "ps" | "demo") => void }) {
  const t = useT();
  return (
    <Section title={t("welcome.start.title")}>
      <p className="text-[13px] text-ink-dim">{t("welcome.start.body")}</p>
      {setup?.completed && <p className="text-[12px] text-warn">{t("welcome.start.again")}</p>}
      <div className="flex flex-col gap-2">
        <Choice
          on={false}
          onClick={() => onPick("ps")}
          title={t("welcome.start.ps")}
          desc={t("welcome.start.psDesc")}
        />
        <Choice
          on={false}
          onClick={() => onPick("demo")}
          title={t("welcome.start.demo")}
          desc={t("welcome.start.demoDesc")}
        />
      </div>
      <p className="text-[11.5px] text-ink-faint">{t("welcome.start.network")}</p>
    </Section>
  );
}

function DemoStep({
  setup,
  onBack,
  onNext,
  busy,
}: {
  setup: SetupState | null;
  onBack: () => void;
  onNext: () => void;
  busy: boolean;
}) {
  const t = useT();
  const [scenarios, setScenarios] = useState<string[]>(SCENARIOS);
  const [current, setCurrent] = useState("practice");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.admin
      .config()
      .then((doc) => {
        const row = doc.settings.find((s) => s.key === "sim_scenario");
        if (row?.choices?.length) setScenarios(row.choices);
        if (typeof row?.value === "string" && row.value) setCurrent(row.value);
      })
      .catch(() => {});
    // Back on the simulator, if the PlayStation path had already left it.
    if (setup && setup.source !== "sim") void api.admin.updateSettings({ source: "sim" }).catch(() => {});
  }, [setup]);

  async function pick(name: string) {
    setSaving(true);
    try {
      await api.admin.updateConfig({ values: { sim_scenario: name } });
      setCurrent(name);
    } catch (e) {
      toast(message(e, t("welcome.demo.failed")), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section title={t("welcome.demo.title")}>
      <p className="text-[13px] text-ink-dim">{t("welcome.demo.body")}</p>
      <div role="radiogroup" aria-label={t("welcome.demo.title")} className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {scenarios.map((name) => (
          <Choice
            key={name}
            on={name === current}
            onClick={() => !saving && void pick(name)}
            title={SCENARIO_LABEL[name] ? t(SCENARIO_LABEL[name]) : name}
            desc={SCENARIO_LABEL[name] ? t(`${SCENARIO_LABEL[name]}Desc` as MessageKey) : ""}
          />
        ))}
      </div>
      <p className="text-[11.5px] text-ink-faint">{t("welcome.demo.later")}</p>
      <Nav onBack={onBack} onNext={onNext} next={t("common.next")} disabled={busy} />
    </Section>
  );
}

function FindStep({
  setup,
  setSetup,
  onBack,
  onNext,
}: {
  setup: SetupState | null;
  setSetup: (s: SetupState) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const t = useT();
  type Phase = "idle" | "switching" | "searching" | "found" | "notFound";
  const [phase, setPhase] = useState<Phase>("idle");
  const [found, setFound] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [manual, setManual] = useState("");
  const [saving, setSaving] = useState(false);

  async function refreshSetup() {
    try {
      setSetup(await api.admin.setup());
    } catch {
      // the help below just has less to go on
    }
  }

  async function search() {
    setNote(null);
    setFound(null);
    try {
      // Switching to the PlayStation source opens UDP 33740 and starts the
      // heartbeat: this is the moment the system's prompt appears.
      setPhase("switching");
      await api.admin.updateSettings({ source: "udp" });
      setPhase("searching");
      const r = await api.admin.discoverConsole();
      if (r.found && r.ip) {
        await api.admin.updateSettings({ ps_ip: r.ip });
        setFound(r.ip);
        setPhase("found");
      } else {
        if (r.reason) setNote(t("settings.connection.cantSearch", { reason: r.reason }));
        setPhase("notFound");
      }
    } catch (e) {
      setNote(message(e, t("settings.connection.searchFailed")));
      setPhase("notFound");
    } finally {
      void refreshSetup();
    }
  }

  async function saveManual() {
    const ip = manual.trim();
    if (!ip) return;
    setSaving(true);
    try {
      if (setup?.source !== "udp") await api.admin.updateSettings({ source: "udp" });
      await api.admin.updateSettings({ ps_ip: ip });
      onNext();
    } catch (e) {
      setNote(message(e, t("welcome.find.saveFailed")));
      void refreshSetup();
    } finally {
      setSaving(false);
    }
  }

  const problem = classifyProblem(setup?.listener_error, setup?.heartbeat_error);
  const busy = phase === "switching" || phase === "searching";

  return (
    <Section title={t("welcome.find.title")}>
      <p className="text-[13px] text-ink-dim">{t("welcome.find.body")}</p>

      {phase === "found" && found ? (
        <p role="status" className="text-[13px] text-throttle">
          {t("welcome.find.found", { ip: found })}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2.5">
          <button className="btn btn-primary" disabled={busy} onClick={() => void search()}>
            {phase === "switching"
              ? t("welcome.find.starting")
              : phase === "searching"
                ? t("settings.connection.searching")
                : phase === "notFound"
                  ? t("welcome.find.retry")
                  : t("welcome.find.search")}
          </button>
          {phase === "notFound" && <span className="text-[12px] text-warn">{t("settings.connection.notFound")}</span>}
        </div>
      )}

      {note && <p className="text-[12px] text-ink-dim">{note}</p>}
      {problem && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn/8 px-3 py-2 text-[12px] text-warn">
          {t(PROBLEM_HELP[problem], {
            error: setup?.listener_error ?? "",
            reason: setup?.heartbeat_error?.reason ?? "",
          })}
        </p>
      )}

      {phase !== "found" && (
        <div className="flex flex-col gap-1.5">
          <label htmlFor="welcome-ip" className="text-[12.5px]">
            {t("welcome.find.manual")}
          </label>
          <div className="flex gap-2">
            <input
              id="welcome-ip"
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void saveManual()}
              placeholder="192.168.1.20"
              autoComplete="off"
              className="min-w-0 flex-1 rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-[13px] text-ink placeholder:text-ink-ghost focus:border-accent focus:outline-none"
            />
            <button className="btn shrink-0" disabled={!manual.trim() || saving} onClick={() => void saveManual()}>
              {t("welcome.find.use")}
            </button>
          </div>
          <span className="text-[11px] text-ink-faint">{t("welcome.find.manualHelp")}</span>
        </div>
      )}

      <Nav onBack={onBack} onNext={onNext} next={t("common.next")} disabled={phase !== "found"} />
    </Section>
  );
}

function LiveStep({ onBack, onNext, busy }: { onBack: () => void; onNext: () => void; busy: boolean }) {
  const t = useT();
  const [status, setStatus] = useState<ConnectionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .status()
        .then((s) => {
          if (!alive) return;
          setStatus(s);
          setError(null);
        })
        .catch((e) => alive && setError(message(e, t("welcome.live.statusFailed"))));
    poll();
    const id = window.setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [t]);

  const connected = status?.connected ?? false;
  const problem = classifyProblem(status?.listener_error, status?.heartbeat_error);
  // Packets that arrive but do not decrypt: usually a format the console
  // (or this GT7 version) does not answer properly. A is the safe one.
  const suggestA = !connected && (status?.decode_errors ?? 0) > 0 && status?.packet_format !== "A";

  async function useFormatA() {
    try {
      await api.admin.updateSettings({ packet_format: "A" });
      toast(t("welcome.live.formatSet"), "success");
    } catch (e) {
      toast(message(e, t("welcome.live.formatFailed")), "error");
    }
  }

  return (
    <Section title={t("welcome.live.title")}>
      <p className="text-[13px] text-ink-dim">{t("welcome.live.body")}</p>
      <p role="status" className={`text-[13px] ${connected ? "text-throttle" : "text-ink"}`}>
        {connected
          ? t("settings.connection.receivingFrom", { ip: status?.console_ip || t("settings.connection.consoleWord") })
          : t("welcome.live.waiting", { n: status?.packets_received ?? 0 })}
      </p>
      {suggestA && (
        <div className="flex flex-wrap items-center gap-2.5 rounded-md border border-warn/40 bg-warn/8 px-3 py-2">
          <span className="flex-1 text-[12px] text-warn">
            {t("welcome.live.decodeErrors", { n: status?.decode_errors ?? 0 })}
          </span>
          <button className="btn" onClick={() => void useFormatA()}>
            {t("welcome.live.useA")}
          </button>
        </div>
      )}
      {problem && (
        <p role="alert" className="rounded-md border border-warn/40 bg-warn/8 px-3 py-2 text-[12px] text-warn">
          {t(PROBLEM_HELP[problem], {
            error: status?.listener_error ?? "",
            reason: status?.heartbeat_error?.reason ?? "",
          })}
        </p>
      )}
      {error && <p className="text-[12px] text-warn">{error}</p>}
      <Nav
        onBack={onBack}
        onNext={onNext}
        next={connected ? t("common.next") : t("welcome.live.skip")}
        disabled={busy}
      />
    </Section>
  );
}

function DoneStep({ result, stats }: { result: SetupCompleted; stats: AdminStats | null }) {
  const t = useT();
  const url = result.lan_reachable && stats?.lan_ip ? phoneUrl(stats.lan_ip, stats.http_port) : null;
  const qr = useMemo(() => (url ? qrPath(url) : null), [url]);
  const token = result.admin_token;

  return (
    <Section title={t("welcome.done.title")}>
      <p className="text-[13px] text-ink-dim">
        {result.source === "sim" ? t("welcome.done.bodyDemo") : t("welcome.done.body")}
      </p>
      {url && qr && (
        <div className="flex flex-wrap items-center gap-4">
          <svg
            role="img"
            aria-label={t("settings.phone.qr", { url })}
            viewBox={`0 0 ${qr.size} ${qr.size}`}
            shapeRendering="crispEdges"
            className="h-40 w-40 rounded-md"
          >
            {/* Dark on light whatever the theme: phone cameras read that. */}
            <rect width={qr.size} height={qr.size} fill="#fff" />
            <path d={qr.d} fill="#000" />
          </svg>
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-[12.5px]">{t("welcome.done.scan")}</span>
            <span className="font-tabular text-[13px]">{url}</span>
          </div>
        </div>
      )}
      {token && (
        <div className="flex flex-col gap-1.5 rounded-md border border-edge px-3.5 py-3">
          <span className="text-[12.5px]">{t("welcome.done.token")}</span>
          <div className="flex flex-wrap items-center gap-2">
            <code className="select-all break-all rounded bg-panel-2 px-2 py-1 font-tabular text-[13px]">{token}</code>
            <button
              className="btn"
              onClick={() =>
                void copyText(token).then((ok) =>
                  toast(ok ? t("common.copied") : t("clipboard.selected"), ok ? "success" : "error"),
                )
              }
            >
              {t("common.copy")}
            </button>
          </div>
          <span className="text-[11px] text-ink-faint">{t("welcome.done.tokenHelp")}</span>
        </div>
      )}
      {!result.lan_access && <p className="text-[12px] text-ink-faint">{t("welcome.done.lanOff")}</p>}
      <div className="flex justify-end pt-1">
        <button className="btn btn-primary" onClick={() => navigate("live")}>
          {t("welcome.done.open")}
        </button>
      </div>
    </Section>
  );
}

