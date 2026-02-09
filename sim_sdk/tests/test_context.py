"""Tests for sim_sdk.context module."""

import os
import threading

import pytest

from sim_sdk.context import (
    SimContext,
    SimMode,
    clear_context,
    get_context,
    init_context,
    set_context,
)


class TestSimMode:
    """Tests for SimMode enum."""

    def test_mode_values(self):
        assert SimMode.OFF.value == "off"
        assert SimMode.RECORD.value == "record"
        assert SimMode.REPLAY.value == "replay"

    def test_mode_from_string(self):
        assert SimMode("off") == SimMode.OFF
        assert SimMode("record") == SimMode.RECORD
        assert SimMode("replay") == SimMode.REPLAY


class TestSimContext:
    """Tests for SimContext class."""

    def test_default_values(self):
        ctx = SimContext()
        assert ctx.mode == SimMode.OFF
        assert ctx.run_id == ""
        assert ctx.request_id == ""
        assert ctx.stub_dir is None
        assert ctx.ordinal_counters == {}

    def test_is_active(self):
        ctx_off = SimContext(mode=SimMode.OFF)
        ctx_record = SimContext(mode=SimMode.RECORD)
        ctx_replay = SimContext(mode=SimMode.REPLAY)

        assert not ctx_off.is_active
        assert ctx_record.is_active
        assert ctx_replay.is_active

    def test_is_recording(self):
        ctx_off = SimContext(mode=SimMode.OFF)
        ctx_record = SimContext(mode=SimMode.RECORD)
        ctx_replay = SimContext(mode=SimMode.REPLAY)

        assert not ctx_off.is_recording
        assert ctx_record.is_recording
        assert not ctx_replay.is_recording

    def test_is_replaying(self):
        ctx_off = SimContext(mode=SimMode.OFF)
        ctx_record = SimContext(mode=SimMode.RECORD)
        ctx_replay = SimContext(mode=SimMode.REPLAY)

        assert not ctx_off.is_replaying
        assert not ctx_record.is_replaying
        assert ctx_replay.is_replaying

    def test_ordinal_tracking(self):
        ctx = SimContext()

        # First call with fingerprint "fp1"
        assert ctx.next_ordinal("fp1") == 0
        assert ctx.next_ordinal("fp1") == 1
        assert ctx.next_ordinal("fp1") == 2

        # Different fingerprint starts at 0
        assert ctx.next_ordinal("fp2") == 0
        assert ctx.next_ordinal("fp2") == 1

        # Back to fp1 continues
        assert ctx.next_ordinal("fp1") == 3

    def test_reset_ordinals(self):
        ctx = SimContext()

        ctx.next_ordinal("fp1")
        ctx.next_ordinal("fp1")
        assert ctx.ordinal_counters["fp1"] == 2

        ctx.reset_ordinals()
        assert ctx.ordinal_counters == {}
        assert ctx.next_ordinal("fp1") == 0

    def test_new_request_id(self):
        ctx = SimContext()

        # Generate first request ID
        request_id1 = ctx.new_request_id()
        assert len(request_id1) == 8
        assert ctx.request_id == request_id1

        # Track some ordinals
        ctx.next_ordinal("fp1")

        # Generate new request ID - should reset ordinals
        request_id2 = ctx.new_request_id()
        assert request_id2 != request_id1
        assert ctx.ordinal_counters == {}


class TestContextFunctions:
    """Tests for context management functions."""

    def test_get_context_creates_default(self):
        clear_context()
        ctx = get_context()
        assert ctx.mode == SimMode.OFF

    def test_set_and_get_context(self):
        ctx = SimContext(mode=SimMode.RECORD, run_id="test-123")
        set_context(ctx)

        retrieved = get_context()
        assert retrieved.mode == SimMode.RECORD
        assert retrieved.run_id == "test-123"

    def test_clear_context(self):
        ctx = SimContext(mode=SimMode.RECORD)
        set_context(ctx)

        clear_context()

        # After clear, should create new default context
        new_ctx = get_context()
        assert new_ctx.mode == SimMode.OFF

    def test_context_from_env(self):
        os.environ["SIM_MODE"] = "record"
        os.environ["SIM_RUN_ID"] = "env-run-456"
        os.environ["SIM_STUB_DIR"] = "/tmp/stubs"

        clear_context()
        ctx = get_context()

        assert ctx.mode == SimMode.RECORD
        assert ctx.run_id == "env-run-456"
        assert str(ctx.stub_dir) == "/tmp/stubs"

    def test_context_from_env_invalid_mode(self):
        os.environ["SIM_MODE"] = "invalid"

        clear_context()
        ctx = get_context()

        # Should default to OFF for invalid mode
        assert ctx.mode == SimMode.OFF

    def test_init_context_with_values(self, temp_dir):
        ctx = init_context(
            mode=SimMode.REPLAY,
            run_id="init-789",
            stub_dir=temp_dir,
        )

        assert ctx.mode == SimMode.REPLAY
        assert ctx.run_id == "init-789"
        assert ctx.stub_dir == temp_dir

        # Should be retrievable
        retrieved = get_context()
        assert retrieved is ctx


class TestThreadIsolation:
    """Tests for thread-local context isolation."""

    def test_contexts_isolated_between_threads(self):
        results = {}

        def thread_func(thread_id, mode):
            ctx = SimContext(mode=mode, run_id=f"thread-{thread_id}")
            set_context(ctx)

            # Small delay to ensure both threads are running
            import time
            time.sleep(0.01)

            # Verify our context is still set
            retrieved = get_context()
            results[thread_id] = (retrieved.mode, retrieved.run_id)

        t1 = threading.Thread(target=thread_func, args=(1, SimMode.RECORD))
        t2 = threading.Thread(target=thread_func, args=(2, SimMode.REPLAY))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each thread should have its own context
        assert results[1] == (SimMode.RECORD, "thread-1")
        assert results[2] == (SimMode.REPLAY, "thread-2")
