"""
Tests for T4: sim_capture() Context Manager

Covers all acceptance criteria:
1. Record mode — set_result() persists fixture, cap.result returns value
2. Replay mode — cap.replaying is True, cap.result returns recorded value
3. Replay stub miss — SimStubMissError raised with diagnostics
4. Warning on missing set_result() — logger.warning if not called in record
5. Ordinal uniqueness — multiple captures with same label get distinct ordinals
6. Sync context manager — `with sim_capture(label) as cap:`
7. Async context manager — `async with sim_capture(label) as cap:`
8. Zero framework dependencies — no web/HTTP/DB imports
9. Off mode — handle is inert, block runs normally
10. Round-trip — record then replay returns identical value
"""

import asyncio
import inspect
import json
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from sim_sdk.context import SimContext, SimMode, set_context, clear_context
from sim_sdk.capture import sim_capture, CaptureHandle, _capture_key
from sim_sdk.trace import SimStubMissError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_context():
    """Ensure each test starts with a clean context."""
    clear_context()
    yield
    clear_context()


@pytest.fixture
def stub_dir(tmp_path):
    """Provide a temporary stub directory."""
    return tmp_path / "stubs"


def make_record_ctx(stub_dir: Path, run_id: str = "test-run") -> SimContext:
    """Create a record-mode context."""
    stub_dir.mkdir(parents=True, exist_ok=True)
    ctx = SimContext(mode=SimMode.RECORD, run_id=run_id, stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_replay_ctx(stub_dir: Path, run_id: str = "test-run") -> SimContext:
    """Create a replay-mode context."""
    ctx = SimContext(mode=SimMode.REPLAY, run_id=run_id, stub_dir=stub_dir)
    set_context(ctx)
    return ctx


def make_off_ctx() -> SimContext:
    """Create an off-mode context."""
    ctx = SimContext(mode=SimMode.OFF)
    set_context(ctx)
    return ctx


# ===========================================================================
# 1. Record Mode
# ===========================================================================

class TestRecordMode:
    """Record mode: set_result() persists fixture, cap.result returns value."""

    def test_set_result_stores_value(self, stub_dir):
        """cap.result returns the value passed to set_result()."""
        make_record_ctx(stub_dir)

        with sim_capture("tax_rate") as cap:
            rate = 0.0825
            cap.set_result(rate)

        assert cap.result == 0.0825

    def test_fixture_written_to_disk(self, stub_dir):
        """Record mode writes a JSON fixture under __capture__/."""
        make_record_ctx(stub_dir)

        with sim_capture("tax_rate") as cap:
            cap.set_result({"rate": 0.0825})

        # Check file exists at expected path
        expected_path = stub_dir / "__capture__" / "tax_rate_0.json"
        assert expected_path.exists(), f"Expected fixture at {expected_path}"

        data = json.loads(expected_path.read_text())
        assert data["type"] == "capture"
        assert data["label"] == "tax_rate"
        assert data["ordinal"] == 0
        assert data["result"] == {"rate": 0.0825}

    def test_stubs_collected_in_context(self, stub_dir):
        """Record mode pushes capture to ctx.collected_stubs."""
        ctx = make_record_ctx(stub_dir)

        with sim_capture("lookup") as cap:
            cap.set_result(42)

        assert len(ctx.collected_stubs) == 1
        stub = ctx.collected_stubs[0]
        assert stub["type"] == "capture"
        assert stub["label"] == "lookup"
        assert stub["result"] == 42

    def test_complex_result_serialized(self, stub_dir):
        """Complex nested structures are serialized correctly."""
        make_record_ctx(stub_dir)

        value = {"items": [1, 2, 3], "nested": {"key": "value"}, "count": 42}
        with sim_capture("complex") as cap:
            cap.set_result(value)

        expected_path = stub_dir / "__capture__" / "complex_0.json"
        data = json.loads(expected_path.read_text())
        assert data["result"] == value

    def test_none_result_recorded(self, stub_dir):
        """None is a valid result that gets recorded."""
        make_record_ctx(stub_dir)

        with sim_capture("nullable") as cap:
            cap.set_result(None)

        expected_path = stub_dir / "__capture__" / "nullable_0.json"
        data = json.loads(expected_path.read_text())
        assert data["result"] is None


# ===========================================================================
# 2. Replay Mode
# ===========================================================================

class TestReplayMode:
    """Replay mode: cap.replaying is True, cap.result returns recorded value."""

    def test_replaying_flag_is_true(self, stub_dir):
        """In replay mode, cap.replaying is True."""
        # First record
        make_record_ctx(stub_dir)
        with sim_capture("rate") as cap:
            cap.set_result(0.0825)

        # Now replay
        make_replay_ctx(stub_dir)
        with sim_capture("rate") as cap:
            assert cap.replaying is True

    def test_result_returns_recorded_value(self, stub_dir):
        """Replay returns the exact value that was recorded."""
        # Record
        make_record_ctx(stub_dir)
        with sim_capture("rate") as cap:
            cap.set_result(0.0825)

        # Replay
        make_replay_ctx(stub_dir)
        with sim_capture("rate") as cap:
            assert cap.result == 0.0825

    def test_replay_skips_block_body(self, stub_dir):
        """Developer can check cap.replaying to skip the block body."""
        # Record
        make_record_ctx(stub_dir)
        with sim_capture("svc") as cap:
            cap.set_result({"status": "ok"})

        # Replay — the block body should be skippable
        make_replay_ctx(stub_dir)
        executed = False
        with sim_capture("svc") as cap:
            if not cap.replaying:
                executed = True  # This should NOT execute
            result = cap.result

        assert not executed
        assert result == {"status": "ok"}

    def test_replay_pushes_stub(self, stub_dir):
        """Replay mode pushes recorded value to ctx.collected_stubs."""
        # Record
        make_record_ctx(stub_dir)
        with sim_capture("svc") as cap:
            cap.set_result(99)

        # Replay
        ctx = make_replay_ctx(stub_dir)
        with sim_capture("svc") as cap:
            pass

        assert len(ctx.collected_stubs) == 1
        stub = ctx.collected_stubs[0]
        assert stub["source"] == "replay"
        assert stub["result"] == 99


# ===========================================================================
# 3. Replay Stub Miss
# ===========================================================================

class TestReplayStubMiss:
    """SimStubMissError raised when fixture not found during replay."""

    def test_missing_fixture_raises(self, stub_dir):
        """Replay with no recorded fixture raises SimStubMissError."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)

        with pytest.raises(SimStubMissError):
            with sim_capture("nonexistent") as cap:
                pass

    def test_error_has_diagnostics(self, stub_dir):
        """SimStubMissError contains useful diagnostic info."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        make_replay_ctx(stub_dir)

        with pytest.raises(SimStubMissError) as exc_info:
            with sim_capture("missing_svc") as cap:
                pass

        err = exc_info.value
        assert "capture:missing_svc" in err.qualname

    def test_missing_stub_dir_raises(self):
        """Replay with no stub_dir at all raises SimStubMissError."""
        ctx = SimContext(mode=SimMode.REPLAY, run_id="test", stub_dir=None)
        set_context(ctx)

        with pytest.raises(SimStubMissError):
            with sim_capture("any_label") as cap:
                pass


# ===========================================================================
# 4. Warning on Missing set_result()
# ===========================================================================

class TestSetResultWarning:
    """Logger warning if set_result() not called in record mode."""

    def test_warns_when_set_result_not_called(self, stub_dir, caplog):
        """Warning logged when record mode exits without set_result()."""
        make_record_ctx(stub_dir)

        with caplog.at_level(logging.WARNING, logger="sim_sdk.capture"):
            with sim_capture("forgot") as cap:
                pass  # Developer forgot to call set_result()

        assert any("set_result()" in record.message for record in caplog.records)

    def test_no_warning_when_set_result_called(self, stub_dir, caplog):
        """No warning when set_result() is properly called."""
        make_record_ctx(stub_dir)

        with caplog.at_level(logging.WARNING, logger="sim_sdk.capture"):
            with sim_capture("remembered") as cap:
                cap.set_result("ok")

        warning_messages = [r.message for r in caplog.records if "set_result" in r.message]
        assert len(warning_messages) == 0


# ===========================================================================
# 5. Ordinal Uniqueness
# ===========================================================================

class TestOrdinalUniqueness:
    """Multiple captures with same label get distinct ordinals."""

    def test_same_label_gets_incrementing_ordinals(self, stub_dir):
        """Two captures with same label write to ordinal 0 and 1."""
        make_record_ctx(stub_dir)

        with sim_capture("rate") as cap1:
            cap1.set_result(0.08)

        with sim_capture("rate") as cap2:
            cap2.set_result(0.10)

        # Both should exist on disk with different ordinals
        path0 = stub_dir / "__capture__" / "rate_0.json"
        path1 = stub_dir / "__capture__" / "rate_1.json"
        assert path0.exists()
        assert path1.exists()

        data0 = json.loads(path0.read_text())
        data1 = json.loads(path1.read_text())
        assert data0["ordinal"] == 0
        assert data1["ordinal"] == 1
        assert data0["result"] == 0.08
        assert data1["result"] == 0.10

    def test_different_labels_start_at_zero(self, stub_dir):
        """Different labels each start at ordinal 0."""
        make_record_ctx(stub_dir)

        with sim_capture("alpha") as cap1:
            cap1.set_result("a")

        with sim_capture("beta") as cap2:
            cap2.set_result("b")

        path_a = stub_dir / "__capture__" / "alpha_0.json"
        path_b = stub_dir / "__capture__" / "beta_0.json"
        assert path_a.exists()
        assert path_b.exists()

    def test_replay_respects_ordinals(self, stub_dir):
        """Replay returns correct value for each ordinal."""
        # Record two captures with same label
        make_record_ctx(stub_dir)
        with sim_capture("rate") as cap:
            cap.set_result("first")
        with sim_capture("rate") as cap:
            cap.set_result("second")

        # Replay both
        make_replay_ctx(stub_dir)
        with sim_capture("rate") as cap1:
            val1 = cap1.result
        with sim_capture("rate") as cap2:
            val2 = cap2.result

        assert val1 == "first"
        assert val2 == "second"


# ===========================================================================
# 6. Sync Context Manager
# ===========================================================================

class TestSyncContextManager:
    """sim_capture works as a sync context manager: `with sim_capture(...) as cap:`"""

    def test_enter_returns_capture_handle(self, stub_dir):
        """__enter__ returns a CaptureHandle instance."""
        make_record_ctx(stub_dir)
        with sim_capture("test") as cap:
            assert isinstance(cap, CaptureHandle)

    def test_replaying_false_in_record(self, stub_dir):
        """cap.replaying is False in record mode."""
        make_record_ctx(stub_dir)
        with sim_capture("test") as cap:
            assert cap.replaying is False

    def test_full_sync_workflow(self, stub_dir):
        """Complete sync record/replay workflow."""
        # Record
        make_record_ctx(stub_dir)
        with sim_capture("price") as cap:
            price = 19.99
            cap.set_result(price)

        # Replay
        make_replay_ctx(stub_dir)
        with sim_capture("price") as cap:
            if not cap.replaying:
                pytest.fail("Should be replaying")
            assert cap.result == 19.99


# ===========================================================================
# 7. Async Context Manager
# ===========================================================================

class TestAsyncContextManager:
    """sim_capture works as an async context manager: `async with sim_capture(...) as cap:`"""

    @pytest.mark.asyncio
    async def test_async_enter_returns_capture_handle(self, stub_dir):
        """__aenter__ returns a CaptureHandle instance."""
        make_record_ctx(stub_dir)
        async with sim_capture("async_test") as cap:
            assert isinstance(cap, CaptureHandle)

    @pytest.mark.asyncio
    async def test_async_record_writes_fixture(self, stub_dir):
        """Async record mode writes fixture to disk."""
        make_record_ctx(stub_dir)
        async with sim_capture("async_svc") as cap:
            cap.set_result({"async": True})

        expected = stub_dir / "__capture__" / "async_svc_0.json"
        assert expected.exists()

    @pytest.mark.asyncio
    async def test_async_replay_returns_recorded(self, stub_dir):
        """Async replay returns recorded value."""
        # Record
        make_record_ctx(stub_dir)
        async with sim_capture("async_rate") as cap:
            cap.set_result(0.075)

        # Replay
        make_replay_ctx(stub_dir)
        async with sim_capture("async_rate") as cap:
            assert cap.replaying is True
            assert cap.result == 0.075

    @pytest.mark.asyncio
    async def test_async_full_workflow(self, stub_dir):
        """Complete async record/replay workflow."""
        make_record_ctx(stub_dir)
        async with sim_capture("api_call") as cap:
            result = {"status": 200, "body": "OK"}
            cap.set_result(result)

        make_replay_ctx(stub_dir)
        async with sim_capture("api_call") as cap:
            if not cap.replaying:
                pytest.fail("Should be replaying")
            assert cap.result == {"status": 200, "body": "OK"}


# ===========================================================================
# 8. Zero Framework Dependencies
# ===========================================================================

class TestZeroDependencies:
    """capture.py must not import any web framework, HTTP lib, or DB driver."""

    def test_no_framework_imports(self):
        """Verify capture.py source has no forbidden imports."""
        source = inspect.getsource(__import__("sim_sdk.capture", fromlist=["capture"]))

        forbidden = [
            "flask", "django", "fastapi", "starlette",
            "requests", "httpx", "aiohttp", "urllib3",
            "sqlalchemy", "psycopg", "pymysql", "sqlite3",
            "boto3", "grpc",
        ]
        for lib in forbidden:
            assert f"import {lib}" not in source, f"capture.py imports forbidden library: {lib}"
            assert f"from {lib}" not in source, f"capture.py imports forbidden library: {lib}"


# ===========================================================================
# 9. Off Mode
# ===========================================================================

class TestOffMode:
    """Off mode: handle is inert, block runs normally."""

    def test_off_mode_handle_is_inert(self):
        """In off mode, CaptureHandle is returned but is effectively a no-op."""
        make_off_ctx()
        with sim_capture("anything") as cap:
            assert isinstance(cap, CaptureHandle)
            assert cap.replaying is False

    def test_off_mode_block_executes(self):
        """Block body runs normally in off mode."""
        make_off_ctx()
        executed = False
        with sim_capture("test") as cap:
            executed = True
        assert executed

    def test_off_mode_set_result_is_noop(self):
        """set_result() can be called but nothing is persisted."""
        make_off_ctx()
        with sim_capture("test") as cap:
            cap.set_result("value")
            assert cap.result == "value"  # Still accessible within block


# ===========================================================================
# 10. Round-Trip
# ===========================================================================

class TestRoundtrip:
    """Record then replay returns identical value."""

    def test_roundtrip_dict(self, stub_dir):
        """Dict value survives record/replay round-trip."""
        original = {"user": "alice", "items": [1, 2, 3], "total": 29.97}

        make_record_ctx(stub_dir)
        with sim_capture("order") as cap:
            cap.set_result(original)

        make_replay_ctx(stub_dir)
        with sim_capture("order") as cap:
            assert cap.result == original

    def test_roundtrip_string(self, stub_dir):
        """String value survives record/replay round-trip."""
        make_record_ctx(stub_dir)
        with sim_capture("greeting") as cap:
            cap.set_result("hello world")

        make_replay_ctx(stub_dir)
        with sim_capture("greeting") as cap:
            assert cap.result == "hello world"

    def test_roundtrip_list(self, stub_dir):
        """List value survives record/replay round-trip."""
        original = [1, "two", 3.0, None, True]

        make_record_ctx(stub_dir)
        with sim_capture("mixed") as cap:
            cap.set_result(original)

        make_replay_ctx(stub_dir)
        with sim_capture("mixed") as cap:
            assert cap.result == original


# ===========================================================================
# Capture key formatting
# ===========================================================================

class TestCaptureKey:
    """Tests for the _capture_key helper."""

    def test_basic_key(self):
        assert _capture_key("tax_rate", 0) == "__capture__/tax_rate_0.json"

    def test_dots_replaced(self):
        assert _capture_key("service.lookup", 0) == "__capture__/service_lookup_0.json"

    def test_slashes_replaced(self):
        assert _capture_key("api/rate", 1) == "__capture__/api_rate_1.json"

    def test_spaces_replaced(self):
        assert _capture_key("my label", 2) == "__capture__/my_label_2.json"


# ===========================================================================
# Sink integration
# ===========================================================================

class TestSinkIntegration:
    """Record mode uses ctx.sink when available."""

    def test_record_emits_to_sink(self, stub_dir):
        """When ctx.sink is set, capture emits a FixtureEvent to the buffer."""
        stub_dir.mkdir(parents=True, exist_ok=True)
        mock_sink = MagicMock()
        ctx = SimContext(
            mode=SimMode.RECORD, run_id="test", stub_dir=stub_dir, sink=mock_sink,
        )
        set_context(ctx)

        with sim_capture("via_sink") as cap:
            cap.set_result({"routed": True})

        # Sink.emit() should have been called (not write())
        mock_sink.emit.assert_called_once()
        event = mock_sink.emit.call_args[0][0]
        assert event.storage_key == "__capture__/via_sink_0.json"
        assert event.qualname == "capture:via_sink"
        assert event.output == {"routed": True}
        assert event.ordinal == 0
