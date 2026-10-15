"""The live client protocol: what /ws/live sends and accepts, as models.

The browser UI and native apps (the phone client) share one WebSocket. A
client that says nothing gets exactly what the browser always got: compact
telemetry at GT7_WS_RATE and every event. A client that opens with `hello`
declares the protocol it speaks and subscribes to what it wants — its own
telemetry rate up to the full 60 Hz, the `full` frame with every packet field,
and which event types to receive — and is answered with `welcome`.

Versioning: PROTOCOL_VERSION only goes up for a change an existing client
would misread. Adding a message type, a field, or a capability is not one —
clients ignore what they do not know — so most releases leave it alone.

These models are the contract; scripts/export_schemas.py writes them out as
JSON Schema under docs/reference/schemas/ws/ for code generators, and the
tests validate real frames against them so the two cannot drift apart.
"""

from __future__ import annotations

import dataclasses
import typing
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.models import TelemetryPacket

PROTOCOL_VERSION = 1
MAX_TELEMETRY_HZ = 60
FRAME_KINDS = ("compact", "full")

# Every event type the server sends besides `telemetry`. A client's
# `subscribe.events` picks from these; `status` and `welcome` always arrive.
EVENT_TYPES = (
    "lap",
    "session",
    "status",
    "survey",
    "voice_callout",
    "voice_output_status",
    "race_engineer_status",
)
ALWAYS_SENT = frozenset({"status", "welcome", "error"})

# The packet fields of the `full` frame, in declaration order.
PACKET_FIELDS = tuple(f.name for f in dataclasses.fields(TelemetryPacket))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    # Payloads that grow: clients must ignore fields they do not know.
    model_config = ConfigDict(extra="allow")


# --- client -> server -------------------------------------------------------


class Subscribe(_Open):
    telemetry_hz: float | None = Field(
        default=None,
        ge=1,
        le=MAX_TELEMETRY_HZ,
        description="Telemetry frames per second; omitted = the server's GT7_WS_RATE.",
    )
    frame: Literal["compact", "full"] = Field(
        default="compact",
        description="`full` adds `packet`: every parsed field of the latest packet.",
    )
    events: list[str] | None = Field(
        default=None,
        description="Event types to receive; omitted = all. `status` and `welcome` "
        "are always sent.",
    )


class Hello(_Open):
    protocol: int = Field(description="The protocol version the client speaks.")
    client_id: str = Field(default="", max_length=64)
    app: str = Field(default="", max_length=64, description='e.g. "gt7-phone"')
    app_version: str = Field(default="", max_length=32)
    subscribe: Subscribe = Field(default_factory=Subscribe)


class HelloMessage(_Strict):
    type: Literal["hello"]
    data: Hello


class SubscribeMessage(_Strict):
    type: Literal["subscribe"]
    data: Subscribe


# The browser's voice protocol (frontend/src/store/engineer.ts). Native apps
# speak callouts themselves and never send these.
class ClientCapabilitiesMessage(_Strict):
    type: Literal["client_capabilities"]
    data: dict[str, Any]


class VoiceClaimMessage(_Strict):
    type: Literal["claim_voice_output", "release_voice_output", "voice_callout_ack"]
    data: dict[str, Any]


# --- server -> client -------------------------------------------------------


class Capabilities(_Strict):
    max_telemetry_hz: int
    default_telemetry_hz: int
    frames: list[str]
    events: list[str]


class Welcome(_Strict):
    protocol: int
    server: Literal["gt7-datalogger"] = "gt7-datalogger"
    server_version: str
    lan_ip: str
    http_port: int
    capabilities: Capabilities
    subscribe: Subscribe = Field(description="The subscription in effect, after clamping.")


class WelcomeMessage(_Strict):
    type: Literal["welcome"]
    data: Welcome


class ProtocolError(_Strict):
    message: str
    request: str = Field(description="The client message type it answers.")


class ErrorMessage(_Strict):
    """A client message the server could not accept; nothing was changed."""

    type: Literal["error"]
    data: ProtocolError


