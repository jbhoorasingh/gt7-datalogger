import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/Tooltip";
import type { PersonalBest } from "@/lib/types";
import { BestsBoard } from "./BestsBoard";

const best: PersonalBest = {
  track_name: "Suzuka",
  car_id: 7,
  car_name: "Car 7",
  car_category: "Gr.3",
  lap_id: 2,
  session_id: 1,
  number: 2,
  time_ms: 90_000,
  finished_at: "2026-09-28T12:00:00Z",
  clean_lap: true,
  off_survey_count: 0,
  salvaged: false,
  lap_count: 3,
  excluded_faster: [{ lap_id: 1, time_ms: 89_000, reason: "race-start" }],
};

describe("BestsBoard exclusions", () => {
  it("shows the readable race-start label beside an excluded quicker lap", () => {
    const html = renderToStaticMarkup(
      <TooltipProvider><BestsBoard bests={[best]} /></TooltipProvider>,
    );
    expect(html).toContain("race start");
    expect(html).not.toContain("race-start");
  });
});
