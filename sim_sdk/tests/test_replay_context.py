"""
Tests for ReplayContext — per-request replay lifecycle manager.

Covers:
  - Construction from a real fixture file (calculate_quote.json)
  - Stub store is loaded and accessible
  - Ordinal counters increment independently per type (db / http / trace)
  - Counters reset between requests (new ReplayContext per request)
  - Context manager sets and clears the ContextVar
  - get_replay_context() returns None when no context is active
  - Explicit set_replay_context / clear_replay_context helpers
  - Error propagation: missing file, invalid JSON
"""

import json
from pathlib import Path

import pytest

from sim_sdk.replay_context import (
    ReplayContext,
    get_replay_context,
    set_replay_context,
    clear_replay_context,
)
from sim_sdk.errors import SimStubMissError

FIXTURE_DIR = str(Path(__file__).parent / "fixtures")
FIXTURE_ID = "calculate_quote"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fixture(tmp_path: Path, name: str, data: dict) -> tuple[str, str]:
    """Write a fixture JSON and return (fixture_id, fixture_dir) pair."""
    fixture_id = name.replace(".json", "")
    p = tmp_path / f"{fixture_id}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return fixture_id, str(tmp_path)


_MINIMAL_FIXTURE = {
    "schema_version": 1,
    "stubs": [
        {
            "qualname": "db:users",
            "input_fingerprint": "aabb:1122",
            "output": [{"id": 1}],
            "ordinal": 0,
            "event_type": "Stub",
        },
        {
            "qualname": "db:users",
            "input_fingerprint": "aabb:1122",
            "output": [{"id": 2}],
            "ordinal": 1,
            "event_type": "Stub",
        },
        {
            "qualname": "capture:tax_service",
            "input_fingerprint": "ccdd:3344",
            "output": {"rate": 0.1},
            "status": 200,
            "headers": {},
            "ordinal": 0,
            "event_type": "Stub",
        },
    ],
}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestReplayContextConstruction:

    def test_loads_from_real_fixture(self):
        ctx = ReplayContext(fixture_id=FIXTURE_ID, fixture_dir=FIXTURE_DIR)
        assert ctx.fixture_id == FIXTURE_ID
        assert ctx.stub_store is not None

    def test_stub_store_serves_db_stub(self):
        ctx = ReplayContext(fixture_id=FIXTURE_ID, fixture_dir=FIXTURE_DIR)
        rows = ctx.stub_store.get_db_stub("2c2c6f4f7f54aad7:fd5c81b9314f2dba", 0)
        assert rows == [
            {"sku": "GADGET-B", "name": "Super Gadget", "price": 49.99},
            {"sku": "WIDGET-A", "name": "Premium Widget", "price": 29.99},
        ]

    def test_stub_store_serves_http_stub(self):
        ctx = ReplayContext(fixture_id=FIXTURE_ID, fixture_dir=FIXTURE_DIR)
        status, body, headers = ctx.stub_store.get_http_stub("tax_service", 0)
        assert status == 200
        assert body == {"region": "US-CA", "rate": 0.0725}

    def test_missing_fixture_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Fixture not found"):
            ReplayContext(fixture_id="nonexistent", fixture_dir=str(tmp_path))

    def test_invalid_json_raises_value_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            ReplayContext(fixture_id="bad", fixture_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Ordinal counters
# ---------------------------------------------------------------------------

class TestOrdinalCounters:

    @pytest.fixture()
    def ctx(self, tmp_path) -> ReplayContext:
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        return ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

    def test_db_ordinal_starts_at_zero(self, ctx):
        assert ctx.next_db_ordinal("aabb:1122") == 0

    def test_db_ordinal_increments_on_second_call(self, ctx):
        ctx.next_db_ordinal("aabb:1122")
        assert ctx.next_db_ordinal("aabb:1122") == 1

    def test_http_ordinal_starts_at_zero(self, ctx):
        assert ctx.next_http_ordinal("tax_service") == 0

    def test_http_ordinal_increments_on_second_call(self, ctx):
        ctx.next_http_ordinal("tax_service")
        assert ctx.next_http_ordinal("tax_service") == 1

    def test_trace_ordinal_starts_at_zero(self, ctx):
        assert ctx.next_trace_ordinal("some_fp") == 0

    def test_trace_ordinal_increments_on_second_call(self, ctx):
        ctx.next_trace_ordinal("some_fp")
        assert ctx.next_trace_ordinal("some_fp") == 1

    def test_db_and_http_counters_are_independent(self, ctx):
        """Incrementing the DB counter must not affect the HTTP counter."""
        ctx.next_db_ordinal("aabb:1122")
        ctx.next_db_ordinal("aabb:1122")
        assert ctx.next_http_ordinal("tax_service") == 0

    def test_db_and_trace_counters_are_independent(self, ctx):
        ctx.next_db_ordinal("aabb:1122")
        assert ctx.next_trace_ordinal("aabb:1122") == 0

    def test_different_fingerprints_have_independent_counters(self, ctx):
        ctx.next_db_ordinal("fp_a")
        assert ctx.next_db_ordinal("fp_b") == 0

    def test_ordinal_sequence_matches_stub_store_lookup(self, ctx):
        """End-to-end: ordinals from next_db_ordinal feed correct stubs."""
        ordinal_0 = ctx.next_db_ordinal("aabb:1122")
        rows_0 = ctx.stub_store.get_db_stub("aabb:1122", ordinal_0)
        assert rows_0 == [{"id": 1}]

        ordinal_1 = ctx.next_db_ordinal("aabb:1122")
        rows_1 = ctx.stub_store.get_db_stub("aabb:1122", ordinal_1)
        assert rows_1 == [{"id": 2}]


# ---------------------------------------------------------------------------
# Counter isolation between requests (new context per request)
# ---------------------------------------------------------------------------

class TestOrdinalResetBetweenRequests:

    def test_new_context_resets_db_ordinals(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)

        ctx1 = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)
        ctx1.next_db_ordinal("aabb:1122")
        ctx1.next_db_ordinal("aabb:1122")

        ctx2 = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)
        assert ctx2.next_db_ordinal("aabb:1122") == 0

    def test_new_context_resets_http_ordinals(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)

        ctx1 = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)
        ctx1.next_http_ordinal("tax_service")

        ctx2 = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)
        assert ctx2.next_http_ordinal("tax_service") == 0


