"""Simulated telemetry source: drives laps around a synthetic circuit at 60 Hz.

Lets the whole stack (lap detection, storage, live dashboard) run without a
PlayStation. The track is a rounded-rectangle circuit with two hard braking
zones; laps vary slightly so comparison/deviation charts have real content.

The car is deliberately **self-consistent**: it advances along the circuit at
the speed it reports, turns at the rate its own line demands, and its
accelerometer is that motion rather than a decorative sine. Features that check
one broadcast channel against another — the g-g diagram's calibration, the
ABS/TCS intervention traces — can only be exercised without a console if the
synthetic data would pass the same check real data has to.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.models import SimulatorFlags, TelemetryPacket
from app.telemetry.packet import build_packet, parse_packet

log = logging.getLogger(__name__)

TICK = 1 / 60
TRACK_LENGTH = 3200.0  # meters
CAR_ID = 3298  # Shelby GT350R '16 in the bundled cars.csv
FUEL_PER_LAP = 1.8
# How long a simulated race streams its cool-down (past-finish packets)
# before restarting — long enough for the finish edge (#60) to be seen.
COOLDOWN_TICKS = 180  # ~3 s


@dataclass(slots=True, frozen=True)
class SimScenario:
    """Optional overrides that stage a situation worth talking about.

    The defaults reproduce the original free-practice simulation exactly —
    many tests depend on it, so a scenario only ever adds to that behavior.
    Selected with GT7_SIM_SCENARIO; see SCENARIOS below.
    """

    race_laps: int = 0  # 0 = open practice (no race distance)
    fuel_start: float = 100.0
    fuel_rate: float = 1.0  # multiplier on FUEL_PER_LAP
    temp_offset: float = 0.0  # °C added to water and oil
    oil_pressure_scale: float = 1.0
    race_positions: int = 0  # 0 = no position reporting (GT7 sends -1)
    replay: bool = False  # stream one TT-leader replay lap instead of driving


# Scenarios for exercising Race Engineer callouts without a console.
SCENARIOS: dict[str, SimScenario] = {
    "practice": SimScenario(),
    # A short race: final lap, halfway, and positions changing under you.
    "race": SimScenario(race_laps=6, race_positions=8),
    # Not enough fuel to finish: pit window, then the shortage warning.
    "fuel_shortage": SimScenario(race_laps=10, fuel_start=9.0, fuel_rate=2.5),
    "overheating": SimScenario(race_laps=6, temp_offset=35.0),
    "oil_pressure": SimScenario(race_laps=6, oil_pressure_scale=0.2),
    # The TT-leader single-lap replay shape (#26): pre-roll, one flying lap
    # streamed as lap 0, then LOADING forever — exercises lap salvage and its
    # pre-roll trimming end to end. Restart the source to run it again.
    "leader_replay": SimScenario(replay=True),
}


def scenario_for(name: str) -> SimScenario:
    return SCENARIOS.get(name, SCENARIOS["practice"])


# --- the circuit ---------------------------------------------------------------
#
# A closed parametric curve, re-parameterised by ARC LENGTH and scaled so one
# lap of it measures exactly TRACK_LENGTH. Walking the parameter at a constant
# rate instead — which is what this used to do — makes the car cover ground
# faster through the tight parts than the speed it reports, and every quantity
# derived from the path then disagrees with the telemetry: the yaw rate comes
# out several times too high, and a lateral acceleration built from it is not a
# number any real car produces. Arc length costs one table at import.

_PATH_STEPS = 4096


def _build_path() -> tuple[list[float], list[float], list[float]]:
    xs: list[float] = []
    zs: list[float] = []
    for i in range(_PATH_STEPS + 1):
        a = i / _PATH_STEPS * 2 * math.pi
        xs.append(500 * math.cos(a) + 80 * math.cos(3 * a))
        zs.append(300 * math.sin(a) + 40 * math.sin(2 * a))
    cumulative = [0.0]
    for i in range(_PATH_STEPS):
        cumulative.append(
            cumulative[-1] + math.hypot(xs[i + 1] - xs[i], zs[i + 1] - zs[i])
        )
    scale = TRACK_LENGTH / cumulative[-1]
    return (
        [c * scale for c in cumulative],
        [x * scale for x in xs],
        [z * scale for z in zs],
    )


_PATH_S, _PATH_X, _PATH_Z = _build_path()

# How hard the simulated car may corner. Whatever the driver model wants to do
# is capped at sqrt(GRIP / curvature), because a target speed picked from lap
# fraction alone knows nothing about the shape of the corner it is entering —
# and a car taken through this circuit's 46 m radius at 260 km/h broadcasts an
# eleven-g accelerometer, which is not data any feature should be tuned on.
GRIP_M_S2 = 14.0


def _build_curvature() -> list[float]:
    """Signed curvature (1/m) at each path sample.

    Measured over a window rather than between neighbouring samples: the path
    is a table of straight segments, so a one-segment difference is a staircase
    whose steps land wherever the sampling happens to fall. Curvature is what
    the whole driver model rests on — the yaw rate it broadcasts is `v × k`,
    which is exact and smooth where differencing consecutive positions each
    tick is neither.
    """
    n = _PATH_STEPS
    window = max(2, n // 256)  # ~12 m of track
    curvature = [0.0] * (n + 1)
    for i in range(n):
        a, b, c = i, (i + window) % n, (i + 2 * window) % n
        h0 = math.atan2(_PATH_Z[b] - _PATH_Z[a], _PATH_X[b] - _PATH_X[a])
        h1 = math.atan2(_PATH_Z[c] - _PATH_Z[b], _PATH_X[c] - _PATH_X[b])
        ds = math.hypot(_PATH_X[b] - _PATH_X[a], _PATH_Z[b] - _PATH_Z[a])
        turn = math.remainder(h1 - h0, math.tau)
        curvature[(i + window) % n] = turn / ds if ds > 0 else 0.0
    curvature[n] = curvature[0]
    return curvature


_PATH_K = _build_curvature()


def _sample_index(distance: float) -> tuple[int, float]:
    d = distance % TRACK_LENGTH
    i = min(max(bisect.bisect_right(_PATH_S, d) - 1, 0), _PATH_STEPS - 1)
    span = _PATH_S[i + 1] - _PATH_S[i]
    return i, (d - _PATH_S[i]) / span if span > 0 else 0.0


def _position_at(distance: float) -> tuple[float, float]:
    """World position this many meters into the lap."""
    i, f = _sample_index(distance)
    return (
        _PATH_X[i] + (_PATH_X[i + 1] - _PATH_X[i]) * f,
        _PATH_Z[i] + (_PATH_Z[i + 1] - _PATH_Z[i]) * f,
    )


def _curvature_at(distance: float) -> float:
    i, f = _sample_index(distance)
    return _PATH_K[i] + (_PATH_K[i + 1] - _PATH_K[i]) * f


def _grip_limit(distance: float) -> float:
    """Fastest this corner can be taken (m/s), looking a little way ahead so
    the driver is already slowing when it arrives rather than in it."""
    k = abs(_curvature_at(distance + 25.0))
    return math.sqrt(GRIP_M_S2 / k) if k > 1e-6 else 1e6


def _speed_profile(s: float, jitter: float) -> float:
    """Target speed (m/s) at track position s in [0, 1).

    Corner speeds are flat steps so the driver must actually brake into them
    (a gradual profile would let partial throttle track it with zero braking).
    """
    base = 62.0  # ~223 km/h baseline
    # Two corners: heavy at 20%, medium at 65%
    for center, width, depth in ((0.20, 0.05, 38.0), (0.65, 0.045, 26.0)):
        d = min(abs(s - center), 1 - abs(s - center))
        if d < width:
            base -= depth
    return max(14.0, base + jitter)


class SimTelemetrySource:
    def __init__(
        self,
        on_packet: Callable[[TelemetryPacket], Awaitable[None]],
        scenario: SimScenario | None = None,
    ) -> None:
        self._on_packet = on_packet
        self._scenario = scenario or SCENARIOS["practice"]
        self._task: asyncio.Task[None] | None = None
        self._packet_count = 0

    @property
    def connected(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def stats(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "console_ip": "simulated",
            "packets_received": self._packet_count,
            "decode_errors": 0,
            "packet_format": "C",
        }

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        log.info("simulated telemetry source started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        if self._scenario.replay:
            await self._run_replay()
            return
        rng = random.Random(42)
        sim = self._scenario
        distance = 0.0
        lap = 1
        lap_start_tick = 0
        tick = 0
        speed = 40.0
        fuel = sim.fuel_start
        # Mid-pack start, gaining a place every other lap: enough for the
        # position detector to have something to stabilize on.
        position = max(1, sim.race_positions // 2) if sim.race_positions else -1
        best_ms = -1
        last_ms = -1
        cooldown = 0  # >0 = past the checkered flag, restart when it runs out
        lap_jitter = rng.uniform(-1.5, 1.5)

        while True:
            previous_speed = speed
            s = (distance % TRACK_LENGTH) / TRACK_LENGTH
            # The profile says how fast the driver WANTS to go here; the
            # circuit's own radius says how fast the car can.
            target = min(_speed_profile(s, lap_jitter), _grip_limit(distance))
            ahead = min(
                _speed_profile((s + 0.006) % 1.0, lap_jitter),
                _grip_limit(distance + 0.006 * TRACK_LENGTH),
            )
            # Driver model: brake into corners, lift-and-coast just before the
            # brake point, full throttle on straights, hold speed otherwise.
            if target < speed - 2.0:
                throttle, brake = 0, int(min(255, (speed - target) * 22))
                speed = max(target, speed - 22.0 * TICK)
            elif ahead < speed - 5.0:
                throttle, brake = 0, 0
                speed = max(14.0, speed - 2.5 * TICK)
            elif target > speed + 0.5:
                throttle, brake = 255, 0
                speed = min(target, speed + 9.0 * TICK)
            else:
                throttle = 255 if target > 55 else int(140 + rng.uniform(-30, 30))
                brake = 0
                speed = target
            # Rate limit whatever the branches decided. Each of them can snap
            # straight onto `target` when it is close, and a 2 m/s step inside
            # one 60 Hz tick is 120 m/s² — a twelve-g spike in the broadcast
            # longitudinal accelerometer, from a car that never braked harder
            # than 2.2 g.
            speed = min(
                max(speed, previous_speed - 22.0 * TICK), previous_speed + 9.0 * TICK
            )
            distance += speed * TICK
            fuel -= FUEL_PER_LAP * sim.fuel_rate * (speed * TICK) / TRACK_LENGTH
            if fuel <= 0:
                fuel = sim.fuel_start

            new_lap = int(distance // TRACK_LENGTH) + 1
            if new_lap != lap:
                last_ms = int((tick - lap_start_tick) * TICK * 1000)
                best_ms = last_ms if best_ms < 0 else min(best_ms, last_ms)
                if sim.race_laps and new_lap > sim.race_laps:
                    # Checkered flag, the way GT7 reports it: current_lap
                    # runs past total_laps while the cool-down is driven. A
                    # few seconds of it lets the pipeline catch the finish
                    # edge (#60) before the restart below.
                    cooldown = COOLDOWN_TICKS
                lap = new_lap
                lap_start_tick = tick
                lap_jitter = rng.uniform(-1.5, 1.5)
                if position > 1 and lap % 2 == 0:
                    position -= 1
            if cooldown:
                cooldown -= 1
                if cooldown == 0:
                    # Start the race again rather than cooling down forever.
                    # The lap counter reset is exactly what a real race
                    # restart looks like to the pipeline.
                    distance, lap, fuel, best_ms = 0.0, 1, sim.fuel_start, -1
                    lap_start_tick = tick
                    position = sim.race_positions // 2 if sim.race_positions else -1

            px, pz = _position_at(distance)

            # Yaw rate the circuit itself demands at this speed, rather than
            # an arbitrary sine. Everything downstream that claims to describe
            # the car's rotation — the broadcast angular velocity, the
            # accelerometer, the steering angle, body roll — comes from this
            # one number, so the simulated car turns exactly as fast as its own
            # line says it does.
            yaw_rate = speed * _curvature_at(distance)

            gear = min(6, max(1, int(speed / 11) + 1))
            rpm = 2000 + (speed * 3.6 % 60) / 60 * 5500 + gear * 100

            # Per-wheel slip: hard braking locks the fronts, hard launches spin
            # the rears — gives the lockup/wheelspin detectors real events.
            locking = brake > 200
            spinning = throttle == 255 and speed < 25
            base_rps = speed / 0.33
            factor = [1.0, 1.0, 1.0, 1.0]  # FL FR RL RR
            if locking:
                factor[0] = 0.72 + rng.uniform(0, 0.08)
                factor[1] = 0.86 + rng.uniform(0, 0.08)
            if spinning:
                factor[2] = factor[3] = 1.18 + rng.uniform(0, 0.1)
            rps = (
                base_rps * factor[0],
                base_rps * factor[1],
                base_rps * factor[2],
                base_rps * factor[3],
            )

            # Suspension compression (m): braking loads the front axle,
            # cornering loads the outside; corner 1 apex adds a kerb strike.
            lat = yaw_rate  # the car's actual rotation, see above
            front = 0.030 + (0.028 if brake > 100 else 0.0)
            rear = 0.030 + (0.012 if throttle > 200 else 0.0)
            roll = lat * 0.02
            kerb = 0.05 if abs(s - 0.21) < 0.0008 else 0.0
            suspension = (
                front + max(0, -roll) + kerb + rng.uniform(0, 0.002),
                front + max(0, roll) + rng.uniform(0, 0.002),
                rear + max(0, -roll) + rng.uniform(0, 0.002),
                rear + max(0, roll) + rng.uniform(0, 0.002),
            )

            flags = SimulatorFlags.CAR_ON_TRACK | SimulatorFlags.IN_GEAR
            if spinning:
                flags |= SimulatorFlags.TCS_ACTIVE
            if locking:
                flags |= SimulatorFlags.ASM_ACTIVE
            if rpm > 8600:
                flags |= SimulatorFlags.REV_LIMITER

            plain = build_packet(
                packet_id=tick,
                position=(px, 10.0, pz),
                velocity=(speed, 0.0, 0.0),
                angular_velocity=(0.0, lat, 0.0),
                body_height=0.08 + rng.uniform(0, 0.01),
                engine_rpm=rpm,
                fuel_level=fuel,
                fuel_capacity=100.0,
                speed_mps=speed,
                boost=0.0,
                tire_temps=(70 + speed / 4, 71 + speed / 4, 68 + speed / 5, 69 + speed / 5),
                current_lap=lap,
                total_laps=sim.race_laps,
                best_lap_time_ms=best_ms,
                last_lap_time_ms=last_ms,
                day_progression_ms=int(tick * TICK * 1000),
                race_position=position,
                total_positions=sim.race_positions,
                flags=int(flags),
                current_gear=gear,
                suggested_gear=15,
                throttle=throttle,
                brake=brake,
                wheel_rps=rps,
                suspension=suspension,
                oil_pressure=(6.5 - rpm / 9000 * 1.5) * sim.oil_pressure_scale,
                water_temp=84.0 + (tick % 36000) / 36000 * 8 + sim.temp_offset,
                oil_temp=88.0 + (tick % 36000) / 36000 * 12 + sim.temp_offset,
                gear_ratios=(3.2, 2.3, 1.8, 1.4, 1.15, 0.95),
                transmission_top_speed=290.0,
                car_id=CAR_ID,
                # Packet C extension: exercises the full parse path in dev.
                fmt="C",
                wheel_rotation=lat * 1.2,
                # Accelerations consistent with the motion actually simulated
                # (m/s^2): lateral is v*omega with omega the yaw rate sent
                # above, longitudinal is the real speed delta. They used to be
                # arbitrary multiples, which meant the g-g diagram's
                # calibration correctly refused to trust them and the panel
                # could never be exercised in dev (#16).
                sway=speed * lat,
                surge=(speed - previous_speed) / TICK,
                # Applied pedal, i.e. after the aids acted. TCS trims throttle
                # while the rears spin, ABS bleeds brake while the fronts
                # lock — without this the two channels were byte-identical to
                # the raw ones and the intervention traces (#18) drew nothing.
                throttle_filtered=int(throttle * 0.55) if spinning else throttle,
                brake_filtered=int(brake * 0.7) if locking else brake,
                surface_types="CTTT" if kerb else "TTTT",
                lap_time_ms=int((tick - lap_start_tick) * TICK * 1000),
                wheel_steering_rad=(lat * 0.3, lat * 0.3),
                wheelbase_m=2.7,
                car_category="GRX",
            )
            self._packet_count += 1
            await self._on_packet(parse_packet(plain))
            tick += 1
            await asyncio.sleep(TICK)

    async def _run_replay(self) -> None:
        """One TT-leader replay lap, the way GT7 streams it (#26).

        A replay looks like driving on the wire — on-track flags, moving
        telemetry — but the lap counter never moves: it sits at 0 through the
        pre-roll and the flying lap, the lap clock is frozen at 0 until the
        start line and counts from the crossing, and the stream ends AT the
        finish with an endless LOADING idle that carries the lap's time. That
        ending is exactly what the salvage path exists for. The driving
        reuses _run's self-consistent physics so the salvaged lap charts like
        a real one — the g-g calibration has to accept it.
        """
        rng = random.Random(42)
        # The pre-roll joins the lap already in flight, a stretch before the
        # line: distance starts 88% of the way around with the car near the
        # straight's target speed, so the buffer opens with samples the
        # salvage trimmer has to cut.
        distance = 0.88 * TRACK_LENGTH
        speed = 55.0
        tick = 0
        lap_jitter = rng.uniform(-1.5, 1.5)
        lap_start_tick = -1  # -1 until the start line is crossed
        last_ms = -1  # set at the second crossing: the replay is over then

        while True:
            if last_ms >= 0:
                # Replay finished: GT7 sits on a loading screen while the menu
                # rebuilds, still reporting the lap it just showed. This is
                # the packet the salvage attempt keys on; the app should
                # salvage exactly one lap and then sit quiet (restart the
                # source to run the replay again).
                plain = build_packet(
                    packet_id=tick,
                    flags=int(SimulatorFlags.LOADING),
                    current_lap=0,
                    total_laps=0,
                    best_lap_time_ms=last_ms,
                    last_lap_time_ms=last_ms,
                    day_progression_ms=int(tick * TICK * 1000),
                    race_position=-1,
                    total_positions=0,
                    car_id=CAR_ID,
                    fmt="C",
                    car_category="GRX",
                )
                self._packet_count += 1
                await self._on_packet(parse_packet(plain))
                tick += 1
                await asyncio.sleep(TICK)
                continue

            previous_speed = speed
            s = (distance % TRACK_LENGTH) / TRACK_LENGTH
            target = min(_speed_profile(s, lap_jitter), _grip_limit(distance))
            ahead = min(
                _speed_profile((s + 0.006) % 1.0, lap_jitter),
                _grip_limit(distance + 0.006 * TRACK_LENGTH),
            )
            # Same driver model and rate limiting as _run — see the comments
            # there for why each branch and the clamp exist.
            if target < speed - 2.0:
                throttle, brake = 0, int(min(255, (speed - target) * 22))
                speed = max(target, speed - 22.0 * TICK)
            elif ahead < speed - 5.0:
                throttle, brake = 0, 0
                speed = max(14.0, speed - 2.5 * TICK)
            elif target > speed + 0.5:
                throttle, brake = 255, 0
                speed = min(target, speed + 9.0 * TICK)
            else:
                throttle = 255 if target > 55 else int(140 + rng.uniform(-30, 30))
                brake = 0
                speed = target
            speed = min(
                max(speed, previous_speed - 22.0 * TICK), previous_speed + 9.0 * TICK
            )
            crossing = (distance + speed * TICK) // TRACK_LENGTH > distance // TRACK_LENGTH
            distance += speed * TICK
            if crossing:
                if lap_start_tick < 0:
                    # Start line: the broadcast lap clock starts counting
                    # while the lap counter stays parked at 0.
                    lap_start_tick = tick
                else:
                    # Finish line: the flying lap is complete and the replay
                    # ends ON it — no packet ever reports the counter
                    # stepping forward.
                    last_ms = int((tick - lap_start_tick) * TICK * 1000)
                    continue

            px, pz = _position_at(distance)
            yaw_rate = speed * _curvature_at(distance)
            gear = min(6, max(1, int(speed / 11) + 1))
            rpm = 2000 + (speed * 3.6 % 60) / 60 * 5500 + gear * 100

            locking = brake > 200
            spinning = throttle == 255 and speed < 25
            base_rps = speed / 0.33
            factor = [1.0, 1.0, 1.0, 1.0]  # FL FR RL RR
            if locking:
                factor[0] = 0.72 + rng.uniform(0, 0.08)
                factor[1] = 0.86 + rng.uniform(0, 0.08)
            if spinning:
                factor[2] = factor[3] = 1.18 + rng.uniform(0, 0.1)

            lat = yaw_rate
            front = 0.030 + (0.028 if brake > 100 else 0.0)
            rear = 0.030 + (0.012 if throttle > 200 else 0.0)
            roll = lat * 0.02
            kerb = 0.05 if abs(s - 0.21) < 0.0008 else 0.0
            suspension = (
                front + max(0, -roll) + kerb + rng.uniform(0, 0.002),
                front + max(0, roll) + rng.uniform(0, 0.002),
                rear + max(0, -roll) + rng.uniform(0, 0.002),
                rear + max(0, roll) + rng.uniform(0, 0.002),
            )

            flags = SimulatorFlags.CAR_ON_TRACK | SimulatorFlags.IN_GEAR
            if spinning:
                flags |= SimulatorFlags.TCS_ACTIVE
            if locking:
                flags |= SimulatorFlags.ASM_ACTIVE
            if rpm > 8600:
                flags |= SimulatorFlags.REV_LIMITER

            plain = build_packet(
                packet_id=tick,
                position=(px, 10.0, pz),
                velocity=(speed, 0.0, 0.0),
                angular_velocity=(0.0, lat, 0.0),
                body_height=0.08 + rng.uniform(0, 0.01),
                engine_rpm=rpm,
                # Replays do not burn the watcher's fuel.
                fuel_level=100.0,
                fuel_capacity=100.0,
                speed_mps=speed,
                boost=0.0,
                tire_temps=(70 + speed / 4, 71 + speed / 4, 68 + speed / 5, 69 + speed / 5),
                # The whole point of the scenario: the counter never leaves 0
                # and the lap's time only ever appears in the LOADING idle.
                current_lap=0,
                total_laps=0,
                best_lap_time_ms=-1,
                last_lap_time_ms=-1,
                day_progression_ms=int(tick * TICK * 1000),
                race_position=-1,
                total_positions=0,
                flags=int(flags),
                current_gear=gear,
                suggested_gear=15,
                throttle=throttle,
                brake=brake,
                wheel_rps=(
                    base_rps * factor[0],
                    base_rps * factor[1],
                    base_rps * factor[2],
                    base_rps * factor[3],
                ),
                suspension=suspension,
                oil_pressure=6.5 - rpm / 9000 * 1.5,
                water_temp=84.0 + (tick % 36000) / 36000 * 8,
                oil_temp=88.0 + (tick % 36000) / 36000 * 12,
                gear_ratios=(3.2, 2.3, 1.8, 1.4, 1.15, 0.95),
                transmission_top_speed=290.0,
                car_id=CAR_ID,
                fmt="C",
                wheel_rotation=lat * 1.2,
                sway=speed * lat,
                surge=(speed - previous_speed) / TICK,
                throttle_filtered=int(throttle * 0.55) if spinning else throttle,
                brake_filtered=int(brake * 0.7) if locking else brake,
                surface_types="CTTT" if kerb else "TTTT",
                # Frozen at 0 through the pre-roll, then ms since the start
                # line — the backward-jump/frozen-run shape the trimmer keys
                # on.
                lap_time_ms=(
                    0 if lap_start_tick < 0 else int((tick - lap_start_tick) * TICK * 1000)
                ),
                wheel_steering_rad=(lat * 0.3, lat * 0.3),
                wheelbase_m=2.7,
                car_category="GRX",
            )
            self._packet_count += 1
            await self._on_packet(parse_packet(plain))
            tick += 1
            await asyncio.sleep(TICK)