class TelemetryFrame(_Strict):
    """The compact frame: what the live view and dash draw from."""

    on_track: bool
    paused: bool
    speed_kmh: float
    rpm: int
    rpm_alert: float
    gear: int
    suggested_gear: int = Field(description="15 = no suggestion")
    throttle: float = Field(description="0–100 %")
    brake: float = Field(description="0–100 %")
    boost: float
    fuel_level: float
    fuel_capacity: float
    current_lap: int
    total_laps: int
    best_lap_ms: int
    last_lap_ms: int
    position: int
    total_positions: int
    tire_temps: list[float] = Field(description="FL, FR, RL, RR")
    tire_slip: float
    water_temp: float
    oil_temp: float
    oil_pressure: float
    aids: int
    surface: int | None = Field(
        description="Four wheels' surface codes packed; null below packet C."
    )
    car_id: int
    car_name: str
    car_category: str
    session_best_ms: int
    prev_best_ms: int
    delta_ms: float | None = Field(description="Live delta to the session best, at the same place.")
    lap_elapsed_ms: int
    pos_x: float
    pos_z: float
    tod_ms: int
    track_name: str
    steer_rad: float | None


# Every TelemetryPacket field with its type, built from the dataclass itself
# so the schema can never fall behind the parser. Fields of the extended
# formats default to None in the dataclass and are null in the frame below
# the packet format that carries them.
_PACKET_HINTS = typing.get_type_hints(TelemetryPacket)
Packet = create_model(  # type: ignore[call-overload]
    "Packet",
    __config__=ConfigDict(extra="forbid"),
    __doc__="Every parsed field of the newest packet, unrounded (app.models.TelemetryPacket).",
    **{
        f.name: (
            _PACKET_HINTS[f.name],
            ... if f.default is dataclasses.MISSING else f.default,
        )
        for f in dataclasses.fields(TelemetryPacket)
    },
)


class FullTelemetryFrame(TelemetryFrame):
    packet: Packet = Field(  # type: ignore[valid-type]
        description="Every TelemetryPacket field, unrounded; extended-format fields "
        "are null below the packet format that carries them."
    )


class TelemetryMessage(_Strict):
    type: Literal["telemetry"]
    data: TelemetryFrame | FullTelemetryFrame


class Status(_Open):
    """Sent on connect, and as `session` when the session changes."""

    protocol: int
    source: str
    recording: bool
    session_id: int | None
    track_name: str
    frames_dropped: int
    connected: bool
    console_ip: str
    packets_received: int
    decode_errors: int
    packet_format: str


class StatusMessage(_Strict):
    type: Literal["status", "session"]
    data: Status


class LapEvent(_Open):
    """A lap was saved. The summary fields of GET /api/sessions/{id}/laps, plus
    the metrics computed as it closed."""

    id: int
    session_id: int | None
    number: int
    time_ms: int
    car_id: int
    counts_for_best: bool
    full_lap: bool
    salvaged: bool


class LapMessage(_Strict):
    type: Literal["lap"]
    data: LapEvent


class OtherMessage(_Strict):
    """survey, voice_callout, voice_output_status, race_engineer_status: open
    payloads documented in docs/reference/api.md."""

    type: Literal["survey", "voice_callout", "voice_output_status", "race_engineer_status"]
    data: dict[str, Any]


ClientMessage = (
    HelloMessage | SubscribeMessage | ClientCapabilitiesMessage | VoiceClaimMessage
)
ServerMessage = (
    WelcomeMessage | ErrorMessage | TelemetryMessage | StatusMessage | LapMessage | OtherMessage
)


def json_schemas() -> dict[str, dict[str, Any]]:
    """The protocol as JSON Schema, one document per direction."""
    from pydantic import TypeAdapter

    def schema(tp: Any, title: str) -> dict[str, Any]:
        doc = TypeAdapter(tp).json_schema()
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": title,
            "x-protocol-version": PROTOCOL_VERSION,
            **doc,
        }

    return {
        "client-message": schema(ClientMessage, "GT7 Datalogger live client → server message"),
        "server-message": schema(ServerMessage, "GT7 Datalogger live server → client message"),
    }
