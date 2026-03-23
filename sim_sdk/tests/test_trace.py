"""
Tests for @sim_trace decorator using plain Python functions.

Covers all acceptance criteria:
  1. Off mode: function behaves identically
  2. Record mode: function executes, FixtureEvent emitted to sink
  3. Replay mode: function body NOT executed, recorded output returned via StubStore
  4. Replay stub miss: returns None, function NOT executed, warning logged
  5. Works on plain sync functions
  6. Works on async functions
  7. Nested @sim_trace: inner results become stubs of outer
  8. Fingerprint stable across runs for same inputs
  9. Zero framework imports in trace.py
"""

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from sim_sdk.context import SimContext, SimMode, get_context, set_context, clear_context
from sim_sdk.fixture.schema import FixtureEvent
from sim_sdk.replay_context import ReplayContext
from sim_sdk.trace import (
    sim_trace,
    _compute_fingerprint,
    _make_serializable,
)


# ---------------------------------------------------------------------------
# CollectSink — in-memory sink for assertions and fixture.json export
# ---------------------------------------------------------------------------

class CollectSink:
    """In-memory sink that collects FixtureEvents and can write a fixture.json."""

    def __init__(self):
        self.events: list = []

    def emit(self, event: FixtureEvent) -> None:
        self.events.append(event)

    def to_fixture_json(self, fixture_dir: Path, fixture_id: str) -> None:
        """Write all collected events to a StubStore-compatible fixture.json."""
        fixture_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "stubs": [e.to_dict() for e in self.events],
        }
        (fixture_dir / f"{fixture_id}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_context():
    """Ensure each test starts with a fresh context."""
    clear_context()
    yield
    clear_context()


def make_record_ctx(run_id: str = "test-run"):
    """Create a record-mode SimContext with a CollectSink. Returns (ctx, sink)."""
    sink = CollectSink()
    ctx = SimContext(mode=SimMode.RECORD, run_id=run_id, sink=sink)
    set_context(ctx)
    return ctx, sink


def make_replay_sim_ctx(run_id: str = "test-run") -> SimContext:
    """Set a SimContext in REPLAY mode. Stub lookup is via ReplayContext/StubStore."""
    ctx = SimContext(mode=SimMode.REPLAY, run_id=run_id)
    set_context(ctx)
    return ctx


# ---------------------------------------------------------------------------
# AC1: Off mode — function behaves identically
# ---------------------------------------------------------------------------

class TestOffMode:
    def test_passthrough(self):
        """Decorated function returns correct result in off mode."""
        @sim_trace
        def add(a, b):
            return a + b

        result = add(2, 3)
        assert result == 5

    def test_no_events_emitted(self):
        """No FixtureEvents emitted when mode is off."""
        ctx = SimContext(mode=SimMode.OFF, run_id="test")
        set_context(ctx)

        @sim_trace
        def add(a, b):
            return a + b

        add(2, 3)
        assert ctx.collected_stubs == []

    def test_exception_passthrough(self):
        """Exceptions propagate normally in off mode."""
        @sim_trace
        def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            failing()


# ---------------------------------------------------------------------------
# AC2: Record mode — function executes, FixtureEvent emitted to sink
# ---------------------------------------------------------------------------

class TestRecordMode:
    def test_function_executes(self):
        """Function body actually runs in record mode."""
        call_log = []

        @sim_trace
        def add(a, b):
            call_log.append((a, b))
            return a + b

        make_record_ctx()
        result = add(2, 3)

        assert result == 5
        assert call_log == [(2, 3)]

    def test_event_emitted_to_sink(self):
        """One FixtureEvent is emitted per function call."""
        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        add(2, 3)

        assert len(sink.events) == 1

    def test_event_contents(self):
        """Emitted event has correct input, output, qualname, run_id."""
        @sim_trace
        def multiply(a, b):
            return a * b

        ctx, sink = make_record_ctx()
        multiply(4, 5)

        event = sink.events[0]
        assert event.input["a"] == 4
        assert event.input["b"] == 5
        assert event.output == 20
        assert event.run_id == "test-run"
        assert event.error is None
        assert event.input_fingerprint != ""
        assert event.output_fingerprint != ""
        assert event.duration_ms >= 0

    def test_exception_recorded(self):
        """Exception is recorded in the event's error field."""
        @sim_trace
        def failing(x):
            raise RuntimeError(f"bad input: {x}")

        ctx, sink = make_record_ctx()

        with pytest.raises(RuntimeError, match="bad input: 42"):
            failing(42)

        assert len(sink.events) == 1
        assert sink.events[0].error is not None
        assert "RuntimeError" in sink.events[0].error
        assert "bad input: 42" in sink.events[0].error

    def test_no_sink_configured(self):
        """When no sink is set, function still executes and returns correctly."""
        ctx = SimContext(mode=SimMode.RECORD, run_id="test")
        set_context(ctx)

        @sim_trace
        def add(a, b):
            return a + b

        result = add(2, 3)
        assert result == 5


# ---------------------------------------------------------------------------
# AC3: Replay mode — body NOT executed, recorded output returned via StubStore
# ---------------------------------------------------------------------------

class TestReplayMode:
    def test_skips_body(self, tmp_path):
        """Function body does NOT execute in replay mode."""
        call_log = []

        @sim_trace
        def add(a, b):
            call_log.append("called")
            return a + b

        # Record
        ctx, sink = make_record_ctx()
        add(2, 3)
        assert call_log == ["called"]
        sink.to_fixture_json(tmp_path, "fix")

        # Replay — body should NOT run again
        call_log.clear()
        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = add(2, 3)

        assert result == 5
        assert call_log == []

    def test_returns_recorded_output(self, tmp_path):
        """Replay returns the exact recorded output."""
        @sim_trace
        def greet(name):
            return f"Hello, {name}!"

        ctx, sink = make_record_ctx()
        greet("Alice")
        sink.to_fixture_json(tmp_path, "fix")

        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = greet("Alice")

        assert result == "Hello, Alice!"

    def test_returns_complex_output(self, tmp_path):
        """Replay returns complex dict/list output correctly."""
        @sim_trace
        def compute(x, y):
            return {"sum": x + y, "product": x * y}

        ctx, sink = make_record_ctx()
        compute(3, 7)
        sink.to_fixture_json(tmp_path, "fix")

        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = compute(3, 7)

        assert result == {"sum": 10, "product": 21}


# ---------------------------------------------------------------------------
# AC4: Replay stub miss — returns None, function NOT executed
# ---------------------------------------------------------------------------

class TestReplayStubMiss:
    def test_no_replay_context_returns_none(self):
        """Returns None when no ReplayContext is active."""
        call_log = []

        @sim_trace
        def add(a, b):
            call_log.append("called")
            return a + b

        make_replay_sim_ctx()
        result = add(2, 3)

        assert result is None
        assert call_log == []  # Function NOT executed

    def test_stub_miss_returns_none(self, tmp_path):
        """Returns None when fixture exists but has no matching stub."""
        @sim_trace
        def add(a, b):
            return a + b

        # Write fixture for different args (3, 4) — not (1, 2)
        ctx, sink = make_record_ctx()
        add(3, 4)
        sink.to_fixture_json(tmp_path, "fix")

        call_log = []

        @sim_trace
        def add_tracked(a, b):
            call_log.append("called")
            return a + b

        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = add_tracked(1, 2)  # Different func — no matching stub

        assert result is None
        assert call_log == []  # Function NOT executed

    def test_function_never_executes_on_miss(self):
        """Even on miss, the decorated function body is never called."""
        side_effects = []

        @sim_trace
        def mutating_func(x):
            side_effects.append(x)  # Side effect that must NOT happen in replay
            return x * 2

        make_replay_sim_ctx()
        result = mutating_func(42)

        assert result is None
        assert side_effects == []  # No side effects triggered

    def test_miss_returns_none_not_raises(self, tmp_path):
        """Stub miss is silent — returns None, no exception raised."""
        @sim_trace
        def add(a, b):
            return a + b

        # Empty fixture — no stubs at all
        (tmp_path / "fix.json").write_text(
            json.dumps({"schema_version": 1, "stubs": []}), encoding="utf-8"
        )
        make_replay_sim_ctx()

        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = add(1, 2)  # Should not raise

        assert result is None


# ---------------------------------------------------------------------------
# AC5: Works on plain sync functions
# ---------------------------------------------------------------------------

class TestSyncFunctions:
    def test_plain_function(self):
        """Works on def add(a, b): return a + b."""
        @sim_trace
        def add(a, b):
            return a + b

        assert add(10, 20) == 30

    def test_function_with_defaults(self):
        """Works with default arguments."""
        @sim_trace
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        make_record_ctx()
        result = greet("World")
        assert result == "Hello, World!"

    def test_function_with_kwargs(self):
        """Works with keyword arguments."""
        @sim_trace
        def build(name, **opts):
            return {"name": name, **opts}

        make_record_ctx()
        result = build("test", color="red", size=5)
        assert result == {"name": "test", "color": "red", "size": 5}


# ---------------------------------------------------------------------------
# AC6: Works on async functions
# ---------------------------------------------------------------------------

class TestAsyncFunctions:
    def test_async_off_mode(self):
        """Async function works normally in off mode."""
        @sim_trace
        async def async_add(a, b):
            return a + b

        result = asyncio.run(async_add(2, 3))
        assert result == 5

    def test_async_record_mode(self):
        """Async function emits FixtureEvent on record."""
        call_log = []

        @sim_trace
        async def async_multiply(a, b):
            call_log.append("called")
            return a * b

        ctx, sink = make_record_ctx()
        result = asyncio.run(async_multiply(4, 5))

        assert result == 20
        assert call_log == ["called"]
        assert len(sink.events) == 1
        assert sink.events[0].output == 20

    def test_async_replay_mode(self, tmp_path):
        """Async function body is skipped in replay; recorded output returned."""
        call_log = []

        @sim_trace
        async def async_add(a, b):
            call_log.append("called")
            return a + b

        # Record
        ctx, sink = make_record_ctx()
        asyncio.run(async_add(10, 20))
        assert call_log == ["called"]
        sink.to_fixture_json(tmp_path, "fix")

        # Replay
        call_log.clear()
        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = asyncio.run(async_add(10, 20))

        assert result == 30
        assert call_log == []


# ---------------------------------------------------------------------------
# AC7: Nested @sim_trace — inner results become stubs of outer
# ---------------------------------------------------------------------------

class TestNestedTraces:
    def test_inner_becomes_stub(self):
        """Inner @sim_trace result appears in outer event's stubs list."""
        @sim_trace
        def inner(x):
            return x * 2

        @sim_trace
        def outer(x):
            return inner(x) + 1

        ctx, sink = make_record_ctx()
        result = outer(5)
        assert result == 11

        # Two events emitted: one for inner, one for outer
        assert len(sink.events) == 2

        # Outer event is the last emitted
        outer_event = next(e for e in sink.events if e.stubs)
        assert outer_event.output == 11
        assert len(outer_event.stubs) == 1
        assert outer_event.stubs[0]["output"] == 10  # inner(5) = 10

    def test_nested_replay(self, tmp_path):
        """Nested @sim_trace replays correctly — outer body skipped entirely."""
        outer_log = []
        inner_log = []

        @sim_trace
        def inner(x):
            inner_log.append(x)
            return x * 2

        @sim_trace
        def outer(x):
            outer_log.append(x)
            return inner(x) + 1

        # Record
        ctx, sink = make_record_ctx()
        outer(5)
        assert outer_log == [5]
        assert inner_log == [5]
        sink.to_fixture_json(tmp_path, "fix")

        # Replay — outer body (and thus inner) should NOT execute
        outer_log.clear()
        inner_log.clear()
        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = outer(5)

        assert result == 11
        assert outer_log == []  # outer body skipped
        assert inner_log == []  # inner never called


# ---------------------------------------------------------------------------
# AC8: Fingerprint stable across runs for same inputs
# ---------------------------------------------------------------------------

class TestFingerprinting:
    def test_stable_fingerprint(self):
        """Same inputs produce same fingerprint."""
        fp1 = _compute_fingerprint("my_func", {"a": 1, "b": 2})
        fp2 = _compute_fingerprint("my_func", {"a": 1, "b": 2})
        assert fp1 == fp2

    def test_different_inputs_different_fingerprint(self):
        """Different inputs produce different fingerprints."""
        fp1 = _compute_fingerprint("my_func", {"a": 1, "b": 2})
        fp2 = _compute_fingerprint("my_func", {"a": 1, "b": 3})
        assert fp1 != fp2

    def test_different_qualname_different_fingerprint(self):
        """Different qualnames produce different fingerprints."""
        fp1 = _compute_fingerprint("func_a", {"x": 1})
        fp2 = _compute_fingerprint("func_b", {"x": 1})
        assert fp1 != fp2

    def test_ordinal_tracking(self):
        """Same function+args called twice gets ordinal 0 then 1 in emitted events."""
        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        add(1, 2)
        add(1, 2)  # Same args again

        assert len(sink.events) == 2
        ordinals = sorted(e.ordinal for e in sink.events)
        assert ordinals == [0, 1]

    def test_different_args_different_ordinal_sequence(self):
        """Different args start their own ordinal sequence independently."""
        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        add(1, 2)  # fp_A ordinal 0
        add(3, 4)  # fp_B ordinal 0
        add(1, 2)  # fp_A ordinal 1

        assert len(sink.events) == 3
        ordinals = [e.ordinal for e in sink.events]
        assert ordinals == [0, 0, 1]


# ---------------------------------------------------------------------------
# AC9: Zero framework imports
# ---------------------------------------------------------------------------

class TestZeroDependencies:
    def test_no_banned_imports(self):
        """trace.py does not import any banned framework module."""
        import sim_sdk.trace as trace_module
        source = Path(trace_module.__file__).read_text()

        banned = [
            "flask", "django", "fastapi", "starlette", "requests",
            "httpx", "aiohttp", "psycopg2", "sqlalchemy", "asyncpg",
        ]
        for module in banned:
            assert f"import {module}" not in source, (
                f"trace.py imports banned module: {module}"
            )
            assert f"from {module}" not in source, (
                f"trace.py imports banned module: {module}"
            )


# ---------------------------------------------------------------------------
# Decorator behavior
# ---------------------------------------------------------------------------

class TestDecoratorBehavior:
    def test_preserves_function_name(self):
        """functools.wraps preserves __name__ and __doc__."""
        @sim_trace
        def my_function(x):
            """My docstring."""
            return x

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_custom_name(self):
        """@sim_trace(name="custom") uses custom qualname in emitted event."""
        @sim_trace(name="pricing.quote")
        def calculate(items):
            return sum(items)

        ctx, sink = make_record_ctx()
        calculate([10, 20, 30])

        assert len(sink.events) == 1
        assert sink.events[0].qualname == "pricing.quote"

    def test_both_decorator_syntaxes(self):
        """Both @sim_trace and @sim_trace() work."""
        @sim_trace
        def bare(x):
            return x

        @sim_trace()
        def parens(x):
            return x

        assert bare(42) == 42
        assert parens(42) == 42

    def test_async_decorator_preserves_coroutine(self):
        """Async decorated function is still a coroutine function."""
        @sim_trace
        async def my_async(x):
            return x

        assert inspect.iscoroutinefunction(my_async)


# ---------------------------------------------------------------------------
# Integration: record → replay round-trip via ReplayContext
# ---------------------------------------------------------------------------

class TestRoundtrip:
    def test_record_then_replay(self, tmp_path):
        """Full record → fixture.json → ReplayContext → replay cycle."""
        @sim_trace
        def compute(x, y):
            return {"sum": x + y, "product": x * y}

        # Record
        ctx, sink = make_record_ctx()
        recorded = compute(3, 7)
        assert recorded == {"sum": 10, "product": 21}
        sink.to_fixture_json(tmp_path, "fix")

        # Replay
        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            replayed = compute(3, 7)

        assert replayed == {"sum": 10, "product": 21}

    def test_different_args_miss_returns_none(self, tmp_path):
        """Replay with different args produces a miss and returns None."""
        @sim_trace
        def add(a, b):
            return a + b

        # Record with (1, 2)
        ctx, sink = make_record_ctx()
        add(1, 2)
        sink.to_fixture_json(tmp_path, "fix")

        # Replay with (3, 4) — different fingerprint, no stub
        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            result = add(3, 4)

        assert result is None

    def test_multiple_ordinals_replay_in_order(self, tmp_path):
        """Multiple calls with same args are replayed in ordinal order."""
        results_recorded = []

        @sim_trace
        def next_value(seed):
            results_recorded.append(len(results_recorded))
            return len(results_recorded)

        ctx, sink = make_record_ctx()
        next_value(0)  # ordinal 0 → 1
        next_value(0)  # ordinal 1 → 2
        sink.to_fixture_json(tmp_path, "fix")

        clear_context()
        make_replay_sim_ctx()
        with ReplayContext(fixture_id="fix", fixture_dir=str(tmp_path)):
            r1 = next_value(0)
            r2 = next_value(0)

        assert r1 == 1
        assert r2 == 2


# ---------------------------------------------------------------------------
# AC10: FixtureEvent includes method, path, service fields
# ---------------------------------------------------------------------------

class TestFixtureEventMetadata:
    def test_method_path_service_in_event(self):
        """FixtureEvent includes method, path, service with defaults."""
        from sim_sdk.fixture.schema import FixtureEvent
        event = FixtureEvent(fixture_id="test", qualname="f", run_id="r", recorded_at="now")
        assert event.method == ""
        assert event.path == ""
        assert event.service == ""

    def test_to_dict_includes_metadata(self):
        """to_dict() serializes method, path, service."""
        from sim_sdk.fixture.schema import FixtureEvent
        event = FixtureEvent(
            fixture_id="test", qualname="f", run_id="r", recorded_at="now",
            method="POST", path="/quote", service="pricing-api",
        )
        d = event.to_dict()
        assert d["method"] == "POST"
        assert d["path"] == "/quote"
        assert d["service"] == "pricing-api"


# ---------------------------------------------------------------------------
# AC11: Sampling gate skips emission
# ---------------------------------------------------------------------------

class TestSamplingGate:
    def test_rate_0_skips_emission(self):
        """SIM_SAMPLE_RATE=0 means no events emitted."""
        import os
        from unittest import mock

        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0"}):
            result = add(2, 3)

        assert result == 5  # Function still executes
        assert len(sink.events) == 0  # But no event emitted

    def test_rate_1_emits_normally(self):
        """SIM_SAMPLE_RATE=1 (default) emits events as usual."""
        import os
        from unittest import mock

        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "1.0"}):
            add(2, 3)

        assert len(sink.events) == 1

    def test_nested_trace_inherits_sampling_decision(self):
        """Inner @sim_trace calls are NOT independently sampled."""
        import os
        from unittest import mock

        @sim_trace
        def inner(x):
            return x * 2

        @sim_trace
        def outer(x):
            return inner(x) + 1

        ctx, sink = make_record_ctx()
        # Rate=0 should skip the outer, so inner is never called in record mode
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0"}):
            result = outer(5)

        assert result == 11
        assert len(sink.events) == 0


# ---------------------------------------------------------------------------
# AC12: Events include method/path/service from context
# ---------------------------------------------------------------------------

class TestEventMetadataFromContext:
    def test_method_path_from_context(self):
        """FixtureEvent picks up http_method/http_path from SimContext."""
        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        ctx.http_method = "POST"
        ctx.http_path = "/quote"

        add(2, 3)

        event = sink.events[0]
        assert event.method == "POST"
        assert event.path == "/quote"

    def test_defaults_when_not_set(self):
        """method/path default to empty when context has no HTTP metadata."""
        @sim_trace
        def add(a, b):
            return a + b

        ctx, sink = make_record_ctx()
        add(2, 3)

        event = sink.events[0]
        assert event.method == ""
        assert event.path == ""
