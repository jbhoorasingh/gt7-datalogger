"""Voice callout event model plus the per-event policy table.

A callout is a *live* event, not a durable notification: it carries its own
expiry and the browser drops it rather than speaking something stale. The
policy for each event type (category, priority, time-to-live, cooldown,
whether it may interrupt) lives in SPECS so the rules are readable in one
place instead of scattered across detectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Callout categories the user can toggle individually, in display order.
# Mirrored by hand in frontend/src/lib/types.ts (CALLOUT_CATEGORIES) and
# described for users in frontend/src/lib/calloutCatalog.ts — which a test in
# tests/test_race_engineer.py checks against the SPECS table below.
CATEGORIES = (
    "system",
    "lap",
    "pace",
    "race",
    "position",
    "fuel",
    "strategy",
    "engine",
    "tires",
    "chassis",
    "coaching",
)

VERBOSITY_MODES = ("minimal", "race", "coach")
# Matches the shipped GT7_RACE_ENGINEER_VERBOSITY default. The server setting is
# a ceiling over every device, so the default has to be the maximum — capping it
# lower here would put categories out of reach with no visible cause.
DEFAULT_VERBOSITY = "coach"

# Verbosity is a coarse preset over the same category set: each mode is the
# set of categories it speaks. Explicit per-category toggles narrow it further.
_MINIMAL = frozenset({"system", "engine", "strategy", "race"})
_RACE = _MINIMAL | {"lap", "pace", "position", "fuel"}
_COACH = frozenset(CATEGORIES)

_VERBOSITY_CATEGORIES: dict[str, frozenset[str]] = {
    "minimal": _MINIMAL,
    "race": frozenset(_RACE),
    "coach": _COACH,
}


def categories_for(verbosity: str) -> frozenset[str]:
    """Categories spoken at a verbosity mode (unknown mode -> the default)."""
    return _VERBOSITY_CATEGORIES.get(verbosity, _VERBOSITY_CATEGORIES[DEFAULT_VERBOSITY])


def parse_categories(spec: str) -> set[str]:
    """Parse a comma-separated category list, dropping unknown names."""
    return {c.strip() for c in spec.split(",") if c.strip() in CATEGORIES}


@dataclass(frozen=True, slots=True)
class CalloutSpec:
    """Static policy for one event type."""

    category: str
    priority: int  # 0..100, see docs: 90+ critical, 75+ race-critical, ...
    ttl_ms: int  # how long the callout stays worth speaking
    cooldown_s: float = 0.0  # minimum packet-clock gap between emissions
    interrupt: bool = False  # may cancel speech already in progress


# Every event type the backend can emit. The frontend never needs this table
# (each callout carries its own priority/expiry), but the admin diagnostics
# panel and the docs do.
SPECS: dict[str, CalloutSpec] = {
    # system
    "engineer_enabled": CalloutSpec("system", 20, 8_000),
    "test": CalloutSpec("system", 20, 10_000),
    # lap / pace
    "lap_time": CalloutSpec("lap", 60, 10_000),
    # The final sector is emitted with the lap's own announcement and, at a
    # lower priority, waits in the browser queue while that (and any pace
    # drop) is spoken: about ten seconds at three decimals. Its lifetime has
    # to outlast that wait, or it expires unheard on every lap.
    "sector_time": CalloutSpec("lap", 55, 15_000),
    "personal_best": CalloutSpec("pace", 70, 12_000),
    "pace_drop": CalloutSpec("pace", 62, 12_000),
    # race state
    "final_lap": CalloutSpec("race", 85, 10_000),
    "race_halfway": CalloutSpec("race", 35, 10_000),
    # position
    "position_gained": CalloutSpec("position", 65, 5_000, cooldown_s=3.0),
    "position_lost": CalloutSpec("position", 65, 5_000, cooldown_s=3.0),
    # fuel / strategy
    "fuel_remaining": CalloutSpec("fuel", 30, 12_000),
    "fuel_low": CalloutSpec("fuel", 75, 12_000, cooldown_s=60.0),
    "fuel_critical": CalloutSpec("fuel", 95, 10_000, cooldown_s=30.0, interrupt=True),
    "fuel_short": CalloutSpec("strategy", 90, 15_000, interrupt=True),
    "pit_window_next": CalloutSpec("strategy", 80, 15_000),
    "pit_window_open": CalloutSpec("strategy", 80, 15_000),
    # engine health
    "water_temp_high": CalloutSpec("engine", 90, 10_000, cooldown_s=20.0),
    "water_temp_critical": CalloutSpec("engine", 95, 10_000, cooldown_s=20.0, interrupt=True),
    "oil_temp_high": CalloutSpec("engine", 90, 10_000, cooldown_s=20.0),
    "oil_temp_critical": CalloutSpec("engine", 95, 10_000, cooldown_s=20.0, interrupt=True),
    "oil_pressure_low": CalloutSpec("engine", 98, 10_000, cooldown_s=10.0, interrupt=True),
    # tires
    "tire_temp_high": CalloutSpec("tires", 45, 15_000, cooldown_s=30.0),
    "tire_imbalance": CalloutSpec("tires", 45, 20_000),
    # coaching
    "repeated_lockups": CalloutSpec("coaching", 45, 25_000),
    "repeated_wheelspin": CalloutSpec("coaching", 45, 25_000),
    "repeated_bottoming": CalloutSpec("chassis", 40, 25_000),
    "braking_early": CalloutSpec("coaching", 48, 25_000, cooldown_s=120.0),
    "braking_late": CalloutSpec("coaching", 48, 25_000, cooldown_s=120.0),
    "corner_time_loss": CalloutSpec("coaching", 50, 25_000),
}


@dataclass(slots=True)
class CalloutRequest:
    """What a detector asks for; the manager decides whether it is spoken."""

    event_type: str
    text: str
    message_key: str
    message_args: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Escalation level. A request whose severity exceeds the last emitted one
    # for the same dedupe key bypasses the cooldown ("water temperature high"
    # -> "water temperature critical" must not wait out the 20 s timer).
    severity: int = 0


@dataclass(slots=True)
class VoiceCallout:
    """A validated callout on its way to the browser."""

    id: str
    event_type: str
    text: str
    category: str
    priority: int
    created_at_ms: int
    expires_at_ms: int
    ttl_ms: int
    interrupt: bool = False
    dedupe_key: str | None = None
    message_key: str = ""
    message_args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "text": self.text,
            "category": self.category,
            "priority": self.priority,
            "created_at_ms": self.created_at_ms,
            "expires_at_ms": self.expires_at_ms,
            # The browser expires on ttl_ms measured from ITS receipt time:
            # expires_at_ms is server-clock and a phone whose clock is minutes
            # off would otherwise drop (or never drop) everything.
            "ttl_ms": self.ttl_ms,
            "interrupt": self.interrupt,
            "dedupe_key": self.dedupe_key,
            "message_key": self.message_key,
            "message_args": self.message_args,
            "metadata": self.metadata,
        }
