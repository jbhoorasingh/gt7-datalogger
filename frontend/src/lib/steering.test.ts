import { describe, expect, it } from "vitest";
import { steeringDegrees } from "./steering";

describe("steeringDegrees", () => {
  it("reads GT7's positive (left) rotation as a turn to the left", () => {
    expect(steeringDegrees(Math.PI / 2)).toBeCloseTo(-90);
    expect(steeringDegrees(-Math.PI / 2)).toBeCloseTo(90);
  });

  it("carries on past half a turn", () => {
    expect(steeringDegrees(-2.5 * Math.PI)).toBeCloseTo(450);
  });

  it("is plain zero at centre", () => {
    expect(Object.is(steeringDegrees(0), 0)).toBe(true);
  });
});
