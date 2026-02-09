"""Tests for sim_sdk.clock module."""

import os
from datetime import datetime, timezone

import pytest

from sim_sdk.clock import SimClock, freeze, now, sim_clock, timestamp, unfreeze, utcnow


class TestSimClock:
    """Tests for SimClock class."""

    def test_unfrozen_returns_current_time(self):
        clock = SimClock()
        before = datetime.now()
        clock_time = clock.now()
        after = datetime.now()

        assert before <= clock_time <= after

    def test_frozen_returns_fixed_time(self):
        frozen_time = datetime(2024, 6, 15, 12, 30, 0)
        clock = SimClock(frozen_time=frozen_time)

        assert clock.now() == frozen_time

        # Multiple calls return same time
        import time
        time.sleep(0.01)
        assert clock.now() == frozen_time

    def test_freeze_and_unfreeze(self):
        clock = SimClock()

        # Initially unfrozen
        assert not clock.is_frozen

        # Freeze
        frozen_time = datetime(2024, 1, 1, 0, 0, 0)
        clock.freeze(frozen_time)

        assert clock.is_frozen
        assert clock.frozen_time == frozen_time
        assert clock.now() == frozen_time

        # Unfreeze
        clock.unfreeze()

        assert not clock.is_frozen
        assert clock.frozen_time is None

    def test_now_with_timezone(self):
        frozen_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        clock = SimClock(frozen_time=frozen_time)

        result = clock.now(tz=timezone.utc)
        assert result.tzinfo is not None
        assert result == frozen_time

    def test_utcnow(self):
        frozen_time = datetime(2024, 6, 15, 12, 0, 0)
        clock = SimClock(frozen_time=frozen_time)

        result = clock.utcnow()
        assert result == frozen_time

    def test_timestamp(self):
        frozen_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        clock = SimClock(frozen_time=frozen_time)

        ts = clock.timestamp()
        # 2024-01-01 00:00:00 UTC
        assert ts == 1704067200.0

    def test_context_manager(self):
        clock = SimClock()
        frozen_time = datetime(2024, 6, 15, 12, 0, 0)

        with clock:
            clock.freeze(frozen_time)
            assert clock.is_frozen

        # Should be unfrozen after context
        assert not clock.is_frozen


class TestGlobalClock:
    """Tests for global sim_clock instance and convenience functions."""

    def test_global_clock_exists(self):
        assert sim_clock is not None

    def test_now_function(self):
        sim_clock.unfreeze()
        before = datetime.now()
        result = now()
        after = datetime.now()

        assert before <= result <= after

    def test_utcnow_function(self):
        sim_clock.unfreeze()
        result = utcnow()
        assert isinstance(result, datetime)

    def test_timestamp_function(self):
        sim_clock.unfreeze()
        result = timestamp()
        assert isinstance(result, float)

    def test_freeze_unfreeze_functions(self):
        frozen_time = datetime(2024, 3, 20, 10, 0, 0)

        freeze(frozen_time)
        assert sim_clock.is_frozen
        assert now() == frozen_time

        unfreeze()
        assert not sim_clock.is_frozen


class TestClockFromEnv:
    """Tests for clock initialization from environment."""

    def test_frozen_time_from_env_iso(self):
        os.environ["SIM_FROZEN_TIME"] = "2024-06-15T12:00:00"

        # Force recreation
        from sim_sdk import clock
        new_clock = clock._create_clock_from_env()

        assert new_clock.is_frozen
        assert new_clock.frozen_time == datetime(2024, 6, 15, 12, 0, 0)

    def test_frozen_time_from_env_timestamp(self):
        os.environ["SIM_FROZEN_TIME"] = "1704067200"  # 2024-01-01 00:00:00 UTC

        from sim_sdk import clock
        new_clock = clock._create_clock_from_env()

        assert new_clock.is_frozen

    def test_replay_mode_default_frozen_time(self):
        os.environ["SIM_MODE"] = "replay"
        os.environ.pop("SIM_FROZEN_TIME", None)

        from sim_sdk import clock
        new_clock = clock._create_clock_from_env()

        # Should have default frozen time for replay mode
        assert new_clock.is_frozen
        assert new_clock.frozen_time == datetime(2024, 1, 1, 12, 0, 0)

    def test_off_mode_no_frozen_time(self):
        os.environ["SIM_MODE"] = "off"
        os.environ.pop("SIM_FROZEN_TIME", None)

        from sim_sdk import clock
        new_clock = clock._create_clock_from_env()

        assert not new_clock.is_frozen
