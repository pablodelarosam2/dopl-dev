"""Tests for sim_sdk.sink module."""

import json
import time
from pathlib import Path

import pytest

from sim_sdk.sink import LocalSink, SinkConfig, init_sink, get_default_sink, set_default_sink
from sim_sdk.trace import FixtureEvent


@pytest.fixture
def sink_config(temp_dir):
    """Create a SinkConfig with temp directory."""
    return SinkConfig(
        output_dir=temp_dir,
        service_name="test-service",
        endpoint_name="test-endpoint",
        flush_interval_ms=50,  # Fast flush for testing
        max_events=100,
    )


@pytest.fixture
def local_sink(sink_config):
    """Create a LocalSink for testing."""
    sink = LocalSink(sink_config)
    yield sink
    sink.close()


@pytest.fixture
def sample_event():
    """Create a sample FixtureEvent for testing."""
    return FixtureEvent(
        fixture_id="test-fixture-001",
        name="test_function",
        run_id="test-run-001",
        recorded_at="2024-01-01T00:00:00Z",
        recording_mode="explicit",
        input={"x": 1, "y": 2},
        input_fingerprint="abc123",
        output={"result": 3},
        output_fingerprint="def456",
        db_stubs=[{"fingerprint": "db1", "ordinal": 0, "rows": [{"id": 1}]}],
        http_stubs=[{"fingerprint": "http1", "status": 200, "body": {}}],
        duration_ms=5.5,
    )


class TestLocalSink:
    """Tests for LocalSink class."""

    def test_emit_is_non_blocking(self, local_sink, sample_event):
        """emit() should return immediately without blocking."""
        start = time.time()
        local_sink.emit(sample_event)
        elapsed = time.time() - start

        # Should complete in < 10ms (non-blocking)
        assert elapsed < 0.01

    def test_flush_writes_to_disk(self, local_sink, sample_event, sink_config):
        """flush() should write events to disk."""
        local_sink.emit(sample_event)
        local_sink.flush()

        # Check fixture was written
        fixture_dir = (
            sink_config.output_dir
            / sink_config.service_name
            / sink_config.endpoint_name
            / sample_event.fixture_id
        )
        assert fixture_dir.exists()

        # Check all files exist
        assert (fixture_dir / "input.json").exists()
        assert (fixture_dir / "golden_output.json").exists()
        assert (fixture_dir / "stubs.json").exists()
        assert (fixture_dir / "metadata.json").exists()

    def test_file_contents_correct(self, local_sink, sample_event, sink_config):
        """Written files should contain correct data."""
        local_sink.emit(sample_event)
        local_sink.flush()

        fixture_dir = (
            sink_config.output_dir
            / sink_config.service_name
            / sink_config.endpoint_name
            / sample_event.fixture_id
        )

        # Check input.json
        with open(fixture_dir / "input.json") as f:
            input_data = json.load(f)
        assert input_data["args"] == {"x": 1, "y": 2}

        # Check golden_output.json
        with open(fixture_dir / "golden_output.json") as f:
            output_data = json.load(f)
        assert output_data["output"] == {"result": 3}

        # Check stubs.json
        with open(fixture_dir / "stubs.json") as f:
            stubs_data = json.load(f)
        assert len(stubs_data["db_calls"]) == 1
        assert len(stubs_data["http_calls"]) == 1

    def test_multiple_events(self, local_sink, sink_config):
        """Should handle multiple events."""
        events = [
            FixtureEvent(
                fixture_id=f"fixture-{i}",
                name="test",
                run_id="run",
                recorded_at="2024-01-01T00:00:00Z",
                recording_mode="explicit",
                input={"i": i},
                output={"result": i * 2},
            )
            for i in range(5)
        ]

        for event in events:
            local_sink.emit(event)

        local_sink.flush()

        # Check all fixtures exist
        endpoint_dir = (
            sink_config.output_dir
            / sink_config.service_name
            / sink_config.endpoint_name
        )
        fixture_dirs = list(endpoint_dir.iterdir())
        assert len(fixture_dirs) == 5

    def test_pending_count(self, local_sink, sample_event):
        """pending_count should track buffered events."""
        assert local_sink.pending_count == 0

        local_sink.emit(sample_event)
        assert local_sink.pending_count == 1

        local_sink.flush()
        assert local_sink.pending_count == 0

    def test_buffer_overflow_drops_oldest(self, temp_dir):
        """When buffer is full, oldest events should be dropped."""
        config = SinkConfig(
            output_dir=temp_dir,
            max_events=3,  # Small buffer
            flush_interval_ms=10000,  # Don't auto-flush
        )
        sink = LocalSink(config)

        try:
            # Emit more events than buffer can hold
            for i in range(5):
                sink.emit(FixtureEvent(
                    fixture_id=f"fixture-{i}",
                    name="test",
                    run_id="run",
                    recorded_at="2024-01-01T00:00:00Z",
                    recording_mode="explicit",
                ))

            # Should have dropped oldest 2
            assert sink.dropped_count == 2
            assert sink.pending_count == 3
        finally:
            sink.close()


class TestGlobalSink:
    """Tests for global sink management."""

    def test_init_sink(self, temp_dir):
        """init_sink should create and set default sink."""
        # Clear any existing sink
        set_default_sink(None)

        sink = init_sink(
            output_dir=temp_dir,
            service_name="my-service",
        )

        assert sink is not None
        assert get_default_sink() is sink

        sink.close()
        set_default_sink(None)

    def test_set_default_sink(self, local_sink):
        """set_default_sink should update the global sink."""
        set_default_sink(None)
        assert get_default_sink() is None

        set_default_sink(local_sink)
        assert get_default_sink() is local_sink

        set_default_sink(None)


class TestAutoFlush:
    """Tests for automatic background flushing."""

    def test_auto_flush_on_interval(self, temp_dir):
        """Events should be flushed automatically after interval."""
        config = SinkConfig(
            output_dir=temp_dir,
            service_name="test",
            endpoint_name="test",
            flush_interval_ms=100,  # 100ms flush interval
        )
        sink = LocalSink(config)

        try:
            event = FixtureEvent(
                fixture_id="auto-flush-test",
                name="test",
                run_id="run",
                recorded_at="2024-01-01T00:00:00Z",
                recording_mode="explicit",
            )
            sink.emit(event)

            # Wait for auto-flush
            time.sleep(0.3)

            # Check fixture was written
            fixture_dir = temp_dir / "test" / "test" / "auto-flush-test"
            assert fixture_dir.exists()
        finally:
            sink.close()