# ---------------------------------------------------------------------------
# ContextVar — get / set / clear
# ---------------------------------------------------------------------------

class TestContextVar:

    def test_get_returns_none_outside_context(self):
        assert get_replay_context() is None

    def test_context_manager_sets_context(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        ctx = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

        with ctx:
            active = get_replay_context()
            assert active is ctx

    def test_context_manager_clears_context_on_exit(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        ctx = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

        with ctx:
            pass

        assert get_replay_context() is None

    def test_context_manager_clears_on_exception(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        ctx = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

        try:
            with ctx:
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert get_replay_context() is None

    def test_context_manager_returns_self(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        ctx = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

        with ctx as bound:
            assert bound is ctx

    def test_explicit_set_and_clear(self, tmp_path):
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        ctx = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

        token = set_replay_context(ctx)
        assert get_replay_context() is ctx

        clear_replay_context(token)
        assert get_replay_context() is None

    def test_nested_contexts_restore_outer(self, tmp_path):
        """Inner context manager must restore outer context on exit."""
        fixture_id, fixture_dir = _write_fixture(tmp_path, "minimal", _MINIMAL_FIXTURE)
        outer = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)
        inner = ReplayContext(fixture_id=fixture_id, fixture_dir=fixture_dir)

        with outer:
            assert get_replay_context() is outer
            with inner:
                assert get_replay_context() is inner
            assert get_replay_context() is outer

        assert get_replay_context() is None
