"""
Tests for @sim_trace decorator using plain Python functions.

Covers all T3 acceptance criteria:
  1. Off mode: function behaves identically
  2. Record mode: function executes, fixture event emitted
  3. Replay mode: function body NOT executed, recorded output returned
  4. Replay mode: SimStubMissError raised with diagnostics
  5. Works on plain sync functions
  6. Works on async functions
  7. Nested @sim_trace: inner results become stubs of outer
  8. Fingerprint stable across runs for same inputs
  9. Zero framework imports in trace.py
"""

import asyncio
import inspect
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sim_sdk.context import SimContext, SimMode, get_context, set_context, clear_context
from sim_sdk.trace import (
    sim_trace,
    SimStubMissError,
    FixtureEvent,
    _compute_fingerprint,
    _fixture_key,
    _make_serializable,
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


@pytest.fixture
def stub_dir():
    """Provide a temporary directory for fixture files."""
    dirpath = tempfile.mkdtemp()
    yield Path(dirpath)
    shutil.rmtree(dirpath)


def make_record_ctx(stub_dir: Path) -> SimContext:
    """Create a record-mode context pointing at stub_dir."""
    ctx = SimContext(mode=SimMode.RECORD, run_id="test-run", stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_replay_ctx(stub_dir: Path) -> SimContext:
    """Create a replay-mode context pointing at stub_dir."""
    ctx = SimContext(mode=SimMode.REPLAY, run_id="test-run", stub_dir=stub_dir)
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

        # Default context is OFF
        result = add(2, 3)
        assert result == 5

    def test_no_side_effects(self, stub_dir):
        """No fixture files created when mode is off."""
        ctx = SimContext(mode=SimMode.OFF, run_id="test", stub_dir=stub_dir)
        set_context(ctx)

        @sim_trace
        def add(a, b):
            return a + b

        add(2, 3)

        # stub_dir should remain empty
        files = list(stub_dir.rglob("*.json"))
        assert files == []

    def test_exception_passthrough(self):
        """Exceptions propagate normally in off mode."""
        @sim_trace
        def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            failing()


# ---------------------------------------------------------------------------
# AC2: Record mode — function executes, fixture emitted
# ---------------------------------------------------------------------------

class TestRecordMode:
    def test_function_executes(self, stub_dir):
        """Function body actually runs in record mode."""
        call_log = []

        @sim_trace
        def add(a, b):
            call_log.append((a, b))
            return a + b

        make_record_ctx(stub_dir)
        result = add(2, 3)

        assert result == 5
        assert call_log == [(2, 3)]

    def test_fixture_file_written(self, stub_dir):
        """A fixture JSON file is written to stub_dir."""
        @sim_trace
        def add(a, b):
            return a + b

        make_record_ctx(stub_dir)
        add(2, 3)

        files = list(stub_dir.rglob("*.json"))
        assert len(files) == 1

    def test_fixture_contents(self, stub_dir):
        """Fixture file contains correct input, output, and metadata."""
        @sim_trace
        def multiply(a, b):
            return a * b

        make_record_ctx(stub_dir)
        multiply(4, 5)

        files = list(stub_dir.rglob("*.json"))
        assert len(files) == 1

        with open(files[0]) as f:
            data = json.load(f)

        assert data["input"]["a"] == 4
        assert data["input"]["b"] == 5
        assert data["output"] == 20
        assert data["run_id"] == "test-run"
        assert data["error"] is None
        assert "qualname" in data
        assert "input_fingerprint" in data
        assert "output_fingerprint" in data
        assert data["duration_ms"] >= 0

    def test_exception_recorded(self, stub_dir):
        """Exception is recorded in the fixture's error field."""
        @sim_trace
        def failing(x):
            raise RuntimeError(f"bad input: {x}")

        make_record_ctx(stub_dir)

        with pytest.raises(RuntimeError, match="bad input: 42"):
            failing(42)

        files = list(stub_dir.rglob("*.json"))
        assert len(files) == 1

        with open(files[0]) as f:
            data = json.load(f)

        assert data["error"] is not None
        assert "RuntimeError" in data["error"]
        assert "bad input: 42" in data["error"]

    def test_no_sink_no_stub_dir(self):
        """When neither sink nor stub_dir is set, fixture is silently discarded."""
        ctx = SimContext(mode=SimMode.RECORD, run_id="test")
        set_context(ctx)

        @sim_trace
        def add(a, b):
            return a + b

        # Should not raise
        result = add(2, 3)
        assert result == 5


# ---------------------------------------------------------------------------
# AC3: Replay mode — body NOT executed, recorded output returned
# ---------------------------------------------------------------------------

class TestReplayMode:
    def test_skips_body(self, stub_dir):
        """Function body does NOT execute in replay mode."""
        call_log = []

        @sim_trace
        def add(a, b):
            call_log.append("called")
            return a + b

        # Record first
        make_record_ctx(stub_dir)
        add(2, 3)
        assert call_log == ["called"]

        # Replay — body should NOT run again
        call_log.clear()
        clear_context()
        make_replay_ctx(stub_dir)
        result = add(2, 3)

        assert result == 5
        assert call_log == []  # Body was skipped

    def test_returns_recorded_output(self, stub_dir):
        """Replay returns the exact recorded output."""
        @sim_trace
        def greet(name):
            return f"Hello, {name}!"

        # Record
        make_record_ctx(stub_dir)
        greet("Alice")

        # Replay
        clear_context()
        make_replay_ctx(stub_dir)
        result = greet("Alice")

        assert result == "Hello, Alice!"


# ---------------------------------------------------------------------------
# AC4: Replay — SimStubMissError with diagnostics
# ---------------------------------------------------------------------------

class TestReplayStubMiss:
    def test_raises_when_no_fixture(self, stub_dir):
        """SimStubMissError raised when no recorded fixture exists."""
        @sim_trace
        def add(a, b):
            return a + b

        make_replay_ctx(stub_dir)

        with pytest.raises(SimStubMissError):
            add(2, 3)

    def test_diagnostics_in_error(self, stub_dir):
        """Error message contains qualname, fingerprint, and expected path."""
        @sim_trace
        def add(a, b):
            return a + b

        make_replay_ctx(stub_dir)

        with pytest.raises(SimStubMissError) as exc_info:
            add(99, 1)

        err = exc_info.value
        assert "add" in err.qualname
        assert err.ordinal == 0
        assert len(err.input_fingerprint) == 64  # SHA-256 hex
        assert err.stub_dir == stub_dir
        assert "Expected at:" in str(err)

    def test_raises_when_no_stub_dir(self):
        """SimStubMissError raised when stub_dir is None."""
        ctx = SimContext(mode=SimMode.REPLAY, run_id="test")
        set_context(ctx)

        @sim_trace
        def add(a, b):
            return a + b

        with pytest.raises(SimStubMissError):
            add(1, 2)


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

    def test_function_with_defaults(self, stub_dir):
        """Works with default arguments."""
        @sim_trace
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        make_record_ctx(stub_dir)
        result = greet("World")
        assert result == "Hello, World!"

    def test_function_with_kwargs(self, stub_dir):
        """Works with keyword arguments."""
        @sim_trace
        def build(name, **opts):
            return {"name": name, **opts}

        make_record_ctx(stub_dir)
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

    def test_async_record_mode(self, stub_dir):
        """Async function recorded correctly."""
        call_log = []

        @sim_trace
        async def async_multiply(a, b):
            call_log.append("called")
            return a * b

        make_record_ctx(stub_dir)
        result = asyncio.run(async_multiply(4, 5))

        assert result == 20
        assert call_log == ["called"]

        files = list(stub_dir.rglob("*.json"))
        assert len(files) == 1

    def test_async_replay_mode(self, stub_dir):
        """Async function replayed — body skipped."""
        call_log = []

        @sim_trace
        async def async_add(a, b):
            call_log.append("called")
            return a + b

        # Record
        make_record_ctx(stub_dir)
        asyncio.run(async_add(10, 20))
        assert call_log == ["called"]

        # Replay
        call_log.clear()
        clear_context()
        make_replay_ctx(stub_dir)
        result = asyncio.run(async_add(10, 20))

        assert result == 30
        assert call_log == []  # Body skipped


# ---------------------------------------------------------------------------
# AC7: Nested @sim_trace — inner results become stubs of outer
# ---------------------------------------------------------------------------

class TestNestedTraces:
    def test_inner_becomes_stub(self, stub_dir):
        """Inner @sim_trace result appears in outer fixture's stubs list."""
        @sim_trace
        def inner(x):
            return x * 2

        @sim_trace
        def outer(x):
            return inner(x) + 1

        make_record_ctx(stub_dir)
        result = outer(5)
        assert result == 11

        # Find the outer fixture (it should have stubs)
        files = list(stub_dir.rglob("*.json"))
        # Should have 2 fixtures: one for inner, one for outer
        assert len(files) == 2

        # Find the outer one (the one with stubs)
        outer_fixture = None
        for fpath in files:
            with open(fpath) as f:
                data = json.load(f)
            if data.get("stubs"):
                outer_fixture = data
                break

        assert outer_fixture is not None, "Outer fixture should have stubs"
        assert len(outer_fixture["stubs"]) == 1
        assert outer_fixture["stubs"][0]["output"] == 10  # inner(5) = 10
        assert outer_fixture["output"] == 11  # inner(5) + 1 = 11


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

    def test_ordinal_tracking(self, stub_dir):
        """Same function+args called twice gets ordinal 0 then 1."""
        @sim_trace
        def add(a, b):
            return a + b

        make_record_ctx(stub_dir)
        add(1, 2)
        add(1, 2)  # Same args again

        files = sorted(stub_dir.rglob("*.json"))
        assert len(files) == 2

        with open(files[0]) as f:
            d0 = json.load(f)
        with open(files[1]) as f:
            d1 = json.load(f)

        ordinals = sorted([d0["ordinal"], d1["ordinal"]])
        assert ordinals == [0, 1]


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
            assert f"import {module}" not in source, f"trace.py imports banned module: {module}"
            assert f"from {module}" not in source, f"trace.py imports banned module: {module}"


# ---------------------------------------------------------------------------
# General: decorator behavior
# ---------------------------------------------------------------------------

class TestDecoratorBehavior:
    def test_preserves_function_name(self):
        """functools.wraps preserves __name__."""
        @sim_trace
        def my_function(x):
            """My docstring."""
            return x

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_custom_name(self, stub_dir):
        """@sim_trace(name="custom") uses custom qualname."""
        @sim_trace(name="pricing.quote")
        def calculate(items):
            return sum(items)

        make_record_ctx(stub_dir)
        calculate([10, 20, 30])

        files = list(stub_dir.rglob("*.json"))
        assert len(files) == 1

        with open(files[0]) as f:
            data = json.load(f)

        assert data["qualname"] == "pricing.quote"

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
# Integration: record → replay roundtrip
# ---------------------------------------------------------------------------

class TestRoundtrip:
    def test_record_then_replay(self, stub_dir):
        """Full record → replay cycle: record output, replay returns it."""
        @sim_trace
        def compute(x, y):
            return {"sum": x + y, "product": x * y}

        # Record phase
        make_record_ctx(stub_dir)
        recorded = compute(3, 7)
        assert recorded == {"sum": 10, "product": 21}

        # Replay phase
        clear_context()
        make_replay_ctx(stub_dir)
        replayed = compute(3, 7)
        assert replayed == {"sum": 10, "product": 21}

    def test_record_then_replay_different_args_miss(self, stub_dir):
        """Replay with different args raises SimStubMissError."""
        @sim_trace
        def add(a, b):
            return a + b

        # Record with (1, 2)
        make_record_ctx(stub_dir)
        add(1, 2)

        # Replay with (3, 4) — different args, no fixture
        clear_context()
        make_replay_ctx(stub_dir)

        with pytest.raises(SimStubMissError):
            add(3, 4)
