"""
@sim_trace decorator for general function tracing.

Record mode: execute the function, capture input args + return value,
collect all stubs from inner sim_capture/sim_db calls, emit fixture.

Replay mode: compute fingerprint from input args, look up recorded
golden output, return it WITHOUT executing the function body.

Off mode: execute function normally with zero overhead.

Fingerprint = qualname + canonical(args) + canonical(kwargs).
Supports both sync and async functions.
"""

import functools
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from .context import SimContext, SimMode, get_context
from .canonical import canonicalize_json, fingerprint

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SimStubMissError(Exception):
    """Raised when replay mode cannot find a matching recorded fixture.

    Attributes:
        qualname: Fully-qualified function name.
        input_fingerprint: SHA-256 fingerprint of the input args.
        ordinal: Call ordinal (0-based) for repeated calls with same fingerprint.
        stub_dir: Path where fixture was expected.
    """

    def __init__(
        self,
        qualname: str,
        input_fingerprint: str,
        ordinal: int,
        stub_dir: Optional[Path] = None,
    ):
        self.qualname = qualname
        self.input_fingerprint = input_fingerprint
        self.ordinal = ordinal
        self.stub_dir = stub_dir

        msg = (
            f"No recorded fixture for {qualname} "
            f"(fingerprint={input_fingerprint[:16]}, ordinal={ordinal})"
        )
        if stub_dir is not None:
            expected = stub_dir / _fixture_key(qualname, input_fingerprint, ordinal)
            msg += f"\n  Expected at: {expected}"

        super().__init__(msg)


# ---------------------------------------------------------------------------
# FixtureEvent — emitted to RecordSink during recording
# ---------------------------------------------------------------------------

@dataclass
class FixtureEvent:
    """A complete fixture event containing input, output, and captured stubs."""

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
        """Convert to JSON-serializable dictionary."""
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fixture_key(qualname: str, input_fp: str, ordinal: int) -> str:
    """Build the relative path key for a fixture file.

    Layout: {safe_qualname}/{fingerprint_short}_{ordinal}.json
    """
    safe_name = qualname.replace(".", "_").replace("<", "").replace(">", "")
    return f"{safe_name}/{input_fp[:16]}_{ordinal}.json"


def _compute_fingerprint(qualname: str, args_data: Dict[str, Any]) -> str:
    """Stable fingerprint: SHA-256 of qualname + canonical(args)."""
    return fingerprint({"qualname": qualname, "args": args_data})


