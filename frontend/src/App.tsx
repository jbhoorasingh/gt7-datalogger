import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { StatusBar } from "@/components/StatusBar";
import { Toasts } from "@/components/ui/Toasts";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { isDashLocation, parseDashParams } from "@/lib/dash";
import { isEngineerLocation } from "@/lib/engineerRoute";
import { isOverlayLocation, parseOverlayRoute } from "@/lib/overlay";
import { parseAnalysisParams, parseHash, type Route } from "@/lib/router";
import { useTelemetry } from "@/store/telemetry";

// Every view is its own chunk (#33): an OBS overlay source or a phone on
// /dash downloads only that view's code — in particular not ECharts, which
// only the Analysis/Survey/Tracks maps use.
const AdminView = lazy(() => import("@/views/AdminView").then((m) => ({ default: m.AdminView })));
const AnalysisView = lazy(() => import("@/views/AnalysisView").then((m) => ({ default: m.AnalysisView })));
const BestsView = lazy(() => import("@/views/BestsView").then((m) => ({ default: m.BestsView })));
const DashView = lazy(() => import("@/views/DashView").then((m) => ({ default: m.DashView })));
const EngineerView = lazy(() => import("@/views/EngineerView").then((m) => ({ default: m.EngineerView })));
const LiveView = lazy(() => import("@/views/LiveView").then((m) => ({ default: m.LiveView })));
const OverlayView = lazy(() => import("@/views/OverlayView").then((m) => ({ default: m.OverlayView })));
const SessionsView = lazy(() => import("@/views/SessionsView").then((m) => ({ default: m.SessionsView })));
const SurveyView = lazy(() => import("@/views/SurveyView").then((m) => ({ default: m.SurveyView })));
const TracksView = lazy(() => import("@/views/TracksView").then((m) => ({ default: m.TracksView })));

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

export default function App() {
  const route = useRoute();
  const connect = useTelemetry((s) => s.connect);

  useEffect(() => connect(), [connect]);

  // Selection handed to Analysis via deep link / cross-view navigation.
  // Keyed on the serialized params so pasting a new URL re-applies it.
  const analysisParams = route.params.toString();
  const analysisRequest = useMemo(
    () => parseAnalysisParams(new URLSearchParams(analysisParams)),
    [analysisParams],
  );

  // Chrome-less deep links render nothing while their chunk loads — a
  // spinner would flash inside an OBS capture.
  if (isOverlayLocation(window.location)) {
    return (
      <Suspense fallback={null}>
        <OverlayView route={parseOverlayRoute(window.location)} />
      </Suspense>
    );
  }
  if (isDashLocation(window.location)) {
    return (
      <Suspense fallback={null}>
        <DashView params={parseDashParams(window.location)} />
      </Suspense>
    );
  }
  if (isEngineerLocation(window.location)) {
    return (
      <Suspense fallback={null}>
        <EngineerView />
      </Suspense>
    );
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-full flex-col">
        <StatusBar view={route.view} />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Suspense
            fallback={<div className="p-6 text-sm text-ink-dim">Loading…</div>}
          >
            {route.view === "live" && <LiveView />}
            {route.view === "analysis" && <AnalysisView request={analysisRequest} />}
            {route.view === "sessions" && <SessionsView />}
            {route.view === "bests" && <BestsView />}
            {route.view === "survey" && <SurveyView />}
            {route.view === "tracks" && <TracksView />}
            {route.view === "admin" && <AdminView />}
          </Suspense>
        </main>
        <Toasts />
      </div>
    </TooltipProvider>
  );
}
