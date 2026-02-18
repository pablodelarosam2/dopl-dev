"""
Sim context management — contextvars-based storage for simulation state.

Uses Python's contextvars.ContextVar for request-scoped state that is both
thread-safe and async-safe. Each thread and each asyncio Task gets its own
context automatically.

Environment Variables:
    SIM_MODE: Operating mode (off, record, replay)
    SIM_RUN_ID: Unique identifier for this simulation run
    SIM_STUB_DIR: Directory for stub files
"""

import os
import uuid
from contextvars import ContextVar, Token
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
    Request-scoped simulation context.

    Attributes:
        mode: Current simulation mode (off, record, replay)
        run_id: Unique identifier for this simulation run
        fixture_id: Identifier for the current fixture set
        request_id: Unique identifier for current request
        stub_dir: Directory where stubs are stored
        sink: Optional RecordSink for emitting fixtures
        ordinal_counters: Track call order per fingerprint within a request
        collected_stubs: Stubs collected from inner sim_capture/sim_db calls
        trace_depth: Current nesting depth of @sim_trace calls
    """
    mode: SimMode = SimMode.OFF
    run_id: str = ""
    fixture_id: str = ""
    request_id: str = ""
    stub_dir: Optional[Path] = None
    sink: Any = None  # Optional RecordSink (typed as Any to avoid circular import)
    ordinal_counters: Dict[str, int] = field(default_factory=dict)
    collected_stubs: List[Dict[str, Any]] = field(default_factory=list)
    trace_depth: int = 0

    def next_ordinal(self, fingerprint: str) -> int:
        """Get the next ordinal for a fingerprint and increment the counter."""
        current = self.ordinal_counters.get(fingerprint, 0)
        self.ordinal_counters[fingerprint] = current + 1
        return current

    def reset_ordinals(self) -> None:
        """Reset ordinal counters (typically at start of new request)."""
        self.ordinal_counters.clear()

    def start_new_request(self) -> str:
        """Generate a new request ID and reset all per-request state.

        Clears ordinal counters, collected stubs, and trace depth.
        """
        self.request_id = str(uuid.uuid4())[:8]
        self.reset()
        return self.request_id

    def reset(self) -> None:
        """Reset all per-request state: ordinals, stubs, and trace depth."""
        self.ordinal_counters.clear()
        self.collected_stubs.clear()
        self.trace_depth = 0

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

    # -- ContextVar class-level API -----------------------------------------

    @staticmethod
    def get_current() -> Optional["SimContext"]:
        """Return the current context, or None if not set."""
        return _context_var.get()

    @staticmethod
    def set_current(ctx: "SimContext") -> Token:
        """Set the current context and return a Token for later reset."""
        return _context_var.set(ctx)

    @staticmethod
    def reset_current(token: Token) -> None:
        """Restore the context to the value before the matching set_current()."""
        _context_var.reset(token)


# ---------------------------------------------------------------------------
# ContextVar — thread-safe + async-safe context storage
# ---------------------------------------------------------------------------

_context_var: ContextVar[Optional[SimContext]] = ContextVar(
    "sim_context", default=None,
)


def get_context() -> SimContext:
    """
    Get the current simulation context.

    If no context has been set for the current thread/task, creates one
    from environment variables (SIM_MODE, SIM_RUN_ID, SIM_STUB_DIR).
    """
    ctx = _context_var.get()
    if ctx is None:
        ctx = _create_context_from_env()
        _context_var.set(ctx)
    return ctx


def set_context(context: SimContext) -> None:
    """Set the simulation context for the current thread/task."""
    _context_var.set(context)


def clear_context() -> None:
    """Clear the simulation context for the current thread/task."""
    _context_var.set(None)


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


def init_sim(
    mode: Optional[SimMode] = None,
    run_id: Optional[str] = None,
    stub_dir: Optional[Path] = None,
    sink: Any = None,
) -> SimContext:
    """
    Initialize simulation context at app startup.

    Falls back to environment variables for unspecified values:
    SIM_MODE (default: off), SIM_RUN_ID (default: random), SIM_STUB_DIR.

    Args:
        mode: Simulation mode. Defaults to SIM_MODE env var.
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


# Backward-compatible alias
init_context = init_sim
