"""Pytest fixtures for sim_sdk tests."""

import os
import tempfile
from pathlib import Path

import pytest

from sim_sdk.context import SimContext, SimMode, clear_context, set_context
from sim_sdk.store import StubStore


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def stub_store(temp_dir):
    """Create a StubStore with a temporary directory."""
    return StubStore(temp_dir)


@pytest.fixture
def sim_context_off():
    """Create a SimContext in OFF mode."""
    ctx = SimContext(mode=SimMode.OFF)
    set_context(ctx)
    yield ctx
    clear_context()


@pytest.fixture
def sim_context_record(temp_dir):
    """Create a SimContext in RECORD mode."""
    ctx = SimContext(
        mode=SimMode.RECORD,
        run_id="test-run-123",
        stub_dir=temp_dir,
    )
    set_context(ctx)
    yield ctx
    clear_context()


@pytest.fixture
def sim_context_replay(temp_dir):
    """Create a SimContext in REPLAY mode."""
    ctx = SimContext(
        mode=SimMode.REPLAY,
        run_id="test-run-123",
        stub_dir=temp_dir,
    )
    set_context(ctx)
    yield ctx
    clear_context()


@pytest.fixture(autouse=True)
def reset_env():
    """Reset environment variables after each test."""
    original_env = os.environ.copy()
    yield
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)
    clear_context()
