"""
Integration test for SIM_SAMPLE_RATE over 100+ requests.

Exit criterion: SIM_SAMPLE_RATE=0.5 results in approximately 50% capture
(verified over 100+ requests).
"""

import os
import random
from unittest import mock

import pytest

from sim_sdk.context import SimContext, SimMode, set_context, clear_context
from sim_sdk.fixture.schema import FixtureEvent
from sim_sdk.trace import sim_trace


class CollectSink:
    """In-memory sink for counting emitted events."""

    def __init__(self):
        self.events: list = []

    def emit(self, event: FixtureEvent) -> None:
        self.events.append(event)


@pytest.fixture(autouse=True)
def clean_context():
    clear_context()
    yield
    clear_context()


class TestSamplingIntegration:
    def test_rate_1_captures_all(self):
        """SIM_SAMPLE_RATE=1.0 captures 100% of requests."""
        sink = CollectSink()

        @sim_trace
        def handler(i):
            return i

        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "1.0"}):
            for i in range(100):
                clear_context()
                ctx = SimContext(mode=SimMode.RECORD, run_id=f"run-{i}", sink=sink)
                set_context(ctx)
                handler(i)

        assert len(sink.events) == 100

    def test_rate_0_captures_none(self):
        """SIM_SAMPLE_RATE=0.0 captures 0% of requests."""
        sink = CollectSink()

        @sim_trace
        def handler(i):
            return i

        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0"}):
            for i in range(100):
                clear_context()
                ctx = SimContext(mode=SimMode.RECORD, run_id=f"run-{i}", sink=sink)
                set_context(ctx)
                handler(i)

        assert len(sink.events) == 0

    def test_rate_half_approximately_50_percent(self):
        """SIM_SAMPLE_RATE=0.5 captures ~50% over 200 requests."""
        random.seed(12345)  # Deterministic
        sink = CollectSink()

        @sim_trace
        def handler(i):
            return i

        n_requests = 200

        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0.5"}):
            for i in range(n_requests):
                clear_context()
                ctx = SimContext(mode=SimMode.RECORD, run_id=f"run-{i}", sink=sink)
                set_context(ctx)
                handler(i)

        capture_rate = len(sink.events) / n_requests
        assert 0.35 <= capture_rate <= 0.65, (
            f"Expected ~50% capture rate, got {capture_rate * 100:.1f}% "
            f"({len(sink.events)}/{n_requests})"
        )

    def test_default_rate_captures_all(self):
        """When SIM_SAMPLE_RATE is not set, all requests are captured."""
        sink = CollectSink()

        @sim_trace
        def handler(i):
            return i

        # Ensure SIM_SAMPLE_RATE is NOT in the environment
        env = os.environ.copy()
        env.pop("SIM_SAMPLE_RATE", None)
        with mock.patch.dict(os.environ, env, clear=True):
            for i in range(50):
                clear_context()
                ctx = SimContext(mode=SimMode.RECORD, run_id=f"run-{i}", sink=sink)
                set_context(ctx)
                handler(i)

        assert len(sink.events) == 50

    def test_function_still_executes_when_sampled_out(self):
        """Even when sampled out, the function body runs and returns correctly."""
        results = []

        @sim_trace
        def handler(i):
            results.append(i)
            return i * 2

        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0"}):
            for i in range(10):
                clear_context()
                ctx = SimContext(mode=SimMode.RECORD, run_id=f"run-{i}", sink=CollectSink())
                set_context(ctx)
                val = handler(i)
                assert val == i * 2

        assert results == list(range(10))
