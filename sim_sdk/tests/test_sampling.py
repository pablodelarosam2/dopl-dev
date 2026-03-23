"""Tests for SIM_SAMPLE_RATE sampling gate."""

import os
from unittest import mock

import pytest

from sim_sdk.sampling import should_record, get_sample_rate


class TestGetSampleRate:
    def test_default_is_1(self):
        """Default sample rate is 1.0 (100% capture)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            # Remove SIM_SAMPLE_RATE if present
            os.environ.pop("SIM_SAMPLE_RATE", None)
            rate = get_sample_rate()
        assert rate == 1.0

    def test_reads_env_var(self):
        """Reads SIM_SAMPLE_RATE from environment."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0.5"}):
            rate = get_sample_rate()
        assert rate == 0.5

    def test_zero_rate(self):
        """SIM_SAMPLE_RATE=0 means capture nothing."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0"}):
            rate = get_sample_rate()
        assert rate == 0.0

    def test_invalid_value_defaults_to_1(self):
        """Invalid SIM_SAMPLE_RATE falls back to 1.0."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "not-a-number"}):
            rate = get_sample_rate()
        assert rate == 1.0

    def test_negative_clamps_to_0(self):
        """Negative values are clamped to 0.0."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "-0.5"}):
            rate = get_sample_rate()
        assert rate == 0.0

    def test_above_1_clamps_to_1(self):
        """Values above 1.0 are clamped to 1.0."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "2.5"}):
            rate = get_sample_rate()
        assert rate == 1.0


class TestShouldRecord:
    def test_rate_1_always_records(self):
        """At rate 1.0, should_record() always returns True."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "1.0"}):
            results = [should_record() for _ in range(100)]
        assert all(results)

    def test_rate_0_never_records(self):
        """At rate 0.0, should_record() always returns False."""
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0"}):
            results = [should_record() for _ in range(100)]
        assert not any(results)

    def test_rate_half_approximately_50_percent(self):
        """At rate 0.5, approximately 50% of calls return True."""
        import random
        random.seed(42)  # Deterministic for testing
        with mock.patch.dict(os.environ, {"SIM_SAMPLE_RATE": "0.5"}):
            results = [should_record() for _ in range(1000)]
        hit_rate = sum(results) / len(results)
        assert 0.40 <= hit_rate <= 0.60, f"Expected ~50%, got {hit_rate * 100:.1f}%"


class TestZeroDependencies:
    def test_no_banned_imports(self):
        """sampling.py must not import banned modules."""
        from pathlib import Path
        import sim_sdk.sampling as mod
        source = Path(mod.__file__).read_text()
        banned = [
            "flask", "django", "fastapi", "requests", "httpx",
            "aiohttp", "psycopg2", "sqlalchemy",
        ]
        for module in banned:
            assert f"import {module}" not in source
            assert f"from {module}" not in source
