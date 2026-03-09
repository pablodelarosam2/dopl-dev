"""
Wire-format types matching the agent's POST /v1/events contract.

The Go agent uses PascalCase JSON keys (default Go encoding, no struct
tags), so all serialization here must emit PascalCase field names.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..fixture.schema import FixtureEvent

SCHEMA_VERSION = 1


@dataclass
class EventEnvelope:
    """Single event envelope matching the agent's ingest.Envelope struct."""

    schema_version: int
    fixture_id: str
    session_id: str
    event_type: str
    timestamp_ms: int
    payload: Dict[str, Any]
    service: str = ""
    trace: str = ""

    def to_wire(self) -> Dict[str, Any]:
        """Serialize to PascalCase dict matching the Go agent's decoder."""
        return {
            "SchemaVersion": self.schema_version,
            "FixtureID": self.fixture_id,
            "SessionID": self.session_id,
            "EventType": self.event_type,
            "TimestampMs": self.timestamp_ms,
            "Payload": self.payload,
            "Service": self.service,
            "Trace": self.trace,
        }


@dataclass
class BatchRequest:
    """Batch of envelopes matching the agent's ingest.IngestRequest."""

    envelopes: List[EventEnvelope]

    def to_wire(self) -> Dict[str, Any]:
        return {"Events": [e.to_wire() for e in self.envelopes]}

    def serialize(self) -> bytes:
        return json.dumps(
            self.to_wire(), separators=(",", ":"), default=str,
        ).encode("utf-8")


@dataclass
class BatchResponse:
    """Parsed response from the agent's ingest.IngestResponse."""

    accepted: int = 0
    dropped: int = 0
    dropped_by_reason: Dict[str, int] = field(default_factory=dict)
    invalid: int = 0

    @classmethod
    def from_wire(cls, data: Dict[str, Any]) -> BatchResponse:
        return cls(
            accepted=data.get("Accepted", 0),
            dropped=data.get("Dropped", 0),
            dropped_by_reason=data.get("DroppedByReason") or {},
            invalid=data.get("Invalid", 0),
        )


def fixture_to_envelope(
    event: FixtureEvent,
    *,
    service: str = "",
    session_id: str = "",
) -> EventEnvelope:
    """Convert an SDK FixtureEvent into a wire-format EventEnvelope.

    Maps run_id → SessionID and qualname → Trace.
    The full FixtureEvent dict is placed inside Payload as an opaque blob
    so the agent stores it without needing to understand the schema.
    """
    return EventEnvelope(
        schema_version=SCHEMA_VERSION,
        fixture_id=event.fixture_id,
        session_id=session_id or event.run_id,
        event_type=event.event_type,
        timestamp_ms=int(time.time() * 1000),
        payload=event.to_dict(),
        service=service,
        trace=event.qualname,
    )
