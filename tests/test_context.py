"""
Tests for T2: SimContext + Context Variable Management

Covers all acceptance criteria:
1. SimContext stores run_id, fixture_id, mode, ordinals, collected stubs
2. get_context() returns active context / no-op context outside scope
3. Ordinal counters increment correctly per unique fingerprint
4. Context isolated between concurrent threads
5. init_sim() reads env vars with sensible defaults (mode=off if not set)
6. Zero imports from any framework or driver
7. 100% unit test coverage on context creation, scoping, ordinal tracking
"""

import os
import threading
import pytest
from pathlib import Path
from unittest.mock import patch

from sim_sdk.context import (
    SimContext,
    SimMode,
    get_context,
    set_context,
    clear_context,
    init_sim,
    init_context,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_context():
    """Ensure each test starts with a clean context."""
    clear_context()
    yield
    clear_context()


# ===========================================================================
# AC1: SimContext stores required fields
# ===========================================================================

class TestSimContextFields:
    """SimContext stores run_id, fixture_id, mode, ordinals, collected stubs."""

    def test_default_mode_is_off(self):
        ctx = SimContext()
        assert ctx.mode == SimMode.OFF

    def test_stores_run_id(self):
        ctx = SimContext(run_id="abc123")
        assert ctx.run_id == "abc123"

    def test_stores_fixture_id(self):
        ctx = SimContext(fixture_id="fix-001")
        assert ctx.fixture_id == "fix-001"

    def test_stores_mode_record(self):
        ctx = SimContext(mode=SimMode.RECORD)
        assert ctx.mode == SimMode.RECORD

    def test_stores_mode_replay(self):
        ctx = SimContext(mode=SimMode.REPLAY)
        assert ctx.mode == SimMode.REPLAY

    def test_ordinal_counters_empty_by_default(self):
        ctx = SimContext()
        assert ctx.ordinal_counters == {}

    def test_collected_stubs_empty_by_default(self):
        ctx = SimContext()
        assert ctx.collected_stubs == []

    def test_stores_stub_dir(self):
        ctx = SimContext(stub_dir=Path("/tmp/stubs"))
        assert ctx.stub_dir == Path("/tmp/stubs")

    def test_stores_sink(self):
        sink = object()
        ctx = SimContext(sink=sink)
        assert ctx.sink is sink

    def test_trace_depth_zero_by_default(self):
        ctx = SimContext()
        assert ctx.trace_depth == 0

    def test_request_id_empty_by_default(self):
        ctx = SimContext()
        assert ctx.request_id == ""

    def test_stub_dir_none_by_default(self):
        ctx = SimContext()
        assert ctx.stub_dir is None

    def test_sink_none_by_default(self):
        ctx = SimContext()
        assert ctx.sink is None

    def test_two_contexts_have_independent_stubs(self):
        ctx1 = SimContext()
        ctx2 = SimContext()
        ctx1.collected_stubs.append({"a": 1})
        assert len(ctx2.collected_stubs) == 0

    def test_two_contexts_have_independent_ordinals(self):
        ctx1 = SimContext()
        ctx2 = SimContext()
        ctx1.next_ordinal("fp")
        assert ctx2.next_ordinal("fp") == 0


# ===========================================================================
# AC1 (cont): Mode properties
# ===========================================================================

class TestSimModeProperties:
    """is_active, is_recording, is_replaying properties."""

    def test_off_mode_not_active(self):
        ctx = SimContext(mode=SimMode.OFF)
        assert ctx.is_active is False
        assert ctx.is_recording is False
        assert ctx.is_replaying is False

    def test_record_mode(self):
        ctx = SimContext(mode=SimMode.RECORD)
        assert ctx.is_active is True
        assert ctx.is_recording is True
        assert ctx.is_replaying is False

    def test_replay_mode(self):
        ctx = SimContext(mode=SimMode.REPLAY)
        assert ctx.is_active is True
        assert ctx.is_recording is False
        assert ctx.is_replaying is True


class TestSimMode:
    """SimMode enum values."""

    def test_off_value(self):
        assert SimMode.OFF.value == "off"

    def test_record_value(self):
        assert SimMode.RECORD.value == "record"

    def test_replay_value(self):
        assert SimMode.REPLAY.value == "replay"

    def test_construct_from_string(self):
        assert SimMode("off") == SimMode.OFF
        assert SimMode("record") == SimMode.RECORD
        assert SimMode("replay") == SimMode.REPLAY

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            SimMode("invalid")


# ===========================================================================
# AC2: get_context() behavior
# ===========================================================================

class TestGetContext:
    """get_context() returns active context / no-op context outside scope."""

    def test_returns_off_mode_by_default(self):
        ctx = get_context()
        assert ctx.mode == SimMode.OFF
        assert ctx.is_active is False

    def test_returns_set_context(self):
        ctx = SimContext(mode=SimMode.RECORD, run_id="r1")
        set_context(ctx)
        assert get_context() is ctx

    def test_clear_context_resets(self):
        ctx = SimContext(mode=SimMode.RECORD)
        set_context(ctx)
        clear_context()
        new_ctx = get_context()
        assert new_ctx is not ctx
        assert new_ctx.mode == SimMode.OFF

    def test_auto_creates_from_env(self):
        with patch.dict(os.environ, {"SIM_MODE": "record", "SIM_RUN_ID": "env-run"}):
            clear_context()
            ctx = get_context()
            assert ctx.mode == SimMode.RECORD
            assert ctx.run_id == "env-run"

    def test_caches_across_calls(self):
        ctx1 = get_context()
        ctx2 = get_context()
        assert ctx1 is ctx2

    def test_reads_stub_dir_from_env(self, tmp_path):
        with patch.dict(os.environ, {"SIM_STUB_DIR": str(tmp_path)}):
            clear_context()
            ctx = get_context()
            assert ctx.stub_dir == tmp_path

    def test_no_stub_dir_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            clear_context()
            ctx = get_context()
            assert ctx.stub_dir is None

    def test_invalid_mode_falls_back_to_off(self):
        with patch.dict(os.environ, {"SIM_MODE": "garbage"}):
            clear_context()
            ctx = get_context()
            assert ctx.mode == SimMode.OFF


# ===========================================================================
# AC3: Ordinal counters
# ===========================================================================

class TestOrdinalCounters:
    """Ordinal counters increment correctly per unique fingerprint."""

    def test_first_ordinal_is_zero(self):
        ctx = SimContext()
        assert ctx.next_ordinal("fp1") == 0

    def test_increments_on_same_fingerprint(self):
        ctx = SimContext()
        assert ctx.next_ordinal("fp1") == 0
        assert ctx.next_ordinal("fp1") == 1
        assert ctx.next_ordinal("fp1") == 2

    def test_independent_per_fingerprint(self):
        ctx = SimContext()
        assert ctx.next_ordinal("fp_a") == 0
        assert ctx.next_ordinal("fp_b") == 0
        assert ctx.next_ordinal("fp_a") == 1
        assert ctx.next_ordinal("fp_b") == 1

    def test_many_fingerprints(self):
        ctx = SimContext()
        for i in range(100):
            assert ctx.next_ordinal(f"fp_{i}") == 0
        for i in range(100):
            assert ctx.next_ordinal(f"fp_{i}") == 1

    def test_reset_ordinals_clears(self):
        ctx = SimContext()
        ctx.next_ordinal("fp1")
        ctx.next_ordinal("fp1")
        ctx.reset_ordinals()
        assert ctx.next_ordinal("fp1") == 0

    def test_reset_clears_all_state(self):
        ctx = SimContext()
        ctx.next_ordinal("fp1")
        ctx.collected_stubs.append({"test": True})
        ctx.trace_depth = 3
        ctx.reset()
        assert ctx.ordinal_counters == {}
        assert ctx.collected_stubs == []
        assert ctx.trace_depth == 0

    def test_start_new_request_resets_ordinals(self):
        ctx = SimContext()
        ctx.next_ordinal("fp1")
        ctx.collected_stubs.append({"test": True})
        req_id = ctx.start_new_request()
        assert len(req_id) == 8
        assert ctx.ordinal_counters == {}
        assert ctx.collected_stubs == []
        assert ctx.request_id == req_id

    def test_start_new_request_generates_unique_ids(self):
        ctx = SimContext()
        ids = {ctx.start_new_request() for _ in range(50)}
        assert len(ids) == 50  # all unique


# ===========================================================================
# AC4: Thread isolation
# ===========================================================================

class TestThreadIsolation:
    """Context is isolated between concurrent threads."""

    def test_threads_get_independent_contexts(self):
        results = {}
        barrier = threading.Barrier(2)

        def thread_fn(name, mode):
            clear_context()
            ctx = SimContext(mode=mode, run_id=name)
            set_context(ctx)
            barrier.wait()  # both threads alive with their context set
            retrieved = get_context()
            results[name] = {
                "mode": retrieved.mode,
                "run_id": retrieved.run_id,
            }

        t1 = threading.Thread(target=thread_fn, args=("t1", SimMode.RECORD))
        t2 = threading.Thread(target=thread_fn, args=("t2", SimMode.REPLAY))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"]["mode"] == SimMode.RECORD
        assert results["t1"]["run_id"] == "t1"
        assert results["t2"]["mode"] == SimMode.REPLAY
        assert results["t2"]["run_id"] == "t2"

    def test_ordinals_not_shared_across_threads(self):
        results = {}
        barrier = threading.Barrier(2)

        def thread_fn(name):
            clear_context()
            ctx = SimContext(mode=SimMode.RECORD, run_id=name)
            set_context(ctx)
            ctx.next_ordinal("shared_fp")
            ctx.next_ordinal("shared_fp")
            barrier.wait()
            results[name] = ctx.next_ordinal("shared_fp")

        t1 = threading.Thread(target=thread_fn, args=("t1",))
        t2 = threading.Thread(target=thread_fn, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each thread's third call should be ordinal=2, not leaked
        assert results["t1"] == 2
        assert results["t2"] == 2

    def test_clear_in_one_thread_no_effect_on_other(self):
        results = {}
        barrier = threading.Barrier(2)

        def thread_a():
            ctx = SimContext(mode=SimMode.RECORD, run_id="a")
            set_context(ctx)
            barrier.wait()
            # thread_b clears its own context here
            barrier.wait()
            results["a_run_id"] = get_context().run_id

        def thread_b():
            ctx = SimContext(mode=SimMode.REPLAY, run_id="b")
            set_context(ctx)
            barrier.wait()
            clear_context()
            barrier.wait()
            results["b_mode"] = get_context().mode

        t1 = threading.Thread(target=thread_a)
        t2 = threading.Thread(target=thread_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["a_run_id"] == "a"  # thread A unaffected
        assert results["b_mode"] == SimMode.OFF  # thread B got fresh OFF

    def test_main_thread_unaffected_by_child(self):
        ctx = SimContext(mode=SimMode.RECORD, run_id="main")
        set_context(ctx)

        def child():
            set_context(SimContext(mode=SimMode.REPLAY, run_id="child"))

        t = threading.Thread(target=child)
        t.start()
        t.join()

        # Main thread context untouched
        assert get_context().run_id == "main"
        assert get_context().mode == SimMode.RECORD


# ===========================================================================
# AC5: init_sim() reads env vars
# ===========================================================================

class TestInitSim:
    """init_sim() reads from env vars with sensible defaults."""

    def test_defaults_to_off(self):
        ctx = init_sim()
        assert ctx.mode == SimMode.OFF

    def test_reads_sim_mode_env(self):
        with patch.dict(os.environ, {"SIM_MODE": "record"}):
            ctx = init_sim()
            assert ctx.mode == SimMode.RECORD

    def test_reads_sim_mode_replay(self):
        with patch.dict(os.environ, {"SIM_MODE": "replay"}):
            ctx = init_sim()
            assert ctx.mode == SimMode.REPLAY

    def test_reads_sim_run_id_env(self):
        with patch.dict(os.environ, {"SIM_RUN_ID": "env-123"}):
            ctx = init_sim()
            assert ctx.run_id == "env-123"

    def test_reads_sim_stub_dir_env(self):
        with patch.dict(os.environ, {"SIM_STUB_DIR": "/tmp/stubs"}):
            ctx = init_sim()
            assert ctx.stub_dir == Path("/tmp/stubs")

    def test_explicit_mode_overrides_env(self):
        with patch.dict(os.environ, {"SIM_MODE": "record"}):
            ctx = init_sim(mode=SimMode.REPLAY)
            assert ctx.mode == SimMode.REPLAY

    def test_explicit_run_id_overrides_env(self):
        with patch.dict(os.environ, {"SIM_RUN_ID": "env-id"}):
            ctx = init_sim(run_id="explicit-id")
            assert ctx.run_id == "explicit-id"

    def test_explicit_stub_dir_overrides_env(self):
        with patch.dict(os.environ, {"SIM_STUB_DIR": "/env/path"}):
            ctx = init_sim(stub_dir=Path("/explicit/path"))
            assert ctx.stub_dir == Path("/explicit/path")

    def test_sets_sink(self):
        sink = object()
        ctx = init_sim(sink=sink)
        assert ctx.sink is sink

    def test_sets_context_globally(self):
        ctx = init_sim(mode=SimMode.RECORD, run_id="test")
        assert get_context() is ctx

    def test_invalid_mode_defaults_to_off(self):
        with patch.dict(os.environ, {"SIM_MODE": "invalid_mode"}):
            ctx = init_sim()
            assert ctx.mode == SimMode.OFF

    def test_case_insensitive_mode(self):
        with patch.dict(os.environ, {"SIM_MODE": "RECORD"}):
            ctx = init_sim()
            assert ctx.mode == SimMode.RECORD

    def test_init_context_is_alias(self):
        """init_context is a backward-compatible alias for init_sim."""
        assert init_context is init_sim


# ===========================================================================
# AC6: Zero framework imports
# ===========================================================================

class TestZeroDependencies:
    """context.py has zero imports from any framework or driver."""

    def test_no_banned_imports(self):
        import sim_sdk.context as ctx_module
        source = Path(ctx_module.__file__).read_text()

        banned = [
            "flask", "django", "fastapi", "starlette",
            "requests", "httpx", "aiohttp", "urllib3",
            "sqlalchemy", "psycopg", "pymysql", "sqlite3",
            "boto3", "grpc", "celery", "redis",
        ]
        for lib in banned:
            assert f"import {lib}" not in source, f"context.py imports banned: {lib}"
            assert f"from {lib}" not in source, f"context.py imports banned: {lib}"

    def test_uses_contextvars_not_threading(self):
        """Verify context.py uses contextvars, not threading.local."""
        import sim_sdk.context as ctx_module
        source = Path(ctx_module.__file__).read_text()

        assert "from contextvars import" in source
        assert "threading.local" not in source


# ===========================================================================
# AC7: ContextVar class-level API
# ===========================================================================

class TestContextVarAPI:
    """SimContext.get_current / set_current / reset_current (Jerry's tests + extras)."""

    def test_get_current_none_when_not_set(self):
        """get_current() returns None when no context is set."""
        assert SimContext.get_current() is None

    def test_set_and_get_current(self):
        """set_current() makes get_current() return the context."""
        ctx = SimContext(run_id="cv-test")
        token = SimContext.set_current(ctx)
        try:
            assert SimContext.get_current() is ctx
        finally:
            SimContext.reset_current(token)

    def test_reset_restores_previous(self):
        """reset_current(token) restores the prior value."""
        ctx1 = SimContext(run_id="first")
        ctx2 = SimContext(run_id="second")

        token1 = SimContext.set_current(ctx1)
        try:
            assert SimContext.get_current() is ctx1

            token2 = SimContext.set_current(ctx2)
            try:
                assert SimContext.get_current() is ctx2
            finally:
                SimContext.reset_current(token2)

            assert SimContext.get_current() is ctx1
        finally:
            SimContext.reset_current(token1)

    def test_get_current_vs_get_context(self):
        """get_current() returns None; get_context() auto-creates."""
        assert SimContext.get_current() is None
        ctx = get_context()
        assert ctx is not None
        assert ctx.mode == SimMode.OFF
        # Now get_current also returns the auto-created context
        assert SimContext.get_current() is ctx

    def test_set_current_compatible_with_set_context(self):
        """set_current and set_context share the same ContextVar."""
        ctx = SimContext(run_id="via-static")
        token = SimContext.set_current(ctx)
        try:
            assert get_context() is ctx
        finally:
            SimContext.reset_current(token)

    def test_set_context_visible_to_get_current(self):
        """set_context() is visible to get_current()."""
        ctx = SimContext(run_id="via-module")
        set_context(ctx)
        assert SimContext.get_current() is ctx


# ===========================================================================
# Scoping: collected stubs
# ===========================================================================

class TestCollectedStubs:
    """collected_stubs list manipulation."""

    def test_append_and_read(self):
        ctx = SimContext()
        ctx.collected_stubs.append({"type": "test", "value": 42})
        assert len(ctx.collected_stubs) == 1
        assert ctx.collected_stubs[0]["value"] == 42

    def test_clear_stubs_via_reset(self):
        ctx = SimContext()
        ctx.collected_stubs.append({"x": 1})
        ctx.reset()
        assert ctx.collected_stubs == []
