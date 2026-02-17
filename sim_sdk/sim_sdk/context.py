"""
Sim context management - thread-local storage for simulation state.

Environment Variables:
    SIM_MODE: Operating mode (off, record, replay)
    SIM_RUN_ID: Unique identifier for this simulation run
    SIM_STUB_DIR: Directory for stub files
    SIM_FROZEN_TIME: ISO format datetime to freeze time at (optional)
"""

import os
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class SimMode(Enum):
    """Simulation operating modes."""
    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


@dataclass
class SimContext:
    """
    Thread-local simulation context.

    Attributes:
        mode: Current simulation mode (off, record, replay)
        run_id: Unique identifier for this run
        request_id: Unique identifier for current request
        stub_dir: Directory where stubs are stored
        ordinal_counters: Track call order per fingerprint within a request
    """
    mode: SimMode = SimMode.OFF
    run_id: str = ""
    fixture_id: str = ""
    request_id: str = ""
    stub_dir: Optional[Path] = None
    sink: Any = None  # Optional RecordSink (typed as Any to avoid circular import)
    ordinal_counters: Dict[str, int] = field(default_factory=dict)
    collected_stubs: List[Dict[str, Any]] = field(default_factory=list)

    def next_ordinal(self, fingerprint: str) -> int:
        """
        Get the next ordinal for a fingerprint and increment the counter.
        Used to handle multiple calls with the same fingerprint in one request.
        """
        current = self.ordinal_counters.get(fingerprint, 0)
        self.ordinal_counters[fingerprint] = current + 1
        return current

    def reset_ordinals(self) -> None:
        """Reset ordinal counters (typically at start of new request)."""
        self.ordinal_counters.clear()

    def new_request_id(self) -> str:
        """Generate and set a new request ID."""
        self.request_id = str(uuid.uuid4())[:8]
        self.reset_ordinals()
        return self.request_id

    @property
    def is_active(self) -> bool:
        """Check if simulation is active (not off mode)."""
        return self.mode != SimMode.OFF

    @property
    def is_recording(self) -> bool:
        """Check if in record mode."""
        return self.mode == SimMode.RECORD

    @property
    def is_replaying(self) -> bool:
        """Check if in replay mode."""
        return self.mode == SimMode.REPLAY


# Thread-local storage for context
_local = threading.local()


def get_context() -> SimContext:
    """
    Get the current thread's simulation context.
    Creates one from environment variables if not exists.
    """
    if not hasattr(_local, "context"):
        _local.context = _create_context_from_env()
    return _local.context


def set_context(context: SimContext) -> None:
    """Set the simulation context for the current thread."""
    _local.context = context


def clear_context() -> None:
    """Clear the simulation context for the current thread."""
    if hasattr(_local, "context"):
        delattr(_local, "context")


def _create_context_from_env() -> SimContext:
    """Create a SimContext from environment variables."""
    mode_str = os.environ.get("SIM_MODE", "off").lower()
    try:
        mode = SimMode(mode_str)
    except ValueError:
        mode = SimMode.OFF

    run_id = os.environ.get("SIM_RUN_ID", str(uuid.uuid4())[:8])
    stub_dir_str = os.environ.get("SIM_STUB_DIR")
    stub_dir = Path(stub_dir_str) if stub_dir_str else None

    return SimContext(
        mode=mode,
        run_id=run_id,
        stub_dir=stub_dir,
    )


def init_context(
    mode: Optional[SimMode] = None,
    run_id: Optional[str] = None,
    stub_dir: Optional[Path] = None,
    sink: Any = None,
) -> SimContext:
    """
    Initialize simulation context with explicit values.
    Falls back to environment variables for unspecified values.

    Args:
        mode: Simulation mode (off, record, replay). Defaults to SIM_MODE env var.
        run_id: Unique run identifier. Defaults to SIM_RUN_ID env var.
        stub_dir: Directory for fixture files. Defaults to SIM_STUB_DIR env var.
        sink: Optional RecordSink for emitting fixtures during recording.
    """
    env_context = _create_context_from_env()

    context = SimContext(
        mode=mode if mode is not None else env_context.mode,
        run_id=run_id if run_id is not None else env_context.run_id,
        stub_dir=stub_dir if stub_dir is not None else env_context.stub_dir,
        sink=sink,
    )

    set_context(context)
    return context
