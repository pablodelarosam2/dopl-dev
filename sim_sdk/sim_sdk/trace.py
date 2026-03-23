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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from .context import SimContext, SimMode, get_context
from .canonical import canonicalize_json, fingerprint
from .fixture.schema import FixtureEvent
from .replay_context import get_replay_context
from .sampling import should_record

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _prepare_input(
    func: Callable, qualname: str, args: tuple, kwargs: dict,
) -> tuple:
    """Compute args_data and input_fp for a traced call.

    Ordinal is NOT computed here — record path calls ctx.next_ordinal();
    replay path calls replay_ctx.next_trace_ordinal().

    Returns:
        (args_data, input_fp)
    """
    args_data = _bind_args(func, args, kwargs)
    input_fp = _compute_fingerprint(qualname, args_data)
    return args_data, input_fp


# -- Replay helpers ---------------------------------------------------------

def _replay(
    qualname: str,
    input_fp: str,
    args_data: Dict[str, Any],
    ctx: SimContext,
) -> Any:
    """Look up the recorded output via StubStore and return it directly.

    The decorated function is NEVER executed in replay mode, including on a
    miss — arbitrary side effects (DB writes, HTTP calls, state mutations)
    must not run inside the replay sandbox.  On miss returns None and logs a
    diagnostic; the caller (verifier) is responsible for detecting the gap.
    """
    replay_ctx = get_replay_context()
    if replay_ctx is None:
        logger.warning(
            "sim_trace replay: no ReplayContext active for %r fp=%s"
            " — function NOT executed, returning None",
            qualname, input_fp[:16],
        )
        return None

    ordinal = replay_ctx.next_trace_ordinal(input_fp)
    stub = replay_ctx.stub_store.get_trace_stub(input_fp, ordinal)

    if stub is None:
        logger.warning(
            "sim_trace stub miss: %r fp=%s ordinal=%d"
            " — function NOT executed, returning None",
            qualname, input_fp[:16], ordinal,
        )
        return None

    output = stub.get("output")

    if ctx.trace_depth > 0:
        ctx.collected_stubs.append({
            "qualname": qualname,
            "input": args_data,
            "output": output,
            "source": "replay",
        })

    return output


# -- Record emission --------------------------------------------------------

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
    """Build a FixtureEvent and emit it through the configured sink."""
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
        event_type="Output",
        method=ctx.http_method,
        path=ctx.http_path,
    )

    if ctx.sink is not None:
        ctx.sink.emit(event)
    else:
        logger.debug("No sink configured — fixture %s discarded", event.fixture_id)

    if ctx.trace_depth > 0:
        ctx.collected_stubs.append({
            "qualname": qualname,
            "input": args_data,
            "output": output_data,
            "source": "record",
        })


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

                args_data, input_fp = _prepare_input(f, qualname, args, kwargs)

                if ctx.is_replaying:
                    return _replay(qualname, input_fp, args_data, ctx)

                # Sampling gate: only check at depth 0 to keep fixture trees intact
                if ctx.trace_depth == 0 and not should_record():
                    return await f(*args, **kwargs)

                ordinal = ctx.next_ordinal(input_fp)
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

                args_data, input_fp = _prepare_input(f, qualname, args, kwargs)

                if ctx.is_replaying:
                    return _replay(qualname, input_fp, args_data, ctx)

                # Sampling gate: only check at depth 0 to keep fixture trees intact
                if ctx.trace_depth == 0 and not should_record():
                    return f(*args, **kwargs)

                ordinal = ctx.next_ordinal(input_fp)
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
