"""Tests for sim_sdk.trace module."""

import pytest
from pathlib import Path

from sim_sdk.context import SimContext, SimMode, set_context, clear_context
from sim_sdk.trace import (
    sim_trace,
    FixtureEvent,
    add_db_stub,
    add_http_stub,
    _reset_stub_collectors,
    _get_collected_stubs,
)


class TestSimTrace:
    """Tests for @sim_trace decorator."""

    def test_off_mode_passthrough(self, sim_context_off):
        """In OFF mode, function should execute normally without capture."""
        @sim_trace
        def add(a, b):
            return a + b

        result = add(2, 3)
        assert result == 5

    def test_record_mode_captures_input_output(self, sim_context_record):
        """In RECORD mode, should capture input and output."""
        results = []

        @sim_trace
        def multiply(x, y):
            return x * y

        result = multiply(4, 5)
        assert result == 20

    def test_captures_function_arguments(self, sim_context_record):
        """Should capture all function arguments."""
        @sim_trace
        def process(name, value, flag=False):
            return f"{name}:{value}:{flag}"

        result = process("test", 42, flag=True)
        assert result == "test:42:True"

    def test_handles_exceptions(self, sim_context_record):
        """Should capture error and re-raise exception."""
        @sim_trace
        def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_func()

    def test_custom_name(self, sim_context_record):
        """Should use custom name if provided."""
        @sim_trace(name="custom_operation")
        def my_func():
            return "result"

        result = my_func()
        assert result == "result"

    def test_nested_functions(self, sim_context_record):
        """Should handle nested traced functions."""
        @sim_trace
        def outer(x):
            return inner(x) + 1

        @sim_trace
        def inner(x):
            return x * 2

        result = outer(5)
        assert result == 11


class TestFixtureEvent:
    """Tests for FixtureEvent dataclass."""

    def test_to_dict(self):
        event = FixtureEvent(
            fixture_id="test123",
            name="test_func",
            run_id="run456",
            recorded_at="2024-01-01T00:00:00Z",
            recording_mode="explicit",
            input={"x": 1},
            output={"result": 2},
        )

        data = event.to_dict()

        assert data["fixture_id"] == "test123"
        assert data["name"] == "test_func"
        assert data["run_id"] == "run456"
        assert data["input"] == {"x": 1}
        assert data["output"] == {"result": 2}

    def test_to_fixture_files(self):
        event = FixtureEvent(
            fixture_id="test123",
            name="test_func",
            run_id="run456",
            recorded_at="2024-01-01T00:00:00Z",
            recording_mode="explicit",
            input={"x": 1},
            input_fingerprint="abc123",
            output={"result": 2},
            output_fingerprint="def456",
            db_stubs=[{"fingerprint": "db1", "rows": []}],
            http_stubs=[{"fingerprint": "http1", "status": 200}],
            duration_ms=5.5,
        )

        files = event.to_fixture_files()

        # Check input file
        assert files["input"]["fixture_id"] == "test123"
        assert files["input"]["args"] == {"x": 1}

        # Check golden_output file
        assert files["golden_output"]["output"] == {"result": 2}

        # Check stubs file
        assert len(files["stubs"]["db_calls"]) == 1
        assert len(files["stubs"]["http_calls"]) == 1

        # Check metadata file
        assert files["metadata"]["name"] == "test_func"
        assert files["metadata"]["schema_version"] == "1.0"


class TestStubCollectors:
    """Tests for stub collection functions."""

    def test_add_and_get_db_stubs(self):
        _reset_stub_collectors()

        add_db_stub({"fingerprint": "db1", "rows": [{"id": 1}]})
        add_db_stub({"fingerprint": "db2", "rows": [{"id": 2}]})

        db_stubs, http_stubs = _get_collected_stubs()

        assert len(db_stubs) == 2
        assert len(http_stubs) == 0
        assert db_stubs[0]["fingerprint"] == "db1"
        assert db_stubs[1]["fingerprint"] == "db2"

    def test_add_and_get_http_stubs(self):
        _reset_stub_collectors()

        add_http_stub({"fingerprint": "http1", "status": 200})
        add_http_stub({"fingerprint": "http2", "status": 201})

        db_stubs, http_stubs = _get_collected_stubs()

        assert len(db_stubs) == 0
        assert len(http_stubs) == 2

    def test_reset_clears_stubs(self):
        _reset_stub_collectors()

        add_db_stub({"fingerprint": "db1"})
        add_http_stub({"fingerprint": "http1"})

        _reset_stub_collectors()

        db_stubs, http_stubs = _get_collected_stubs()

        assert len(db_stubs) == 0
        assert len(http_stubs) == 0

    def test_get_clears_stubs(self):
        _reset_stub_collectors()

        add_db_stub({"fingerprint": "db1"})

        # First get should return stubs
        db_stubs1, _ = _get_collected_stubs()
        assert len(db_stubs1) == 1

        # Second get should return empty (collectors were reset)
        db_stubs2, _ = _get_collected_stubs()
        assert len(db_stubs2) == 0
