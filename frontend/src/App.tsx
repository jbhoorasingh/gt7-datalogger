import { useEffect, useMemo, useState } from "react";
import { StatusBar } from "@/components/StatusBar";
import { Toasts } from "@/components/ui/Toasts";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { isDashLocation, parseDashParams } from "@/lib/dash";
import { isEngineerLocation } from "@/lib/engineerRoute";
import { isOverlayLocation, parseOverlayRoute } from "@/lib/overlay";
import { parseAnalysisParams, parseHash, type Route } from "@/lib/router";
import { AdminView } from "@/views/AdminView";
import { AnalysisView } from "@/views/AnalysisView";
import { DashView } from "@/views/DashView";
import { EngineerView } from "@/views/EngineerView";
import { LiveView } from "@/views/LiveView";
import { OverlayView } from "@/views/OverlayView";
import { SessionsView } from "@/views/SessionsView";
import { SurveyView } from "@/views/SurveyView";
import { useTelemetry } from "@/store/telemetry";

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

  if (isOverlayLocation(window.location)) {
    return <OverlayView route={parseOverlayRoute(window.location)} />;
  }
  if (isDashLocation(window.location)) {
    return <DashView params={parseDashParams(window.location)} />;
  }
  if (isEngineerLocation(window.location)) {
    return <EngineerView />;
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-full flex-col">
        <StatusBar view={route.view} />
        <main className="min-h-0 flex-1 overflow-y-auto">
          {route.view === "live" && <LiveView />}
          {route.view === "analysis" && <AnalysisView request={analysisRequest} />}
          {route.view === "sessions" && <SessionsView />}
          {route.view === "survey" && <SurveyView />}
          {route.view === "admin" && <AdminView />}
        </main>
        <Toasts />
      </div>
    </TooltipProvider>
  );
}
