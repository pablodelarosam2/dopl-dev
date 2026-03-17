"""
Tests for StubStore — the in-memory replay data layer.

All tests against real recorded data load from the canonical fixture file:
  tests/fixtures/calculate_quote.json  (schema_version=1, FixtureEvent format)
"""

import json
from pathlib import Path

import pytest

from sim_sdk.stub_store import StubStore

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "calculate_quote.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fixture(tmp_path, name: str, data: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Main suite — canonical calculate_quote fixture
# ---------------------------------------------------------------------------

class TestStubStoreCalculateQuote:
    """Validate StubStore against the committed calculate_quote.json fixture."""

    @pytest.fixture()
    def store(self) -> StubStore:
        return StubStore.from_fixture(str(FIXTURE_PATH))

    # -- DB stubs -----------------------------------------------------------

    def test_products_returns_correct_rows(self, store):
        rows = store.get_db_stub("2c2c6f4f7f54aad7:fd5c81b9314f2dba", 0)
        assert rows == [
            {"sku": "GADGET-B", "name": "Super Gadget", "price": 49.99},
            {"sku": "WIDGET-A", "name": "Premium Widget", "price": 29.99},
        ]

    def test_users_returns_correct_rows(self, store):
        rows = store.get_db_stub("8e2da4922ec2cc96:080a9ed428559ef6", 0)
        assert rows == [{"region": "US-CA"}]

    def test_quotes_returns_write_result(self, store):
        result = store.get_db_stub("023d3a8283aba4ca:c7317460ab436af4", 0)
        assert result == {"rowcount": 1, "lastrowid": 1}

    def test_db_miss_wrong_fingerprint_returns_none(self, store):
        assert store.get_db_stub("deadbeefdeadbeef:0000000000000000", 0) is None

    def test_db_miss_wrong_ordinal_returns_none(self, store):
        assert store.get_db_stub("2c2c6f4f7f54aad7:fd5c81b9314f2dba", 1) is None

    # -- HTTP / capture stubs -----------------------------------------------

    def test_tax_service_returns_capture_result(self, store):
        status, body, headers = store.get_http_stub("tax_service", 0)
        assert status == 200
        assert body == {"region": "US-CA", "rate": 0.0725}
        assert headers == {}

    def test_http_miss_returns_none(self, store):
        assert store.get_http_stub("nonexistent_service", 0) is None

    # -- Trace stubs --------------------------------------------------------

    def test_trace_miss_fixture_has_no_trace_stubs(self, store):
        """Fixture contains only db: and capture: stubs — trace index is empty."""
        assert store.get_trace_stub("any_fp", 0) is None

    # -- golden_output is NOT indexed ---------------------------------------

    def test_golden_output_not_indexed_as_stub(self, store):
        """golden_output has event_type=Output; StubStore must not index it."""
        golden_fp = "77646bc6050bcb8b1021261c093e131af2238521d4a4c975aaad0a979970837c"
        assert store.get_trace_stub(golden_fp, 0) is None


# ---------------------------------------------------------------------------
# Ordinal tracking — same fingerprint called multiple times
# ---------------------------------------------------------------------------

class TestStubStoreOrdinalTracking:
    """Repeated calls with the same fingerprint are distinguished by ordinal."""

    @pytest.fixture()
    def store(self, tmp_path) -> StubStore:
        data = {
            "schema_version": 1,
            "stubs": [
                {
                    "qualname": "db:products",
                    "input_fingerprint": "aabbccdd:11223344",
                    "output": [{"row": 0}],
                    "ordinal": 0,
                    "event_type": "Stub",
                },
                {
                    "qualname": "db:products",
                    "input_fingerprint": "aabbccdd:11223344",
                    "output": [{"row": 1}],
                    "ordinal": 1,
                    "event_type": "Stub",
                },
            ],
        }
        return StubStore.from_fixture(_write_fixture(tmp_path, "ordinals.json", data))

    def test_ordinal_0_returns_first_result(self, store):
        assert store.get_db_stub("aabbccdd:11223344", 0) == [{"row": 0}]

    def test_ordinal_1_returns_second_result(self, store):
        assert store.get_db_stub("aabbccdd:11223344", 1) == [{"row": 1}]

    def test_ordinal_2_returns_none(self, store):
        assert store.get_db_stub("aabbccdd:11223344", 2) is None


