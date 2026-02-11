"""
@sim_trace decorator for general function tracing.

Marks a function as a sim-traced boundary. Captures:
- Function input arguments as fixture input
- Function return value as golden output
- All dependency stubs captured during execution

Works with any function, not just Flask routes.
"""

import functools
import inspect
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from sim_sdk.context import SimContext, SimMode, get_context, set_context, clear_context
from sim_sdk.canonicalize import canonicalize, fingerprint


F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class FixtureEvent:
    """
    A complete fixture event containing input, output, and all captured stubs.

    This is what gets emitted to the RecordSink.
    """
    fixture_id: str
    name: str  # Function name or custom name
    run_id: str
    recorded_at: str
    recording_mode: str  # "passive" or "explicit"

    # Input (function arguments)
    input: Dict[str, Any] = field(default_factory=dict)
    input_fingerprint: str = ""

    # Output (return value)
    output: Any = None
    output_fingerprint: str = ""

    # Captured dependency stubs
    db_stubs: List[Dict[str, Any]] = field(default_factory=list)
    http_stubs: List[Dict[str, Any]] = field(default_factory=list)

    # Metadata
    duration_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "fixture_id": self.fixture_id,
            "name": self.name,
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "recording_mode": self.recording_mode,
            "input": self.input,
            "input_fingerprint": self.input_fingerprint,
            "output": self.output,
            "output_fingerprint": self.output_fingerprint,
            "db_stubs": self.db_stubs,
            "http_stubs": self.http_stubs,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }

    def to_fixture_files(self) -> Dict[str, Dict[str, Any]]:
        """
        Convert to the per-fixture file format expected by the spec.

        Returns dict with keys: input, golden_output, stubs, metadata
        """
        return {
            "input": {
                "fixture_id": self.fixture_id,
                "name": self.name,
                "args": self.input,
                "fingerprint": self.input_fingerprint,
            },
            "golden_output": {
                "fixture_id": self.fixture_id,
                "output": self.output,
                "fingerprint": self.output_fingerprint,
            },
            "stubs": {
                "fixture_id": self.fixture_id,
                "db_calls": self.db_stubs,
                "http_calls": self.http_stubs,
            },
            "metadata": {
                "fixture_id": self.fixture_id,
                "name": self.name,
                "recorded_at": self.recorded_at,
                "recording_mode": self.recording_mode,
                "run_id": self.run_id,
                "duration_ms": self.duration_ms,
                "schema_version": "1.0",
            },
        }


# Global list to collect stubs during a traced function call
# This is populated by SimDB and SimHTTP wrappers
_current_db_stubs: List[Dict[str, Any]] = []
_current_http_stubs: List[Dict[str, Any]] = []


def _reset_stub_collectors():
    """Reset the global stub collectors."""
    global _current_db_stubs, _current_http_stubs
    _current_db_stubs = []
    _current_http_stubs = []


def _get_collected_stubs() -> tuple:
    """Get the collected stubs and reset."""
    global _current_db_stubs, _current_http_stubs
    db = _current_db_stubs.copy()
    http = _current_http_stubs.copy()
    _reset_stub_collectors()
    return db, http


def add_db_stub(stub: Dict[str, Any]) -> None:
    """
    Add a DB stub to the current collection.
    Called by SimDB wrapper in record mode.
    """
    global _current_db_stubs
    _current_db_stubs.append(stub)


def add_http_stub(stub: Dict[str, Any]) -> None:
    """
    Add an HTTP stub to the current collection.
    Called by SimHTTP wrapper in record mode.
    """
    global _current_http_stubs
    _current_http_stubs.append(stub)


def sim_trace(
    func: Optional[F] = None,
    *,
    name: Optional[str] = None,
    recording_mode: str = "explicit",
) -> Union[F, Callable[[F], F]]:
    """
    Decorator to mark a function as a sim-traced boundary.

    Creates a SimContext at function entry, captures input/output,
    and emits a fixture event to the RecordSink.

    Args:
        func: The function to decorate (when used without parentheses)
        name: Custom name for the fixture (defaults to function name)
        recording_mode: "explicit" (script) or "passive" (live capture)

    Usage:
        @sim_trace
        def calculate_quote(user_id, items):
            prices = sim_db.query(...)
            return {"total": total}

        @sim_trace(name="quote_calculation")
        def calculate(user_id, items):
            ...
    """
    def decorator(f: F) -> F:
        fixture_name = name or f.__name__

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            ctx = get_context()

            # If simulation is off, just call the function
            if not ctx.is_active:
                return f(*args, **kwargs)

            # Create a new fixture ID for this invocation
            fixture_id = str(uuid.uuid4())[:8]

            # Create/update context with fixture info
            ctx.request_id = fixture_id
            ctx.reset_ordinals()

            # Reset stub collectors
            _reset_stub_collectors()

            # Capture input arguments
            sig = inspect.signature(f)
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()
            input_data = dict(bound_args.arguments)

            # Make input JSON-serializable
            input_data = _make_serializable(input_data)
            input_fp = fingerprint(input_data)

            # Start timing
            start_time = time.time()
            error_msg = None
            output = None

            try:
                # Call the actual function
                output = f(*args, **kwargs)
                return output
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                raise
            finally:
                # Calculate duration
                duration_ms = (time.time() - start_time) * 1000

                # Get collected stubs
                db_stubs, http_stubs = _get_collected_stubs()

                # Make output serializable
                output_data = _make_serializable(output)
                output_fp = fingerprint(output_data) if output is not None else ""

                # Create fixture event
                event = FixtureEvent(
                    fixture_id=fixture_id,
                    name=fixture_name,
                    run_id=ctx.run_id,
                    recorded_at=datetime.utcnow().isoformat() + "Z",
                    recording_mode=recording_mode,
                    input=input_data,
                    input_fingerprint=input_fp,
                    output=output_data,
                    output_fingerprint=output_fp,
                    db_stubs=db_stubs,
                    http_stubs=http_stubs,
                    duration_ms=round(duration_ms, 2),
                    error=error_msg,
                )

                # Emit to RecordSink in record mode
                if ctx.is_recording:
                    _emit_fixture_event(event, ctx)

        return wrapper  # type: ignore

    # Handle both @sim_trace and @sim_trace() syntax
    if func is not None:
        return decorator(func)
    return decorator


def _emit_fixture_event(event: FixtureEvent, ctx: SimContext) -> None:
    """
    Emit a fixture event to the configured RecordSink.

    In V0, we write directly to the StubStore. In future versions,
    this will use the async RecordSink pipeline.
    """
    from sim_sdk.sink import get_default_sink

    sink = get_default_sink()
    if sink is not None:
        sink.emit(event)
    elif ctx.stub_dir is not None:
        # Fallback: write directly to StubStore
        from sim_sdk.store import StubStore
        import json

        store = StubStore(ctx.stub_dir)
        fixture_files = event.to_fixture_files()

        # Create fixture directory
        fixture_dir = ctx.stub_dir / "fixtures" / event.fixture_id
        fixture_dir.mkdir(parents=True, exist_ok=True)

        # Write individual files
        for filename, data in fixture_files.items():
            filepath = fixture_dir / f"{filename}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)


def _make_serializable(value: Any) -> Any:
    """
    Convert a value to a JSON-serializable form.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, dict):
        return {str(k): _make_serializable(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_make_serializable(item) for item in value]

    # Handle datetime, date, time
    if hasattr(value, "isoformat"):
        return value.isoformat()

    # Handle Decimal
    if hasattr(value, "__float__"):
        return float(value)

    # Fallback to string representation
    return str(value)
