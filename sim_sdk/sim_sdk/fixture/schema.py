"""
Fixture file schemas and data models.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime


@dataclass
class FixtureEvent:
    """A complete fixture event emitted by @sim_trace during recording.

    Contains the input args, return value, collected inner stubs,
    and metadata for a single traced function call.
    """

    fixture_id: str
    qualname: str
    run_id: str
    recorded_at: str
    input: Dict[str, Any] = field(default_factory=dict)
    input_fingerprint: str = ""
    output: Any = None
    output_fingerprint: str = ""
    stubs: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    error: Optional[str] = None
    ordinal: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "qualname": self.qualname,
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "input": self.input,
            "input_fingerprint": self.input_fingerprint,
            "output": self.output,
            "output_fingerprint": self.output_fingerprint,
            "stubs": self.stubs,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "ordinal": self.ordinal,
        }