# ---------------------------------------------------------------------------
# from_fixture error handling
# ---------------------------------------------------------------------------

class TestStubStoreFromFixtureErrors:

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Fixture not found"):
            StubStore.from_fixture(str(tmp_path / "does_not_exist.json"))

    def test_invalid_json_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            StubStore.from_fixture(str(bad))

    def test_empty_stubs_array_loads_cleanly(self, tmp_path):
        path = _write_fixture(tmp_path, "empty.json", {"schema_version": 1, "stubs": []})
        store = StubStore.from_fixture(path)
        assert store.get_db_stub("any", 0) is None

    def test_missing_stubs_key_loads_cleanly(self, tmp_path):
        path = _write_fixture(tmp_path, "no_stubs.json", {"schema_version": 1, "fixture_id": "abc"})
        store = StubStore.from_fixture(path)
        assert store.get_http_stub("any", 0) is None


# ---------------------------------------------------------------------------
# available_* fingerprint inspection
# ---------------------------------------------------------------------------

class TestAvailableFingerprints:

    @pytest.fixture()
    def store(self, tmp_path) -> StubStore:
        data = {
            "schema_version": 1,
            "stubs": [
                {
                    "qualname": "db:orders",
                    "input_fingerprint": "db_fp_a:db_fp_b",
                    "output": [{"id": 1}],
                    "ordinal": 0,
                    "event_type": "Stub",
                },
                {
                    "qualname": "db:orders",
                    "input_fingerprint": "db_fp_a:db_fp_b",
                    "output": [{"id": 2}],
                    "ordinal": 1,
                    "event_type": "Stub",
                },
                {
                    "qualname": "capture:payment_service",
                    "input_fingerprint": "ignored",
                    "output": {"charged": True},
                    "status": 200,
                    "headers": {},
                    "ordinal": 0,
                    "event_type": "Stub",
                },
                {
                    "qualname": "capture:auth_service",
                    "input_fingerprint": "ignored",
                    "output": {"token": "abc"},
                    "status": 200,
                    "headers": {},
                    "ordinal": 0,
                    "event_type": "Stub",
                },
                {
                    "qualname": "some_traced_func",
                    "input_fingerprint": "trace_fp_x",
                    "output": {"result": 42},
                    "ordinal": 0,
                    "event_type": "Stub",
                },
            ],
        }
        return StubStore.from_fixture(_write_fixture(tmp_path, "multi.json", data))

    def test_available_db_fingerprints_returns_unique(self, store):
        fps = store.available_db_fingerprints()
        assert fps == ["db_fp_a:db_fp_b"]

    def test_available_http_fingerprints_returns_all_labels(self, store):
        labels = store.available_http_fingerprints()
        assert set(labels) == {"payment_service", "auth_service"}

    def test_available_trace_fingerprints_returns_fp(self, store):
        fps = store.available_trace_fingerprints()
        assert fps == ["trace_fp_x"]

    def test_available_db_empty_when_no_db_stubs(self, tmp_path):
        data = {"schema_version": 1, "stubs": []}
        store = StubStore.from_fixture(_write_fixture(tmp_path, "empty.json", data))
        assert store.available_db_fingerprints() == []

    def test_available_http_empty_when_no_http_stubs(self, tmp_path):
        data = {"schema_version": 1, "stubs": []}
        store = StubStore.from_fixture(_write_fixture(tmp_path, "empty2.json", data))
        assert store.available_http_fingerprints() == []

    def test_available_trace_empty_when_no_trace_stubs(self, tmp_path):
        data = {"schema_version": 1, "stubs": []}
        store = StubStore.from_fixture(_write_fixture(tmp_path, "empty3.json", data))
        assert store.available_trace_fingerprints() == []

    def test_available_db_fingerprints_from_real_fixture(self):
        store = StubStore.from_fixture(str(FIXTURE_PATH))
        fps = store.available_db_fingerprints()
        assert "2c2c6f4f7f54aad7:fd5c81b9314f2dba" in fps
        assert "8e2da4922ec2cc96:080a9ed428559ef6" in fps
        assert "023d3a8283aba4ca:c7317460ab436af4" in fps

    def test_available_http_fingerprints_from_real_fixture(self):
        store = StubStore.from_fixture(str(FIXTURE_PATH))
        assert "tax_service" in store.available_http_fingerprints()
