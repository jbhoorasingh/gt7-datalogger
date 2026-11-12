// The steering wheel as drawn (#233). GT7 signs the wheel's rotation positive
// to the LEFT, and `steer_rad` carries it that way. A graphic and a readout
// want the opposite: SVG's rotate() turns clockwise for a positive angle, and
// a driver reads a turn to the right as the positive one. Both go through
// here so the picture and the number can never disagree.

/** Degrees the wheel is turned, positive to the right (clockwise). */
export function steeringDegrees(steerRad: number): number {
  return (-steerRad * 180) / Math.PI || 0; // never -0
}