def _bind_args(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Bind function args/kwargs into a serializable dict."""
    sig = inspect.signature(func)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    return {k: _make_serializable(v) for k, v in bound.arguments.items()}


def _make_serializable(value: Any) -> Any:
    """Convert a value to a JSON-serializable form."""
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
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__float__"):
        return float(value)
    return str(value)


# -- Shared helpers ---------------------------------------------------------

def _prepare_call(
    func: Callable, qualname: str, args: tuple, kwargs: dict, ctx: SimContext,
) -> tuple:
    """Compute fingerprint and ordinal for a traced call.

    Returns:
        (args_data, input_fp, ordinal)
    """
    args_data = _bind_args(func, args, kwargs)
    input_fp = _compute_fingerprint(qualname, args_data)
    ordinal = ctx.next_ordinal(input_fp)
    return args_data, input_fp, ordinal


# -- Fixture I/O -----------------------------------------------------------

def _write_fixture(event: FixtureEvent, ctx: SimContext) -> None:
    """Persist a fixture event (via sink or directly to stub_dir)."""
    data = event.to_dict()

    # Prefer sink if available
    if ctx.sink is not None:
        key = _fixture_key(event.qualname, event.input_fingerprint, event.ordinal)
        ctx.sink.write(key, data)
        return

    # Fallback: write directly to stub_dir
    if ctx.stub_dir is not None:
        key = _fixture_key(event.qualname, event.input_fingerprint, event.ordinal)
        filepath = ctx.stub_dir / key
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return

    logger.debug("No sink or stub_dir configured — fixture %s discarded", event.fixture_id)


def _read_fixture(
    qualname: str, input_fp: str, ordinal: int, stub_dir: Path,
) -> Optional[Dict[str, Any]]:
    """Read a recorded fixture from stub_dir by qualname + fingerprint + ordinal."""
    key = _fixture_key(qualname, input_fp, ordinal)
    filepath = stub_dir / key
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# -- Record emission (shared by sync/async) --------------------------------

def _emit_record(
    qualname: str,
    ctx: SimContext,
    args_data: Dict[str, Any],
    input_fp: str,
    ordinal: int,
    output: Any,
    error_msg: Optional[str],
    duration_ms: float,
    inner_stubs: List[Dict[str, Any]],
) -> None:
    """Build a FixtureEvent, write it, and push as stub if nested."""
    output_data = _make_serializable(output)
    output_fp = fingerprint(output_data) if output is not None else ""

    event = FixtureEvent(
        fixture_id=str(uuid.uuid4())[:8],
        qualname=qualname,
        run_id=ctx.run_id,
        recorded_at=datetime.now(timezone.utc).isoformat(),
        input=args_data,
        input_fingerprint=input_fp,
        output=output_data,
        output_fingerprint=output_fp,
        stubs=inner_stubs,
        duration_ms=round(duration_ms, 2),
        error=error_msg,
        ordinal=ordinal,
    )

    _write_fixture(event, ctx)

    # If we're inside an outer @sim_trace, push ourselves as a stub
    if ctx.trace_depth > 0:
        ctx.collected_stubs.append({
            "qualname": qualname,
            "input": args_data,
            "output": output_data,
            "source": "record",
        })


# -- Replay helper ----------------------------------------------------------

def _replay(
    qualname: str,
    input_fp: str,
    ordinal: int,
    args_data: Dict[str, Any],
    ctx: SimContext,
) -> Any:
    """Look up recorded fixture and return its output."""
    if ctx.stub_dir is None:
        raise SimStubMissError(qualname, input_fp, ordinal)

    fixture_data = _read_fixture(qualname, input_fp, ordinal, ctx.stub_dir)
    if fixture_data is None:
        raise SimStubMissError(qualname, input_fp, ordinal, ctx.stub_dir)

    output = fixture_data.get("output")

    # If nested, push as stub for the outer trace
    if ctx.trace_depth > 0:
        ctx.collected_stubs.append({
            "qualname": qualname,
            "input": args_data,
            "output": output,
            "source": "replay",
        })

    return output


# ---------------------------------------------------------------------------
# Public API — @sim_trace decorator
# ---------------------------------------------------------------------------

def sim_trace(
    func: Optional[F] = None,
    *,
    name: Optional[str] = None,
) -> Union[F, Callable[[F], F]]:
    """
    Decorator to mark a function as a sim-traced boundary.

    In record mode the function executes normally and a fixture event is
    emitted with {input, output, stubs}.  In replay mode the function body
    is skipped entirely and the recorded output is returned.  In off mode
    the function runs with zero overhead.

    Args:
        func: The function to decorate (when used without parentheses).
        name: Custom qualname override for the fixture key.

    Usage::

        @sim_trace
        def calculate_quote(user_id, items):
            ...

        @sim_trace(name="pricing.quote")
        def calculate(user_id, items):
            ...
    """

    def decorator(f: F) -> F:
        qualname = name or f.__qualname__

        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                ctx = get_context()
                if not ctx.is_active:
                    return await f(*args, **kwargs)

                args_data, input_fp, ordinal = _prepare_call(f, qualname, args, kwargs, ctx)

                if ctx.is_replaying:
                    return _replay(qualname, input_fp, ordinal, args_data, ctx)

                # Record mode — execute, scope inner stubs, emit fixture
                ctx.trace_depth += 1
                stubs_snapshot = len(ctx.collected_stubs)
                start = time.time()
                error_msg = None
                output = None
                try:
                    output = await f(*args, **kwargs)
                    return output
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {e}"
                    raise
                finally:
                    duration_ms = (time.time() - start) * 1000
                    ctx.trace_depth -= 1
                    inner_stubs = list(ctx.collected_stubs[stubs_snapshot:])
                    del ctx.collected_stubs[stubs_snapshot:]
                    _emit_record(qualname, ctx, args_data, input_fp, ordinal,
                                 output, error_msg, duration_ms, inner_stubs)

            return async_wrapper  # type: ignore[return-value]

        else:
            @functools.wraps(f)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                ctx = get_context()
                if not ctx.is_active:
                    return f(*args, **kwargs)

                args_data, input_fp, ordinal = _prepare_call(f, qualname, args, kwargs, ctx)

                if ctx.is_replaying:
                    return _replay(qualname, input_fp, ordinal, args_data, ctx)

                # Record mode — execute, scope inner stubs, emit fixture
                ctx.trace_depth += 1
                stubs_snapshot = len(ctx.collected_stubs)
                start = time.time()
                error_msg = None
                output = None
                try:
                    output = f(*args, **kwargs)
                    return output
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {e}"
                    raise
                finally:
                    duration_ms = (time.time() - start) * 1000
                    ctx.trace_depth -= 1
                    inner_stubs = list(ctx.collected_stubs[stubs_snapshot:])
                    del ctx.collected_stubs[stubs_snapshot:]
                    _emit_record(qualname, ctx, args_data, input_fp, ordinal,
                                 output, error_msg, duration_ms, inner_stubs)

            return sync_wrapper  # type: ignore[return-value]

    # Support both @sim_trace and @sim_trace() syntax
    if func is not None:
        return decorator(func)
    return decorator
